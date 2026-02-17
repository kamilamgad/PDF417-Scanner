from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ParsedFields(BaseModel):
    firstName: Optional[str] = None
    middleName: Optional[str] = None
    lastName: Optional[str] = None
    dateOfBirth: Optional[str] = None
    gender: Optional[str] = None
    genderRaw: Optional[str] = None
    driverClass: Optional[str] = None
    licenseNumber: Optional[str] = None
    documentNumber: Optional[str] = None
    addressLine1: Optional[str] = None
    addressLine2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    issueDate: Optional[str] = None
    expirationDate: Optional[str] = None


class ConfidenceModel(BaseModel):
    decode: float = Field(ge=0.0, le=1.0)
    notes: str
    attempts: int


class DecodeAttempt(BaseModel):
    attempt: int
    transform: str
    rotation: int
    success: bool


class DecodeDebugModel(BaseModel):
    decoded_text: Optional[str] = None
    format: Optional[str] = None
    corrected_image_base64: Optional[str] = None
    rawFields: Dict[str, str] = Field(default_factory=dict)
    attempts: List[DecodeAttempt] = Field(default_factory=list)


class DecodeResponse(BaseModel):
    status: Literal["success", "needs_reupload", "error"]
    fields: ParsedFields = Field(default_factory=ParsedFields)
    confidence: ConfidenceModel
    debug: DecodeDebugModel
    tips: Optional[List[str]] = None


class ParseResult(BaseModel):
    fields: ParsedFields
    raw_fields: Dict[str, str]
