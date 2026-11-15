import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.validation import validate_structure


SERVICE_NAME = os.getenv("SERVICE_NAME", "validator")
DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024

app = FastAPI(title=f"{SERVICE_NAME} service")


class ValidationResponse(BaseModel):
    request_id: str
    format: str
    status: str
    reason: str | None


def _max_upload_size() -> int:
    raw_value = os.getenv("MAX_UPLOAD_SIZE_BYTES", str(DEFAULT_MAX_UPLOAD_SIZE))
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_SIZE
    return value if value > 0 else DEFAULT_MAX_UPLOAD_SIZE


async def _read_upload(file: UploadFile) -> bytes:
    max_size = _max_upload_size()
    data = bytearray()

    while chunk := await file.read(READ_CHUNK_SIZE):
        data.extend(chunk)
        if len(data) > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {max_size}-byte size limit",
            )

    return bytes(data)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/validate", response_model=ValidationResponse)
async def validate(
    file: UploadFile = File(...),
    request_id: str = Form(...),
    detected_format: str = Form(...),
) -> ValidationResponse:
    try:
        data = await _read_upload(file)
    finally:
        await file.close()

    normalized_format = detected_format.strip().upper()
    validation_status, reason = validate_structure(data, normalized_format)

    return ValidationResponse(
        request_id=request_id,
        format=normalized_format,
        status=validation_status,
        reason=reason,
    )
