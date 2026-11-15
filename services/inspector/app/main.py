import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.inspection import compare_formats, detect_actual_format, detect_declared_format


SERVICE_NAME = os.getenv("SERVICE_NAME", "inspector")
DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024

app = FastAPI(title=f"{SERVICE_NAME} service")


class InspectionResponse(BaseModel):
    request_id: str
    filename: str
    declared_format: str
    detected_format: str
    match: bool
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


@app.post("/inspect", response_model=InspectionResponse)
async def inspect(
    file: UploadFile = File(...),
    request_id: str = Form(...),
) -> InspectionResponse:
    filename = file.filename or ""
    declared_format, metadata_reason = detect_declared_format(
        filename,
        file.content_type,
    )

    try:
        data = await _read_upload(file)
    finally:
        await file.close()

    detected_format = detect_actual_format(data)
    match, reason = compare_formats(declared_format, detected_format)

    if metadata_reason == "METADATA_MISMATCH":
        match = False
        reason = metadata_reason

    return InspectionResponse(
        request_id=request_id,
        filename=filename,
        declared_format=declared_format,
        detected_format=detected_format,
        match=match,
        reason=reason,
    )
