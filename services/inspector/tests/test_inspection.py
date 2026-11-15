import asyncio
from pathlib import Path

import httpx
import pytest

from app.inspection import (
    compare_formats,
    detect_actual_format,
    detect_declared_format,
)
from app.main import app


SAMPLES = Path(__file__).parent / "samples"


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def _inspect(
    sample_name: str,
    *,
    filename: str | None = None,
    content_type: str,
    request_id: str = "test-001",
):
    data = (SAMPLES / sample_name).read_bytes()
    return _request(
        "POST",
        "/inspect",
        data={"request_id": request_id},
        files={"file": (filename or sample_name, data, content_type)},
    )


def test_health_is_preserved() -> None:
    response = _request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "inspector"}


@pytest.mark.parametrize(
    ("sample_name", "content_type", "expected_format"),
    [
        ("valid.json", "application/json", "JSON"),
        ("valid.xml", "application/xml", "XML"),
        ("valid.csv", "text/csv", "CSV"),
    ],
)
def test_matching_supported_formats(
    sample_name: str,
    content_type: str,
    expected_format: str,
) -> None:
    response = _inspect(sample_name, content_type=content_type)

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "filename": sample_name,
        "declared_format": expected_format,
        "detected_format": expected_format,
        "match": True,
        "reason": None,
    }


def test_csv_content_with_json_filename_is_format_mismatch() -> None:
    response = _inspect(
        "valid.csv",
        filename="telemetry.json",
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "filename": "telemetry.json",
        "declared_format": "JSON",
        "detected_format": "CSV",
        "match": False,
        "reason": "FORMAT_MISMATCH",
    }


def test_broken_json_is_still_detected_as_json() -> None:
    response = _inspect("broken.json", content_type="application/json")

    assert response.status_code == 200
    assert response.json()["detected_format"] == "JSON"
    assert response.json()["match"] is True
    assert response.json()["reason"] is None


def test_arbitrary_text_is_unknown() -> None:
    response = _inspect("unknown.txt", content_type="text/plain")

    assert response.status_code == 200
    assert response.json()["declared_format"] == "UNKNOWN"
    assert response.json()["detected_format"] == "UNKNOWN"
    assert response.json()["match"] is False
    assert response.json()["reason"] == "FORMAT_NOT_DETECTED"


def test_empty_file_is_handled_without_server_error() -> None:
    response = _inspect("empty.json", content_type="application/json")

    assert response.status_code == 200
    assert response.json()["declared_format"] == "JSON"
    assert response.json()["detected_format"] == "UNKNOWN"
    assert response.json()["match"] is False
    assert response.json()["reason"] == "FORMAT_NOT_DETECTED"


def test_metadata_mismatch_has_priority() -> None:
    response = _inspect(
        "valid.json",
        filename="telemetry.json",
        content_type="text/csv",
    )

    assert response.status_code == 200
    assert response.json()["declared_format"] == "JSON"
    assert response.json()["detected_format"] == "JSON"
    assert response.json()["match"] is False
    assert response.json()["reason"] == "METADATA_MISMATCH"


def test_generic_mime_uses_supported_extension() -> None:
    assert detect_declared_format("data.json", "application/octet-stream") == (
        "JSON",
        None,
    )


def test_mime_parameters_are_ignored() -> None:
    assert detect_declared_format("data.csv", "text/csv; charset=utf-8") == (
        "CSV",
        None,
    )


def test_format_helpers_do_not_fully_validate_json() -> None:
    assert detect_actual_format(b'\xef\xbb\xbf  {"sensor": 123,') == "JSON"
    assert compare_formats("JSON", "JSON") == (True, None)
    assert compare_formats("JSON", "UNKNOWN") == (
        False,
        "FORMAT_NOT_DETECTED",
    )


def test_upload_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "4")

    response = _request(
        "POST",
        "/inspect",
        data={"request_id": "too-large"},
        files={"file": ("data.json", b'{"too": "large"}', "application/json")},
    )

    assert response.status_code == 413
