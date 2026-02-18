from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .decoder import decode_pdf417_from_burst, decode_pdf417_from_bytes
from .models import ConfidenceModel, DecodeAttempt, DecodeDebugModel, DecodeResponse, ParsedFields
from .parser import parse_aamva_payload
from .quality import measure_quality_from_path

app = FastAPI(title="PDF417 Phone Scanner", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TARGET_QUALITY_IMAGE = Path.home() / "Downloads" / "workingscan.jpg"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

REUPLOAD_TIPS: List[str] = [
    "Move closer so the PDF417 fills most of the frame.",
    "Hold the phone steady for one full second.",
    "Reduce glare by tilting the card slightly.",
    "Use brighter, even lighting.",
    "Keep the barcode sharp and in focus.",
]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/quality-target")
def quality_target() -> dict:
    measured = measure_quality_from_path(TARGET_QUALITY_IMAGE)
    if measured is None:
        return {
            "available": False,
            "path": str(TARGET_QUALITY_IMAGE),
            "message": "Target quality image not found or unreadable.",
        }
    return {
        "available": True,
        "path": str(TARGET_QUALITY_IMAGE),
        "target": {
            "sharpness": measured.sharpness,
            "edgeSignal": measured.edge_signal,
            "glareRatio": measured.glare_ratio,
            "darkRatio": measured.dark_ratio,
            "mean": measured.mean,
        },
    }


def _error_response(message: str) -> DecodeResponse:
    return DecodeResponse(
        status="error",
        fields=ParsedFields(),
        confidence=ConfidenceModel(decode=0.0, notes=message, attempts=0),
        debug=DecodeDebugModel(decoded_text=None, format=None, attempts=[]),
        tips=REUPLOAD_TIPS,
    )


def _success_response(outcome, attempts_count: int, frame_label: str | None = None) -> DecodeResponse:
    parsed = parse_aamva_payload(outcome.decoded_text or "")
    notes = "Decoded PDF417 and parsed AAMVA fields."
    if frame_label:
        notes = f"Decoded PDF417 from {frame_label} and parsed AAMVA fields."

    format_str = outcome.barcode_format
    if format_str and "PDF417" in format_str.upper():
        format_str = "PDF_417"

    return DecodeResponse(
        status="success",
        fields=parsed.fields,
        confidence=ConfidenceModel(decode=0.98, notes=notes, attempts=attempts_count),
        debug=DecodeDebugModel(
            decoded_text=outcome.decoded_text,
            format=format_str,
            corrected_image_base64=outcome.corrected_image_base64,
            rawFields=parsed.raw_fields,
            attempts=outcome.attempts,
        ),
    )


@app.post("/api/decode", response_model=DecodeResponse)
async def decode_single(image: UploadFile = File(...)) -> DecodeResponse:
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        return _error_response("Unsupported file type. Upload JPG, PNG, or WEBP.")

    data = await image.read()
    if not data:
        return _error_response("Empty file uploaded.")

    outcome = decode_pdf417_from_bytes(data, quality_mode="strict")
    attempts_count = len(outcome.attempts)

    if outcome.success:
        return _success_response(outcome, attempts_count)

    return DecodeResponse(
        status="needs_reupload",
        fields=ParsedFields(),
        confidence=ConfidenceModel(
            decode=0.0,
            notes="Could not decode a PDF417 barcode from this image.",
            attempts=attempts_count,
        ),
        debug=DecodeDebugModel(
            decoded_text=None,
            format=None,
            corrected_image_base64=outcome.corrected_image_base64,
            attempts=outcome.attempts,
        ),
        tips=REUPLOAD_TIPS,
    )


@app.post("/api/decode-burst", response_model=DecodeResponse)
async def decode_burst(
    images: List[UploadFile] = File(...),
    quality_mode: str = Form("strict"),
    decode_profile: str = Form("fast"),
    preferred_transform_hint: str = Form(""),
) -> DecodeResponse:
    if not images:
        return _error_response("No images were uploaded.")

    # Keep burst bounded to avoid huge request processing time.
    burst = images[:12]

    combined_attempts: List[DecodeAttempt] = []
    attempt_offset = 0
    best_corrected = None
    valid_frame_count = 0
    valid_payloads: List[bytes] = []

    for frame_idx, image in enumerate(burst, start=1):
        if image.content_type not in ALLOWED_CONTENT_TYPES:
            continue

        data = await image.read()
        if not data:
            continue
        valid_frame_count += 1
        valid_payloads.append(data)

        outcome = decode_pdf417_from_bytes(
            data,
            quality_mode=quality_mode,
            decode_profile=decode_profile,
            preferred_transform_hint=preferred_transform_hint,
        )
        for item in outcome.attempts:
            combined_attempts.append(
                DecodeAttempt(
                    attempt=item.attempt + attempt_offset,
                    transform=f"frame{frame_idx}/{item.transform}",
                    rotation=item.rotation,
                    success=item.success,
                )
            )

        attempt_offset += len(outcome.attempts)

        if outcome.corrected_image_base64 and best_corrected is None:
            best_corrected = outcome.corrected_image_base64

        if outcome.success:
            outcome.attempts = combined_attempts
            return _success_response(outcome, attempts_count=len(combined_attempts), frame_label=f"frame {frame_idx}")

    return DecodeResponse(
        # Try a bounded fusion fallback only after frame-by-frame attempts fail.
        status="error" if valid_frame_count == 0 else "needs_reupload",
        fields=ParsedFields(),
        confidence=ConfidenceModel(
            decode=0.0,
            notes="No valid images in burst upload." if valid_frame_count == 0 else "Burst decode failed. Improve focus/lighting and retry.",
            attempts=len(combined_attempts),
        ),
        debug=DecodeDebugModel(
            decoded_text=None,
            format=None,
            corrected_image_base64=best_corrected,
            attempts=combined_attempts,
        ),
        tips=REUPLOAD_TIPS,
    ) if valid_frame_count < 3 else _decode_burst_with_fusion(
        valid_payloads=valid_payloads,
        quality_mode=quality_mode,
        preferred_transform_hint=preferred_transform_hint,
        combined_attempts=combined_attempts,
        attempt_offset=attempt_offset,
        best_corrected=best_corrected,
    )


def _decode_burst_with_fusion(
    valid_payloads: List[bytes],
    quality_mode: str,
    preferred_transform_hint: str,
    combined_attempts: List[DecodeAttempt],
    attempt_offset: int,
    best_corrected: str | None,
) -> DecodeResponse:
    fusion_outcome = decode_pdf417_from_burst(
        valid_payloads,
        quality_mode=quality_mode,
        decode_profile="extended",
        preferred_transform_hint=preferred_transform_hint,
    )
    for item in fusion_outcome.attempts:
        combined_attempts.append(
            DecodeAttempt(
                attempt=item.attempt + attempt_offset,
                transform=f"fusion/{item.transform}",
                rotation=item.rotation,
                success=item.success,
            )
        )

    if fusion_outcome.corrected_image_base64 and best_corrected is None:
        best_corrected = fusion_outcome.corrected_image_base64

    if fusion_outcome.success:
        fusion_outcome.attempts = combined_attempts
        return _success_response(
            fusion_outcome,
            attempts_count=len(combined_attempts),
            frame_label="fused burst",
        )

    return DecodeResponse(
        status="needs_reupload",
        fields=ParsedFields(),
        confidence=ConfidenceModel(
            decode=0.0,
            notes="Burst decode failed, including fused fallback.",
            attempts=len(combined_attempts),
        ),
        debug=DecodeDebugModel(
            decoded_text=None,
            format=None,
            corrected_image_base64=best_corrected,
            attempts=combined_attempts,
        ),
        tips=REUPLOAD_TIPS,
    )
