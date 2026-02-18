from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
import zxingcpp

from .models import DecodeAttempt
from .quality import QualityMetrics, measure_quality, measure_quality_from_path


@dataclass
class CandidateImage:
    image: np.ndarray
    transform: str
    rotation: int


@dataclass
class DecodeOutcome:
    success: bool
    decoded_text: Optional[str]
    barcode_format: Optional[str]
    corrected_image_base64: Optional[str]
    attempts: List[DecodeAttempt]


TARGET_QUALITY_IMAGE = Path.home() / "Downloads" / "workingscan.jpg"
QUALITY_MODE = "strict"  # strict | balanced


def _rotate(img: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img.copy()


def _resize_for_decode(img: np.ndarray, min_width: int = 1700, max_width: int = 2800) -> np.ndarray:
    h, w = img.shape[:2]
    if w < min_width:
        scale = min_width / float(w)
        nw, nh = int(w * scale), int(h * scale)
        return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC)
    if w > max_width:
        scale = max_width / float(w)
        nw, nh = int(w * scale), int(h * scale)
        return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return img


def _clahe_gray(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _otsu(gray: np.ndarray) -> np.ndarray:
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def _upscale(img: np.ndarray, scale: float) -> np.ndarray:
    h, w = img.shape[:2]
    nw, nh = int(w * scale), int(h * scale)
    pixels = nw * nh
    if pixels > 5_000_000:
        cap = (5_000_000 / float(pixels)) ** 0.5
        nw = max(64, int(nw * cap))
        nh = max(64, int(nh * cap))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC)


def _barcode_strips(img: np.ndarray) -> List[np.ndarray]:
    h, w = img.shape[:2]
    strips: List[np.ndarray] = []

    # Right-side vertical bands (most common after trying rotations).
    for x0f, x1f in ((0.55, 1.0), (0.62, 1.0), (0.7, 1.0)):
        x0 = int(w * x0f)
        x1 = int(w * x1f)
        for y0f, y1f in ((0.05, 0.97), (0.12, 0.92)):
            y0 = int(h * y0f)
            y1 = int(h * y1f)
            if x1 - x0 >= 120 and y1 - y0 >= 120:
                strips.append(img[y0:y1, x0:x1].copy())

    # Horizontal top bands for states with top barcode orientation.
    for y0f, y1f in ((0.03, 0.28), (0.06, 0.22)):
        y0 = int(h * y0f)
        y1 = int(h * y1f)
        x0, x1 = int(w * 0.06), int(w * 0.94)
        if x1 - x0 >= 200 and y1 - y0 >= 70:
            strips.append(img[y0:y1, x0:x1].copy())

    return strips


