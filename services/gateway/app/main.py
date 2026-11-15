import os
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.orchestration import (
    DownstreamServiceError,
    GatewayConfigurationError,
    ServiceUrls,
    call_audit,
    call_inspector,
    call_storage,
    call_validator,
    decision_from_inspection,
    decision_from_validation,
)
from app.web import router as web_router


SERVICE_NAME = os.getenv("SERVICE_NAME", "gateway")
DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024
DEFAULT_DOWNSTREAM_TIMEOUT_SECONDS = 10.0
READ_CHUNK_SIZE = 64 * 1024
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

app = FastAPI(title=f"{SERVICE_NAME} service")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
app.include_router(web_router)


class IngestResponse(BaseModel):
    request_id: str
    filename: str
    declared_format: str
    detected_format: str
    validation_status: str | None
    decision: str
    reason: str | None
    stored: bool
    bucket: str | None
    object_name: str | None
    audit_stored: bool


def _positive_number_from_environment(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _max_upload_size() -> int:
    return int(
        _positive_number_from_environment(
            "MAX_UPLOAD_SIZE_BYTES",
            DEFAULT_MAX_UPLOAD_SIZE,
        )
    )


def safe_filename(filename: str | None) -> str:
    """Return a transport-safe basename without using it as a local path."""

    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    basename = CONTROL_CHARACTERS.sub("_", basename).strip()
    return basename if basename not in {"", ".", ".."} else "unnamed_file"


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


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    timeout = _positive_number_from_environment(
        "DOWNSTREAM_TIMEOUT_SECONDS",
        DEFAULT_DOWNSTREAM_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "UP", "service": SERVICE_NAME}


@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: Annotated[UploadFile, File(...)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> IngestResponse:
    request_id = str(uuid4())
    filename = safe_filename(file.filename)
    content_type = file.content_type or "application/octet-stream"
    started_at = time.perf_counter()

    try:
        data = await _read_upload(file)
    finally:
        await file.close()

    try:
        urls = ServiceUrls.from_environment()
        inspection = await call_inspector(
            client,
            urls.inspector,
            data=data,
            filename=filename,
            content_type=content_type,
            request_id=request_id,
        )

        decision_result = decision_from_inspection(inspection)
        validation_status: str | None = None

        if decision_result is None:
            validation = await call_validator(
                client,
                urls.validator,
                data=data,
                filename=filename,
                content_type=content_type,
                request_id=request_id,
                detected_format=inspection.detected_format,
            )
            validation_status = validation.status
            decision, reason = decision_from_validation(validation)
        else:
            decision, reason = decision_result

        storage = await call_storage(
            client,
            urls.storage,
            data=data,
            filename=filename,
            content_type=content_type,
            request_id=request_id,
            detected_format=inspection.detected_format,
            decision=decision,
            reason=reason,
        )

        duration_ms = (time.perf_counter() - started_at) * 1000
        audit_stored = await call_audit(
            client,
            urls.audit,
            payload={
                "request_id": request_id,
                "filename": filename,
                "declared_format": inspection.declared_format,
                "detected_format": inspection.detected_format,
                "validation_status": validation_status,
                "decision": decision,
                "reason": reason,
                "duration_ms": duration_ms,
            },
        )
    except GatewayConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway service configuration is incomplete",
        ) from error
    except DownstreamServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error.public_message,
        ) from error

    return IngestResponse(
        request_id=request_id,
        filename=filename,
        declared_format=inspection.declared_format,
        detected_format=inspection.detected_format,
        validation_status=validation_status,
        decision=decision,
        reason=reason,
        stored=storage.stored,
        bucket=storage.bucket,
        object_name=storage.object_name,
        audit_stored=audit_stored,
    )
