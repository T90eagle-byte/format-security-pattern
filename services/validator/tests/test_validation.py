import asyncio
from pathlib import Path

import httpx
import pytest

from app.main import app
from app.validation import (
    validate_csv,
    validate_json,
    validate_structure,
    validate_xml,
)


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


def _validate(
    sample_name: str,
    detected_format: str,
    *,
    request_id: str = "test-001",
) -> httpx.Response:
    data = (SAMPLES / sample_name).read_bytes()
    return _request(
        "POST",
        "/validate",
        data={
            "request_id": request_id,
            "detected_format": detected_format,
        },
        files={"file": ("untrusted-name.bin", data, "application/octet-stream")},
    )


def test_health_is_preserved() -> None:
    response = _request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "validator"}


@pytest.mark.parametrize(
    ("sample_name", "detected_format"),
    [
        ("valid.json", "JSON"),
        ("valid.xml", "XML"),
        ("valid.csv", "CSV"),
    ],
)
def test_valid_structures(sample_name: str, detected_format: str) -> None:
    response = _validate(sample_name, detected_format)

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "format": detected_format,
        "status": "VALID",
        "reason": None,
    }


def test_broken_json_is_invalid_json() -> None:
    response = _validate("broken.json", "JSON")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["reason"] == "INVALID_JSON"


def test_broken_xml_is_invalid_xml() -> None:
    response = _validate("broken.xml", "XML")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["reason"] == "INVALID_XML"


def test_inconsistent_csv_is_invalid_csv() -> None:
    response = _validate("inconsistent.csv", "CSV")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["reason"] == "INVALID_CSV"


def test_empty_file_has_explicit_reason() -> None:
    response = _validate("empty.json", "JSON")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["reason"] == "EMPTY_FILE"


def test_unknown_format_is_unsupported() -> None:
    response = _validate("valid.json", "UNKNOWN")

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "test-001",
        "format": "UNKNOWN",
        "status": "UNSUPPORTED",
        "reason": "UNSUPPORTED_FORMAT",
    }


def test_xml_external_entity_is_blocked() -> None:
    response = _validate("external-entity.xml", "XML")

    assert response.status_code == 200
    assert response.json()["status"] == "INVALID"
    assert response.json()["reason"] == "INVALID_XML"


@pytest.mark.parametrize(
    "data",
    [
        b"a,b\n1,2\n",
        b"a;b\n1;2\n",
        b"a\tb\n1\t2\n",
    ],
)
def test_all_required_csv_delimiters(data: bytes) -> None:
    assert validate_csv(data) == ("VALID", None)


def test_validation_helpers() -> None:
    assert validate_json(b'{"ok": true}') == ("VALID", None)
    assert validate_xml(b"<root />") == ("VALID", None)
    assert validate_structure(b"content", "PDF") == (
        "UNSUPPORTED",
        "UNSUPPORTED_FORMAT",
    )


def test_invalid_encodings_do_not_raise_server_errors() -> None:
    xml_with_unknown_encoding = (
        b'<?xml version="1.0" encoding="made-up"?><root />'
    )

    assert validate_json(b"\xff") == ("INVALID", "INVALID_JSON")
    assert validate_xml(xml_with_unknown_encoding) == ("INVALID", "INVALID_XML")
    assert validate_csv(b"\xff,\xfe") == ("INVALID", "INVALID_CSV")


def test_upload_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "4")

    response = _request(
        "POST",
        "/validate",
        data={"request_id": "too-large", "detected_format": "JSON"},
        files={"file": ("ignored.json", b'{"too": "large"}', "application/json")},
    )

    assert response.status_code == 413