def _center_crop(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x0, x1 = int(w * 0.08), int(w * 0.92)
    y0, y1 = int(h * 0.08), int(h * 0.92)
    return img[y0:y1, x0:x1].copy()


def _prioritize_candidates(
    candidates: List[CandidateImage], preferred_transform_hint: str
) -> List[CandidateImage]:
    hint = (preferred_transform_hint or "").strip().lower()
    if not hint:
        return candidates

    preferred: List[CandidateImage] = []
    other: List[CandidateImage] = []
    for candidate in candidates:
        if candidate.transform.lower().startswith(hint):
            preferred.append(candidate)
        else:
            other.append(candidate)
    return preferred + other


def _build_candidates(
    img: np.ndarray, decode_profile: str = "fast", preferred_transform_hint: str = ""
) -> List[CandidateImage]:
    candidates: List[CandidateImage] = []
    profile = (decode_profile or "fast").lower()

    for rotation in (0, 90, 180, 270):
        rotated = _rotate(img, rotation)
        base = _resize_for_decode(rotated)
        candidates.append(CandidateImage(base, "base", rotation))
        if profile == "extended":
            candidates.append(CandidateImage(_center_crop(base), "center_crop", rotation))
        gray = _clahe_gray(base)
        candidates.append(CandidateImage(gray, "base_clahe", rotation))
        if profile == "extended":
            candidates.append(CandidateImage(_otsu(gray), "base_otsu", rotation))

        strip_cap = 2 if profile == "extended" else 1
        for strip in _barcode_strips(base)[:strip_cap]:
            candidates.append(CandidateImage(strip, "strip", rotation))
            candidates.append(CandidateImage(_upscale(strip, 2.0), "strip_up2", rotation))
            if profile == "extended":
                candidates.append(CandidateImage(_upscale(strip, 3.0), "strip_up3", rotation))
            strip_gray = _clahe_gray(strip)
            candidates.append(CandidateImage(strip_gray, "strip_clahe", rotation))
            candidates.append(CandidateImage(_otsu(strip_gray), "strip_otsu", rotation))

    bounded = candidates[:120] if profile == "extended" else candidates[:48]
    return _prioritize_candidates(bounded, preferred_transform_hint)


def _to_base64_png(img: np.ndarray) -> Optional[str]:
    ok, encoded = cv2.imencode(".png", img)
    if not ok:
        return None
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _decode_with_zxing(
    img: np.ndarray, decode_profile: str = "fast"
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    profile = (decode_profile or "fast").lower()
    variants = [
        ("zxing_localavg", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.HRI, False),
        ("zxing_localavg_nodown", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.HRI, False),
        ("zxing_globalhist", zxingcpp.Binarizer.GlobalHistogram, zxingcpp.TextMode.HRI, False),
    ]
    if profile == "extended":
        variants.append(("zxing_localavg_down", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.HRI, True))
        variants.append(("zxing_plain", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.Plain, False))

    for engine_name, binarizer, text_mode, try_downscale in variants:
        try:
            results = zxingcpp.read_barcodes(
                img,
                formats=zxingcpp.BarcodeFormat.PDF417,
                try_rotate=True,
                try_downscale=try_downscale,
                text_mode=text_mode,
                binarizer=binarizer,
            )
        except Exception:
            continue

        if not results:
            continue
        result = results[0]
        text = (result.text or "").strip()
        if text:
            return text, str(result.format), engine_name

    return None, None, None


def _is_likely_aamva_payload(text: str) -> bool:
    upper = text.upper()
    if "ANSI " in upper:
        return True
    markers = ("DAQ", "DCS", "DAC", "DBB", "DAJ")
    return sum(1 for m in markers if m in upper) >= 3


@lru_cache(maxsize=1)
def _target_quality() -> Optional[QualityMetrics]:
    return measure_quality_from_path(TARGET_QUALITY_IMAGE)


def _passes_quality_gate(metrics: QualityMetrics, target: Optional[QualityMetrics], quality_mode: str) -> bool:
    if target is None:
        return True
    mode = (quality_mode or QUALITY_MODE).lower()
    if mode == "strict":
        sharp_ok = metrics.sharpness >= max(100.0, target.sharpness * 0.92)
        edge_ok = metrics.edge_signal >= max(16.0, target.edge_signal * 0.82)
        glare_ok = metrics.glare_ratio <= min(0.09, max(0.025, target.glare_ratio * 5.5))
        dark_ok = metrics.dark_ratio <= min(0.26, max(0.08, target.dark_ratio * 1.25))
        mean_ok = (target.mean - 52.0) <= metrics.mean <= (target.mean + 52.0)
    else:
        sharp_ok = metrics.sharpness >= max(85.0, target.sharpness * 0.82)
        edge_ok = metrics.edge_signal >= max(12.0, target.edge_signal * 0.68)
        glare_ok = metrics.glare_ratio <= min(0.12, max(0.03, target.glare_ratio * 7.0))
        dark_ok = metrics.dark_ratio <= min(0.30, max(0.10, target.dark_ratio * 1.5))
        mean_ok = (target.mean - 70.0) <= metrics.mean <= (target.mean + 70.0)
    return sharp_ok and edge_ok and glare_ok and dark_ok and mean_ok


def _decode_pdf417_from_image(
    img: np.ndarray,
    quality_mode: str = "strict",
    decode_profile: str = "fast",
    preferred_transform_hint: str = "",
) -> DecodeOutcome:
    if img is None:
        return DecodeOutcome(False, None, None, None, [])

    q = measure_quality(img)
    if not _passes_quality_gate(q, _target_quality(), quality_mode=quality_mode):
        return DecodeOutcome(
            success=False,
            decoded_text=None,
            barcode_format=None,
            corrected_image_base64=None,
            attempts=[
                DecodeAttempt(
                    attempt=1,
                    transform="quality_gate:rejected",
                    rotation=0,
                    success=False,
                )
            ],
        )

    candidates = _build_candidates(
        img,
        decode_profile=decode_profile,
        preferred_transform_hint=preferred_transform_hint,
    )
    attempts: List[DecodeAttempt] = []
    best_preview = None

    for idx, candidate in enumerate(candidates, start=1):
        text, barcode_format, engine = _decode_with_zxing(candidate.image, decode_profile=decode_profile)
        success = text is not None
        attempts.append(
            DecodeAttempt(
                attempt=idx,
                transform=f"{candidate.transform}:{engine or 'none'}",
                rotation=candidate.rotation,
                success=success,
            )
        )

        if best_preview is None:
            best_preview = _to_base64_png(candidate.image)

        if success and _is_likely_aamva_payload(text):
            return DecodeOutcome(
                success=True,
                decoded_text=text,
                barcode_format=barcode_format,
                corrected_image_base64=_to_base64_png(candidate.image),
                attempts=attempts,
            )

    return DecodeOutcome(
        success=False,
        decoded_text=None,
        barcode_format=None,
        corrected_image_base64=best_preview,
        attempts=attempts,
    )


def decode_pdf417_from_bytes(
    image_bytes: bytes,
    quality_mode: str = "strict",
    decode_profile: str = "fast",
    preferred_transform_hint: str = "",
) -> DecodeOutcome:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return _decode_pdf417_from_image(
        img,
        quality_mode=quality_mode,
        decode_profile=decode_profile,
        preferred_transform_hint=preferred_transform_hint,
    )


def _fuse_burst_images(image_bytes_list: list[bytes], max_frames: int = 5) -> Optional[np.ndarray]:
    decoded: list[np.ndarray] = []
    for blob in image_bytes_list:
        nparr = np.frombuffer(blob, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            decoded.append(img)
    if len(decoded) < 2:
        return decoded[0] if decoded else None

    # Use sharpest frame as reference to reduce blur in alignment target.
    scored = sorted(decoded, key=lambda im: measure_quality(im).sharpness, reverse=True)
    ref = scored[0]
    h, w = ref.shape[:2]
    ref_gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)

    aligned: list[np.ndarray] = [ref]
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        40,
        1e-5,
    )
    for img in scored[1:max_frames]:
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            _, warp = cv2.findTransformECC(
                ref_gray,
                gray,
                warp,
                cv2.MOTION_EUCLIDEAN,
                criteria,
                None,
                5,
            )
            moved = cv2.warpAffine(
                img,
                warp,
                (w, h),
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )
            aligned.append(moved)
        except cv2.error:
            continue

    if not aligned:
        return None
    stack = np.stack(aligned, axis=0).astype(np.float32)
    fused = np.median(stack, axis=0).astype(np.uint8)
    return fused


def decode_pdf417_from_burst(
    image_bytes_list: list[bytes],
    quality_mode: str = "balanced",
    decode_profile: str = "extended",
    preferred_transform_hint: str = "",
) -> DecodeOutcome:
    fused = _fuse_burst_images(image_bytes_list)
    if fused is None:
        return DecodeOutcome(False, None, None, None, [])
    return _decode_pdf417_from_image(
        fused,
        quality_mode=quality_mode,
        decode_profile=decode_profile,
        preferred_transform_hint=preferred_transform_hint,
    )
