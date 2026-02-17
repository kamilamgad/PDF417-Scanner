from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional

from .models import ParseResult, ParsedFields

FIELD_CODE_MAP = {
    "DCS": "lastName",
    "DAB": "lastName",
    "DAC": "firstName",
    "DCT": "firstName",
    "DAD": "middleName",
    "DAE": "middleName",
    "DAA": "fullName",
    "DBB": "dateOfBirth",
    "DBA": "expirationDate",
    "DBD": "issueDate",
    "DAQ": "licenseNumber",
    "DCK": "licenseNumberAlt",
    "DCF": "documentNumber",
    "DCG": "documentNumber",
    "DAG": "addressLine1",
    "DAH": "addressLine2",
    "DAI": "city",
    "DAJ": "state",
    "DAK": "postalCode",
    "DBC": "genderRaw",
    "DCA": "driverClass",
    "DCU": "nameSuffix",
}

SEPARATORS = ["\r\n", "\n", "\r", "\x1e", "\x1d"]
INLINE_CODES = sorted(FIELD_CODE_MAP.keys())
INLINE_CODES_PATTERN = re.compile("(" + "|".join(INLINE_CODES) + ")")
TEXTUAL_BREAK_PATTERN = re.compile(r"<(?:LF|CR|RS|GS|FS)>", flags=re.IGNORECASE)


def _normalize_payload_breaks(payload: str) -> str:
    normalized = payload
    for sep in SEPARATORS:
        normalized = normalized.replace(sep, "\n")
    normalized = TEXTUAL_BREAK_PATTERN.sub("\n", normalized)
    return normalized


def _clean_field_value(value: str) -> str:
    cleaned = TEXTUAL_BREAK_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t\r\n,;|^")


def _split_lines(payload: str) -> list[str]:
    normalized = _normalize_payload_breaks(payload)
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    return lines


def _extract_inline_fields(segment: str, raw_fields: Dict[str, str]) -> None:
    matches = list(INLINE_CODES_PATTERN.finditer(segment))
    if not matches:
        return

    for i, match in enumerate(matches):
        code = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(segment)
        value = _clean_field_value(segment[start:end])
        if value and code not in raw_fields:
            raw_fields[code] = value


def _extract_stream_fields(payload: str, raw_fields: Dict[str, str]) -> None:
    normalized = _normalize_payload_breaks(payload)

    dl_idx = normalized.find("DL")
    id_idx = normalized.find("ID")
    idx_candidates = [idx for idx in (dl_idx, id_idx) if idx >= 0]
    if idx_candidates:
        normalized = normalized[min(idx_candidates) + 2 :]

    matches = list(INLINE_CODES_PATTERN.finditer(normalized))
    if not matches:
        return

    for i, match in enumerate(matches):
        code = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        value = _clean_field_value(normalized[start:end].replace("\n", " "))
        if value and code not in raw_fields:
            raw_fields[code] = value


def _to_iso_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    compact = re.sub(r"[^0-9]", "", value)
    if len(compact) == 8:
        candidates = []
        if compact[:4].isdigit() and int(compact[:4]) in range(1900, 2101):
            candidates.append(("%Y%m%d", compact))
        candidates.append(("%m%d%Y", compact))
        candidates.append(("%d%m%Y", compact))

        for fmt, raw in candidates:
            try:
                parsed = datetime.strptime(raw, fmt)
                if parsed.year in range(1900, 2101):
                    return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.year in range(1900, 2101):
                return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _normalize_postal(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 9:
        return digits[:9]
    if len(digits) >= 5:
        return digits[:5]
    return digits or None


def _normalize_gender(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    raw = value.strip().upper()
    mapping = {
        "1": "M",
        "2": "F",
        "3": "X",
        "9": "U",
        "M": "M",
        "MALE": "M",
        "F": "F",
        "FEMALE": "F",
        "X": "X",
        "NON-BINARY": "X",
        "U": "U",
        "UNKNOWN": "U",
    }
    return mapping.get(raw, "U"), value


def _fill_name_from_fullname(
    first: Optional[str], middle: Optional[str], last: Optional[str], full_name: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not full_name or (first and last):
        return first, middle, last

    normalized = full_name.replace("$", ",")
    if "," in normalized:
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        if len(parts) >= 2:
            last = last or parts[0]
            first = first or parts[1]
        if len(parts) >= 3:
            middle = middle or parts[2]
        return first, middle, last

    tokens = [t.strip() for t in normalized.split() if t.strip()]
    if len(tokens) >= 2:
        first = first or tokens[0]
        last = last or tokens[-1]
        if len(tokens) > 2 and not middle:
            middle = " ".join(tokens[1:-1])
    return first, middle, last


def parse_aamva_payload(payload: str) -> ParseResult:
    raw_fields: Dict[str, str] = {}

    for line in _split_lines(payload):
        if len(line) >= 4 and line[0] == "D" and re.fullmatch(r"[A-Z0-9]{3}", line[:3]):
            raw_fields[line[:3]] = _clean_field_value(line[3:])
            continue

        dl_idx = line.find("DL")
        id_idx = line.find("ID")
        idx_candidates = [idx for idx in (dl_idx, id_idx) if idx >= 0]
        if idx_candidates:
            start = min(idx_candidates) + 2
            _extract_inline_fields(line[start:], raw_fields)
        else:
            _extract_inline_fields(line, raw_fields)

    if len(raw_fields) < 2:
        _extract_stream_fields(payload, raw_fields)

    mapped: dict[str, Optional[str]] = {}
    for code, key in FIELD_CODE_MAP.items():
        if code in raw_fields:
            mapped[key] = raw_fields[code]

    first_name, middle_name, last_name = _fill_name_from_fullname(
        mapped.get("firstName"),
        mapped.get("middleName"),
        mapped.get("lastName"),
        mapped.get("fullName"),
    )

    gender, gender_raw = _normalize_gender(mapped.get("genderRaw"))
    driver_class = mapped.get("driverClass")
    license_number = mapped.get("licenseNumber") or mapped.get("licenseNumberAlt")
    if not driver_class and gender_raw:
        raw_candidate = gender_raw.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9]{0,2}", raw_candidate) and raw_candidate not in {
            "M",
            "F",
            "X",
            "U",
            "MALE",
            "FEMALE",
            "UNKNOWN",
        }:
            driver_class = raw_candidate
            gender = None

    parsed_fields = ParsedFields(
        firstName=first_name,
        middleName=middle_name,
        lastName=last_name,
        dateOfBirth=_to_iso_date(mapped.get("dateOfBirth")),
        gender=gender,
        genderRaw=gender_raw,
        driverClass=driver_class,
        licenseNumber=license_number,
        documentNumber=mapped.get("documentNumber"),
        addressLine1=mapped.get("addressLine1"),
        addressLine2=mapped.get("addressLine2"),
        city=mapped.get("city"),
        state=(mapped.get("state") or "").upper()[:2] or None,
        postalCode=_normalize_postal(mapped.get("postalCode")),
        issueDate=_to_iso_date(mapped.get("issueDate")),
        expirationDate=_to_iso_date(mapped.get("expirationDate")),
    )

    return ParseResult(fields=parsed_fields, raw_fields=raw_fields)
