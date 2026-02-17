from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np
import zxingcpp

try:
    from pyzbar.pyzbar import ZBarSymbol, decode as pyzbar_decode

    PYZBAR_AVAILABLE = True
except Exception:
    PYZBAR_AVAILABLE = False

from .models import DecodeAttempt


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


def _rotate(img: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if rotation == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img.copy()


def _resize_if_small(img: np.ndarray, min_width: int = 1600, max_width: int = 2200) -> np.ndarray:
    h, w = img.shape[:2]
    if w >= min_width:
        return img
    scale = min(max_width / float(w), min_width / float(w))
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def _resize_if_tiny_or_huge(
    img: np.ndarray, min_width: int = 1800, max_width: int = 3200, downscale_width: int = 2600
) -> np.ndarray:
    h, w = img.shape[:2]
    if w < min_width:
        scale = min(max_width / float(w), min_width / float(w))
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    # Keep full detail for large originals; downscaling can erase tiny PDF417 modules.
    return img


def _clahe_gray(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sharpen(img: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return cv2.filter2D(img, -1, kernel)


def _upscale(img: np.ndarray, scale: float = 2.0) -> np.ndarray:
    h, w = img.shape[:2]
    new_w = int(w * scale)
    new_h = int(h * scale)
    max_pixels = 5_000_000
    pixels = new_w * new_h
    if pixels > max_pixels:
        cap = (max_pixels / float(pixels)) ** 0.5
        new_w = max(32, int(new_w * cap))
        new_h = max(32, int(new_h * cap))
    try:
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    except cv2.error:
        # On memory pressure, keep original candidate rather than aborting the whole decode pass.
        return img


def _unsharp_mask(img: np.ndarray, sigma: float = 1.2, amount: float = 1.6) -> np.ndarray:
    if len(img.shape) == 2:
        blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma)
        return cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)


def _deblur_enhance(img: np.ndarray) -> np.ndarray:
    den = _denoise(img)
    return _unsharp_mask(den, sigma=1.0, amount=2.2)


def _motion_psf(length: int, angle_deg: float) -> np.ndarray:
    k = np.zeros((length, length), dtype=np.float32)
    k[length // 2, :] = 1.0
    center = (length / 2.0 - 0.5, length / 2.0 - 0.5)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    k = cv2.warpAffine(k, matrix, (length, length))
    s = float(k.sum())
    return k / s if s > 0 else k


def _rl_deconv_gray(gray: np.ndarray, psf: np.ndarray, iterations: int = 10) -> np.ndarray:
    image = gray.astype(np.float32) / 255.0
    estimate = np.full_like(image, 0.5, dtype=np.float32)
    psf_flip = cv2.flip(psf, -1)
    eps = 1e-6
    for _ in range(iterations):
        conv = cv2.filter2D(estimate, -1, psf, borderType=cv2.BORDER_REPLICATE)
        relative = image / (conv + eps)
        estimate *= cv2.filter2D(relative, -1, psf_flip, borderType=cv2.BORDER_REPLICATE)
        estimate = np.clip(estimate, 0.0, 1.0)
    return (estimate * 255.0).astype(np.uint8)


def _wiener_deconv_gray(gray: np.ndarray, psf: np.ndarray, k: float = 0.01) -> np.ndarray:
    h, w = gray.shape[:2]
    psf_pad = np.zeros((h, w), dtype=np.float32)
    ph, pw = psf.shape[:2]
    psf_pad[:ph, :pw] = psf
    psf_pad = np.roll(psf_pad, -ph // 2, axis=0)
    psf_pad = np.roll(psf_pad, -pw // 2, axis=1)

    g = np.fft.fft2(gray.astype(np.float32) / 255.0)
    h_fft = np.fft.fft2(psf_pad)
    h_conj = np.conj(h_fft)
    f_hat = (h_conj / (h_fft * h_conj + k)) * g
    out = np.real(np.fft.ifft2(f_hat))
    out = np.clip(out, 0.0, 1.0)
    return (out * 255.0).astype(np.uint8)


def _retinex_gray(gray: np.ndarray, sigma: float = 35.0) -> np.ndarray:
    g = gray.astype(np.float32) + 1.0
    blur = cv2.GaussianBlur(g, (0, 0), sigmaX=sigma, sigmaY=sigma)
    ret = np.log(g) - np.log(blur + 1e-6)
    ret = cv2.normalize(ret, None, 0, 255, cv2.NORM_MINMAX)
    return ret.astype(np.uint8)


def _threshold_otsu(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def _threshold_adaptive(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        5,
    )


def _invert(img: np.ndarray) -> np.ndarray:
    return cv2.bitwise_not(img)


def _denoise(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 3:
        return cv2.bilateralFilter(img, d=7, sigmaColor=50, sigmaSpace=50)
    return cv2.fastNlMeansDenoising(img, None, 10, 7, 21)


def _order_points(points: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = points.sum(axis=1)
    rect[0] = points[np.argmin(s)]
    rect[2] = points[np.argmax(s)]
    diff = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diff)]
    rect[3] = points[np.argmax(diff)]
    return rect


def _perspective_crop(img: np.ndarray) -> Optional[np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    h, w = img.shape[:2]
    min_area = (h * w) * 0.03
    best = None

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            best = approx.reshape(4, 2)
            break

    if best is None:
        return None

    rect = _order_points(best)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = int(max(width_a, width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = int(max(height_a, height_b))

    if max_width < 250 or max_height < 80:
        return None

    dst = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img, matrix, (max_width, max_height))
    return warped


def _card_perspective_crops(img: np.ndarray) -> List[np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    h, w = img.shape[:2]
    min_area = (h * w) * 0.01
    out: List[np.ndarray] = []

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:24]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        rect = cv2.minAreaRect(contour)
        (rw, rh) = rect[1]
        if rw <= 1 or rh <= 1:
            continue
        ratio = max(rw, rh) / max(1.0, min(rw, rh))
        if ratio < 1.25 or ratio > 2.2:
            continue

        box = cv2.boxPoints(rect).astype("float32")
        ordered = _order_points(box)
        tl, tr, br, bl = ordered
        dst_w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        dst_h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        if dst_w < 180 or dst_h < 100:
            continue

        if dst_h > dst_w:
            dst_w, dst_h = dst_h, dst_w
            ordered = np.array([bl, tl, tr, br], dtype="float32")

        dst = np.array(
            [[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(ordered, dst)
        warped = cv2.warpPerspective(img, matrix, (dst_w, dst_h))
        out.append(warped)

    return out[:6]


def _card_barcode_strips(card: np.ndarray) -> List[np.ndarray]:
    h, w = card.shape[:2]
    strips: List[np.ndarray] = []
    x_ranges = [(0.56, 0.98), (0.60, 0.985), (0.64, 0.99), (0.68, 0.995)]
    y_ranges = [(0.08, 0.95), (0.12, 0.92)]
    for x0f, x1f in x_ranges:
        x0 = int(w * x0f)
        x1 = int(w * x1f)
        for y0f, y1f in y_ranges:
            y0 = int(h * y0f)
            y1 = int(h * y1f)
            if x1 - x0 >= 80 and y1 - y0 >= 110:
                strips.append(card[y0:y1, x0:x1].copy())

    # Some IDs place PDF417 as a long horizontal band near the top.
    tx_ranges = [(0.05, 0.95), (0.08, 0.92), (0.12, 0.88)]
    ty_ranges = [(0.02, 0.28), (0.04, 0.24), (0.06, 0.20)]
    for x0f, x1f in tx_ranges:
        x0 = int(w * x0f)
        x1 = int(w * x1f)
        for y0f, y1f in ty_ranges:
            y0 = int(h * y0f)
            y1 = int(h * y1f)
            if x1 - x0 >= 140 and y1 - y0 >= 60:
                strips.append(card[y0:y1, x0:x1].copy())

    return strips


def _edge_strip_crops(img: np.ndarray) -> List[np.ndarray]:
    h, w = img.shape[:2]
    out: List[np.ndarray] = []
    # Vertical-oriented bands.
    for x0f, x1f in [(0.0, 0.38), (0.62, 1.0), (0.20, 0.80), (0.30, 0.90)]:
        x0 = int(w * x0f)
        x1 = int(w * x1f)
        for y0f, y1f in [(0.02, 0.98), (0.10, 0.92)]:
            y0 = int(h * y0f)
            y1 = int(h * y1f)
            if x1 - x0 >= 90 and y1 - y0 >= 140:
                out.append(img[y0:y1, x0:x1].copy())
    # Horizontal-oriented bands.
    for y0f, y1f in [(0.0, 0.34), (0.66, 1.0), (0.18, 0.58), (0.24, 0.50)]:
        y0 = int(h * y0f)
        y1 = int(h * y1f)
        for x0f, x1f in [(0.02, 0.98), (0.10, 0.92)]:
            x0 = int(w * x0f)
            x1 = int(w * x1f)
            if x1 - x0 >= 140 and y1 - y0 >= 60:
                out.append(img[y0:y1, x0:x1].copy())
    return out


def _dense_barcode_bands(img: np.ndarray) -> List[np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_x = cv2.convertScaleAbs(grad_x)
    grad_x = cv2.GaussianBlur(grad_x, (5, 5), 0)
    row_energy = grad_x.sum(axis=1).astype(np.float64)
    if row_energy.size == 0:
        return []

    out: List[np.ndarray] = []
    for frac in (0.12, 0.16, 0.2):
        win = max(40, int(h * frac))
        if win >= h:
            continue
        window_sum = np.convolve(row_energy, np.ones(win, dtype=np.float64), mode="valid")
        top_idx = np.argsort(window_sum)[-3:][::-1]
        for y0 in top_idx:
            y0i = int(max(0, y0))
            y1i = int(min(h, y0 + win))
            band = img[y0i:y1i, :].copy()
            if band.shape[0] < 30 or band.shape[1] < 120:
                continue
            out.append(band)

    # Deduplicate roughly by shape and first row hash-like sum.
    dedup: List[np.ndarray] = []
    seen: set[tuple[int, int, int]] = set()
    for b in out:
        key = (b.shape[0], b.shape[1], int(b[0].sum()) % 997 if b.shape[0] > 0 else 0)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(b)
    return dedup[:8]


def _barcode_roi_crops(img: np.ndarray) -> List[np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img.shape[:2]
    out: List[np.ndarray] = []

    # Pass 1: Sobel-X based map, good for crisp barcode bars.
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_x = cv2.convertScaleAbs(grad_x)
    grad_x = cv2.GaussianBlur(grad_x, (7, 7), 0)
    _, thresh_1 = cv2.threshold(grad_x, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel_1 = cv2.getStructuringElement(cv2.MORPH_RECT, (27, 9))
    map_1 = cv2.morphologyEx(thresh_1, cv2.MORPH_CLOSE, kernel_1, iterations=2)

    # Pass 2: Blackhat + Scharr, more tolerant of blur/low contrast barcodes.
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (31, 7)))
    scharr = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1)
    scharr = cv2.convertScaleAbs(scharr)
    scharr = cv2.GaussianBlur(scharr, (5, 5), 0)
    _, thresh_2 = cv2.threshold(scharr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel_2 = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 7))
    map_2 = cv2.morphologyEx(thresh_2, cv2.MORPH_CLOSE, kernel_2, iterations=2)

    for morphed in (map_1, map_2):
        morphed = cv2.erode(morphed, None, iterations=1)
        morphed = cv2.dilate(morphed, None, iterations=2)
        contours, _ = cv2.findContours(morphed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            area = cv2.contourArea(contour)
            if area < (h * w * 0.004):
                continue

            rect = cv2.minAreaRect(contour)
            (rw, rh) = rect[1]
            if rw <= 1 or rh <= 1:
                continue
            aspect = max(rw, rh) / max(1.0, min(rw, rh))
            if aspect < 1.05:
                continue

            box = cv2.boxPoints(rect).astype("float32")
            ordered = _order_points(box)
            tl, tr, br, bl = ordered
            dst_w = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            dst_h = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if dst_w < 120 or dst_h < 45:
                continue

            dst = np.array(
                [[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]],
                dtype="float32",
            )
            matrix = cv2.getPerspectiveTransform(ordered, dst)
            warped = cv2.warpPerspective(img, matrix, (dst_w, dst_h))
            out.append(warped)

    return out


def _strip_crops(img: np.ndarray) -> List[np.ndarray]:
    h, w = img.shape[:2]
    crops: List[np.ndarray] = []

    # Because we try all 4 rotations, right-side strip crops are effective for most PDF417 placements.
    x_ranges = [(0.50, 1.0), (0.58, 1.0), (0.66, 1.0), (0.72, 1.0), (0.78, 1.0)]
    y_ranges = [(0.04, 0.98), (0.10, 0.95), (0.18, 0.90)]
    for x0f, x1f in x_ranges:
        x0 = int(w * x0f)
        x1 = int(w * x1f)
        for y0f, y1f in y_ranges:
            y0 = int(h * y0f)
            y1 = int(h * y1f)
            if x1 - x0 >= 100 and y1 - y0 >= 120:
                crops.append(img[y0:y1, x0:x1].copy())

    return crops


def _to_base64_png(img: np.ndarray) -> Optional[str]:
    ok, encoded = cv2.imencode(".png", img)
    if not ok:
        return None
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _downscale_for_fast_pass(img: np.ndarray, target_width: int = 1800) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= target_width:
        return img
    scale = target_width / float(w)
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _decode_with_zxing(img: np.ndarray) -> tuple[Optional[str], Optional[str], Optional[str]]:
    variants = [
        ("zxing_localavg", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.HRI, True),
        ("zxing_localavg_nodown", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.HRI, False),
        ("zxing_globalhist", zxingcpp.Binarizer.GlobalHistogram, zxingcpp.TextMode.HRI, True),
        ("zxing_globalhist_nodown", zxingcpp.Binarizer.GlobalHistogram, zxingcpp.TextMode.HRI, False),
        ("zxing_fixed", zxingcpp.Binarizer.FixedThreshold, zxingcpp.TextMode.HRI, True),
        ("zxing_plain", zxingcpp.Binarizer.LocalAverage, zxingcpp.TextMode.Plain, False),
    ]

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


def _decode_with_pyzbar(img: np.ndarray) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not PYZBAR_AVAILABLE:
        return None, None, None

    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        results = pyzbar_decode(gray, symbols=[ZBarSymbol.PDF417])
    except Exception:
        return None, None, None

    if not results:
        return None, None, None

    payload = results[0].data.decode("utf-8", errors="replace").strip()
    if not payload:
        return None, None, None
    return payload, "PDF_417", "pyzbar"


def _decode_candidate(
    img: np.ndarray, allow_pyzbar: bool = True
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    text, barcode_format, engine = _decode_with_zxing(img)
    if text:
        return text, barcode_format, engine
    if not allow_pyzbar:
        return None, None, None
    return _decode_with_pyzbar(img)


def _is_likely_aamva_payload(text: str) -> bool:
    upper = text.upper()
    if "ANSI " in upper or "@\n" in text:
        return True
    required_markers = ["DCS", "DAC", "DBB", "DAQ", "DAG", "DAJ", "DAK"]
    marker_hits = sum(1 for marker in required_markers if marker in upper)
    return marker_hits >= 3


def _candidate_priority_key(candidate: CandidateImage) -> int:
    transform_priority = {
        "card_crop": 0,
        "card_crop_up2": 1,
        "card_strip": 1,
        "card_strip_up2": 2,
        "card_strip_up3": 3,
        "card_strip_otsu": 4,
        "edge_strip": 4,
        "edge_strip_up3": 5,
        "edge_strip_up4": 6,
        "edge_strip_otsu": 7,
        "edge_strip_adaptive": 8,
        "edge_strip_rl": 9,
        "dense_band": 2,
        "dense_band_up6": 3,
        "dense_band_up8": 4,
        "dense_band_up10": 5,
        "dense_band_unsharp": 6,
        "dense_band_otsu": 7,
        "dense_band_rl": 8,
        "dense_band_wiener": 9,
        "dense_band_retinex": 10,
        "roi_crop": 0,
        "roi_crop_up2": 11,
        "roi_crop_up3": 12,
        "roi_crop_otsu": 13,
        "roi_crop_adaptive": 14,
        "roi_crop_invert": 15,
        "roi_crop_up2_otsu": 16,
        "roi_crop_up3_otsu": 17,
        "strip_crop": 18,
        "strip_crop_up2": 19,
        "strip_crop_up3": 20,
        "strip_crop_otsu": 21,
        "perspective_crop": 0,
        "perspective_crop_otsu": 22,
        "base": 23,
        "denoise": 24,
        "clahe_gray": 25,
        "sharpen": 26,
        "unsharp": 27,
        "deblur_enhance": 28,
        "otsu": 29,
        "adaptive_threshold": 30,
        "otsu_invert": 31,
        "adaptive_invert": 32,
    }
    return transform_priority.get(candidate.transform, 20)


def _build_candidates(
    img: np.ndarray, include_extreme: bool = False, max_candidates: Optional[int] = None
) -> List[CandidateImage]:
    candidates: List[CandidateImage] = []

    def add_candidate(image: np.ndarray, transform: str, rotation: int) -> bool:
        if max_candidates is not None and len(candidates) >= max_candidates:
            return False
        candidates.append(CandidateImage(image=image, transform=transform, rotation=rotation))
        return True

    for rotation in (0, 90, 180, 270):
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
        rotated = _rotate(img, rotation)
        resized = _resize_if_tiny_or_huge(_resize_if_small(rotated))

        if not add_candidate(resized, "base", rotation):
            break

        cropped = _perspective_crop(resized)
        if cropped is not None:
            if not add_candidate(cropped, "perspective_crop", rotation):
                break

        for card in _card_perspective_crops(resized):
            if max_candidates is not None and len(candidates) >= max_candidates:
                break
            if not add_candidate(card, "card_crop", rotation):
                break
            if not add_candidate(_upscale(card, 2.0), "card_crop_up2", rotation):
                break
            for strip in _card_barcode_strips(card):
                if max_candidates is not None and len(candidates) >= max_candidates:
                    break
                if not add_candidate(strip, "card_strip", rotation):
                    break
                if not add_candidate(_upscale(strip, 2.0), "card_strip_up2", rotation):
                    break
                if not add_candidate(
                    _threshold_otsu(_clahe_gray(strip)),
                    "card_strip_otsu",
                    rotation,
                ):
                    break

        for roi in _barcode_roi_crops(resized):
            if max_candidates is not None and len(candidates) >= max_candidates:
                break
            if not add_candidate(roi, "roi_crop", rotation):
                break
            if not add_candidate(_upscale(roi, 2.0), "roi_crop_up2", rotation):
                break
            roi_clahe = _clahe_gray(roi)
            roi_otsu = _threshold_otsu(roi_clahe)
            if not add_candidate(roi_otsu, "roi_crop_otsu", rotation):
                break

        for strip in _strip_crops(resized):
            if max_candidates is not None and len(candidates) >= max_candidates:
                break
            if not add_candidate(strip, "strip_crop", rotation):
                break
            if not add_candidate(_upscale(strip, 2.0), "strip_crop_up2", rotation):
                break
        for strip in _edge_strip_crops(resized):
            if max_candidates is not None and len(candidates) >= max_candidates:
                break
            if not add_candidate(strip, "edge_strip", rotation):
                break
            if not add_candidate(_upscale(strip, 3.0), "edge_strip_up3", rotation):
                break
            if not add_candidate(
                _threshold_otsu(_clahe_gray(strip)),
                "edge_strip_otsu",
                rotation,
            ):
                break

    return sorted(candidates, key=_candidate_priority_key)


def decode_pdf417_from_bytes(image_bytes: bytes) -> DecodeOutcome:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return DecodeOutcome(
            success=False,
            decoded_text=None,
            barcode_format=None,
            corrected_image_base64=None,
            attempts=[],
        )

    attempts: List[DecodeAttempt] = []
    attempt_idx = 0

    # Stage 1: fast path on downscaled image, tuned for common/easier captures.
    fast_img = _downscale_for_fast_pass(img, target_width=1800)
    fast_candidates = _build_candidates(fast_img, include_extreme=False, max_candidates=320)
    for candidate in fast_candidates:
        attempt_idx += 1
        text, barcode_format, engine = _decode_candidate(candidate.image, allow_pyzbar=False)
        success = text is not None
        attempts.append(
            DecodeAttempt(
                attempt=attempt_idx,
                transform=f"fast/{candidate.transform}:{engine or 'none'}",
                rotation=candidate.rotation,
                success=success,
            )
        )
        if success and _is_likely_aamva_payload(text):
            return DecodeOutcome(
                success=True,
                decoded_text=text,
                barcode_format=barcode_format,
                corrected_image_base64=_to_base64_png(candidate.image),
                attempts=attempts,
            )

    # Stage 2: full normal pass on original image.
    normal_img = _downscale_for_fast_pass(img, target_width=2200)
    normal_candidates = _build_candidates(normal_img, include_extreme=False)
    normal_seen: set[tuple[str, int]] = set()
    for candidate in normal_candidates:
        normal_seen.add((candidate.transform, candidate.rotation))
        attempt_idx += 1
        text, barcode_format, engine = _decode_candidate(candidate.image, allow_pyzbar=True)
        success = text is not None
        attempts.append(
            DecodeAttempt(
                attempt=attempt_idx,
                transform=f"normal/{candidate.transform}:{engine or 'none'}",
                rotation=candidate.rotation,
                success=success,
            )
        )
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
        corrected_image_base64=None,
        attempts=attempts,
    )
