from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Decision = Literal["ACCEPT", "QUARANTINE", "REJECT"]
ValidationStatus = Literal["VALID", "INVALID", "UNSUPPORTED"]


class AuditEventCreate(BaseModel):
    request_id: str = Field(min_length=1, max_length=255)
    filename: str = Field(max_length=1024)
    declared_format: str | None = Field(default=None, max_length=64)
    detected_format: str | None = Field(default=None, max_length=64)
    validation_status: ValidationStatus | None = None
    decision: Decision
    reason: str | None = Field(default=None, max_length=1024)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)


class AuditEventCreated(BaseModel):
    id: int
    request_id: str
    stored: bool


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    timestamp: datetime
    filename: str
    declared_format: str | None
    detected_format: str | None
    validation_status: str | None
    decision: str
    reason: str | None
    duration_ms: float
