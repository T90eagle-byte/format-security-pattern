import asyncio
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.main import app
from app.object_storage import ensure_buckets, sanitize_filename


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def _store(
    minio_client: Mock,
    *,
    decision: str,
    filename: str = "valid.json",
    content: bytes = b'{"valid": true}',
    request_id: str = "test-001",
    detected_format: str = "JSON",
    reason: str | None = None,
) -> httpx.Response:
    app.state.minio_client = minio_client
    form_data = {
        "request_id": request_id,
        "decision": decision,
        "detected_format": detected_format,
    }
    if reason is not None:
        form_data["reason"] = reason

    return _request(
        "POST",
        "/store",
        data=form_data,
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_health_is_preserved() -> None:
    response = _request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "storage"}


def test_accept_stores_in_trusted_bucket() -> None:
    client = Mock()

    response = _store(client, decision="ACCEPT")

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "decision": "ACCEPT",
        "stored": True,
        "bucket": "trusted",
        "object_name": "test-001/valid.json",
    }
    assert client.put_object.call_args.args[:2] == (
        "trusted",
        "test-001/valid.json",
    )


def test_quarantine_stores_in_quarantine_bucket() -> None:
    client = Mock()

    response = _store(
        client,
        decision="QUARANTINE",
        filename="valid.csv",
        detected_format="CSV",
        reason="FORMAT_MISMATCH",
    )

    assert response.status_code == 200
    assert response.json()["bucket"] == "quarantine"
    assert response.json()["stored"] is True
    assert client.put_object.call_args.args[:2] == (
        "quarantine",
        "test-001/valid.csv",
    )


def test_reject_does_not_call_put_object() -> None:
    client = Mock()

    response = _store(client, decision="REJECT")

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "decision": "REJECT",
        "stored": False,
        "bucket": None,
        "object_name": None,
    }
    client.put_object.assert_not_called()


def test_unknown_decision_is_client_error() -> None:
    response = _store(Mock(), decision="UNKNOWN")

    assert response.status_code == 422


def test_path_traversal_filename_is_sanitized() -> None:
    client = Mock()

    response = _store(
        client,
        decision="ACCEPT",
        filename="../../evil.json",
    )

    assert response.status_code == 200
    assert response.json()["object_name"] == "test-001/evil.json"
    assert ".." not in response.json()["object_name"]


def test_empty_filename_is_safe() -> None:
    assert sanitize_filename("") == "unnamed_file"
    assert sanitize_filename(None) == "unnamed_file"


def test_upload_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "4")
    client = Mock()

    response = _store(
        client,
        decision="ACCEPT",
        content=b"12345",
    )

    assert response.status_code == 413
    client.put_object.assert_not_called()


def test_quarantine_metadata_is_complete() -> None:
    client = Mock()

    response = _store(
        client,
        decision="QUARANTINE",
        filename="telemetry.csv",
        request_id="test-002",
        detected_format="CSV",
        reason="FORMAT_MISMATCH",
    )

    assert response.status_code == 200
    call = client.put_object.call_args
    assert call.kwargs["metadata"] == {
        "request_id": "test-002",
        "decision": "QUARANTINE",
        "reason": "FORMAT_MISMATCH",
        "detected_format": "CSV",
        "original_filename": "telemetry.csv",
    }


def test_uploaded_content_is_passed_as_memory_stream() -> None:
    client = Mock()

    response = _store(
        client,
        decision="ACCEPT",
        content=b"payload",
    )

    assert response.status_code == 200
    stream = client.put_object.call_args.args[2]
    assert isinstance(stream, BytesIO)
    assert stream.getvalue() == b"payload"


def test_request_id_is_also_sanitized() -> None:
    client = Mock()

    response = _store(
        client,
        decision="ACCEPT",
        request_id="../../request",
    )

    assert response.status_code == 200
    assert response.json()["object_name"] == "request/valid.json"


def test_ensure_buckets_creates_only_missing_buckets() -> None:
    client = Mock()
    client.bucket_exists.side_effect = [True, False]

    ensure_buckets(client)

    assert [call.args[0] for call in client.bucket_exists.call_args_list] == [
        "trusted",
        "quarantine",
    ]
    client.make_bucket.assert_called_once_with("quarantine")


def test_quarantine_objects_returns_safe_metadata() -> None:
    client = Mock()
    client.list_objects.return_value = [
        SimpleNamespace(
            object_name="request/telemetry.json",
            size=42,
            last_modified=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        )
    ]
    client.stat_object.return_value = SimpleNamespace(
        metadata={
            "x-amz-meta-request_id": "request",
            "x-amz-meta-reason": "FORMAT_MISMATCH",
            "x-amz-meta-detected_format": "CSV",
            "content-type": "text/csv",
        }
    )
    app.state.minio_client = client

    response = _request("GET", "/objects/quarantine")

    assert response.status_code == 200
    assert response.json() == [
        {
            "object_name": "request/telemetry.json",
            "size": 42,
            "last_modified": "2026-09-01T10:00:00Z",
            "metadata": {
                "request_id": "request",
                "reason": "FORMAT_MISMATCH",
                "detected_format": "CSV",
            },
        }
    ]
