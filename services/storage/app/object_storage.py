from __future__ import annotations

import io
import os
import re
import unicodedata
from urllib.parse import urlsplit

from minio import Minio


REQUIRED_BUCKETS = ("trusted", "quarantine")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
PUBLIC_METADATA_FIELDS = {
    "request_id",
    "decision",
    "reason",
    "detected_format",
    "original_filename",
}


def _parse_secure(value: str | None, inferred: bool) -> bool:
    if value is None or not value.strip():
        return inferred

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise RuntimeError("MINIO_SECURE must be a boolean value")


def create_minio_client() -> Minio:
    raw_endpoint = os.environ["MINIO_ENDPOINT"].strip()
    access_key = os.environ["MINIO_ACCESS_KEY"]
    secret_key = os.environ["MINIO_SECRET_KEY"]

    if "://" in raw_endpoint:
        parsed = urlsplit(raw_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("MINIO_ENDPOINT must be a valid HTTP(S) endpoint")
        endpoint = parsed.netloc
        inferred_secure = parsed.scheme == "https"
    else:
        endpoint = raw_endpoint.strip("/")
        inferred_secure = False

    if not endpoint:
        raise RuntimeError("MINIO_ENDPOINT must not be empty")

    secure = _parse_secure(os.getenv("MINIO_SECURE"), inferred_secure)
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def sanitize_object_component(value: str, fallback: str) -> str:
    without_controls = "".join(
        character
        for character in value
        if not unicodedata.category(character).startswith("C")
    )
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in without_controls
    )
    safe = re.sub(r"_+", "_", safe).strip("._-")
    return safe[:128] or fallback


def sanitize_filename(filename: str | None) -> str:
    basename = re.split(r"[\\/]", filename or "")[-1]
    safe = sanitize_object_component(basename, "unnamed_file")
    return safe[:255]


def metadata_value(value: str) -> str:
    without_controls = "".join(
        character if not unicodedata.category(character).startswith("C") else " "
        for character in value
    )
    compact = " ".join(without_controls.split())
    return compact.encode("ascii", "backslashreplace").decode("ascii")[:1024]


def ensure_buckets(client: Minio) -> None:
    for bucket in REQUIRED_BUCKETS:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def store_object(
    client: Minio,
    bucket: str,
    object_name: str,
    data: bytes,
    metadata: dict[str, str],
    content_type: str | None,
) -> None:
    client.put_object(
        bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type or "application/octet-stream",
        metadata=metadata,
    )


def _public_metadata(metadata: object) -> dict[str, str]:
    if not hasattr(metadata, "items"):
        return {}

    public: dict[str, str] = {}
    for raw_key, raw_value in metadata.items():
        key = str(raw_key).lower()
        if key.startswith("x-amz-meta-"):
            key = key.removeprefix("x-amz-meta-")
        key = key.replace("-", "_")
        if key in PUBLIC_METADATA_FIELDS:
            public[key] = str(raw_value)
    return public


def list_quarantine_objects(client: Minio) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    for item in client.list_objects("quarantine", recursive=True):
        stat = client.stat_object("quarantine", item.object_name)
        objects.append(
            {
                "object_name": item.object_name,
                "size": item.size or 0,
                "last_modified": item.last_modified,
                "metadata": _public_metadata(stat.metadata),
            }
        )
    return objects
