import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.object_storage import (
    create_minio_client,
    ensure_buckets,
    list_quarantine_objects,
    metadata_value,
    sanitize_filename,
    sanitize_object_component,
    store_object,
)


SERVICE_NAME = os.getenv("SERVICE_NAME", "storage")
DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
BUCKETS_BY_DECISION = {
    "ACCEPT": "trusted",
    "QUARANTINE": "quarantine",
}
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = create_minio_client()

    for attempt in range(1, 11):
        try:
            await run_in_threadpool(ensure_buckets, client)
            break
        except Exception:
            if attempt == 10:
                raise
            logger.warning("MinIO is not ready; retrying bucket initialization")
            await asyncio.sleep(1)

    app.state.minio_client = client
    yield


app = FastAPI(title=f"{SERVICE_NAME} service", lifespan=lifespan)


class StorageResponse(BaseModel):
    request_id: str
    decision: str
    stored: bool
    bucket: str | None
    object_name: str | None


class QuarantineObjectResponse(BaseModel):
    object_name: str
    size: int
    last_modified: datetime | None
    metadata: dict[str, str]


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


@app.get(
    "/objects/quarantine",
    response_model=list[QuarantineObjectResponse],
    tags=["internal"],
)
async def quarantine_objects(request: Request) -> list[dict[str, object]]:
    try:
        return await run_in_threadpool(
            list_quarantine_objects,
            request.app.state.minio_client,
        )
    except Exception as error:
        logger.warning("Unable to list quarantine objects: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is unavailable",
        ) from error


@app.post("/store", response_model=StorageResponse)
async def store(
    request: Request,
    file: UploadFile = File(...),
    request_id: str = Form(...),
    decision: Literal["ACCEPT", "QUARANTINE", "REJECT"] = Form(...),
    detected_format: str = Form(...),
    reason: str | None = Form(None),
) -> StorageResponse:
    original_filename = file.filename or ""
    content_type = file.content_type

    try:
        data = await _read_upload(file)
    finally:
        await file.close()

    if decision == "REJECT":
        return StorageResponse(
            request_id=request_id,
            decision=decision,
            stored=False,
            bucket=None,
            object_name=None,
        )

    bucket = BUCKETS_BY_DECISION[decision]
    object_name = (
        f"{sanitize_object_component(request_id, 'request')}/"
        f"{sanitize_filename(original_filename)}"
    )
    metadata = {
        "request_id": metadata_value(request_id),
        "decision": decision,
        "detected_format": metadata_value(detected_format),
        "original_filename": metadata_value(original_filename),
    }
    if decision == "QUARANTINE":
        metadata["reason"] = metadata_value(reason or "")

    await run_in_threadpool(
        store_object,
        request.app.state.minio_client,
        bucket,
        object_name,
        data,
        metadata,
        content_type,
    )

    return StorageResponse(
        request_id=request_id,
        decision=decision,
        stored=True,
        bucket=bucket,
        object_name=object_name,
    )
