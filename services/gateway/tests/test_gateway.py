import asyncio
import json
import re
from collections.abc import Callable
from uuid import UUID

import httpx
import pytest

from app.main import app, get_http_client, safe_filename


class MockPipeline:
    def __init__(
        self,
        *,
        declared_format: str = "JSON",
        detected_format: str = "JSON",
        inspection_match: bool = True,
        inspection_reason: str | None = None,
        validation_status: str = "VALID",
        validation_reason: str | None = None,
        fail_service: str | None = None,
    ) -> None:
        self.declared_format = declared_format
        self.detected_format = detected_format
        self.inspection_match = inspection_match
        self.inspection_reason = inspection_reason
        self.validation_status = validation_status
        self.validation_reason = validation_reason
        self.fail_service = fail_service
        self.calls: list[str] = []
        self.audit_payload: dict[str, object] | None = None

    @staticmethod
    def _form_value(request: httpx.Request, field: str) -> str:
        pattern = rb'name="' + re.escape(field.encode()) + rb'"\r\n\r\n([^\r]*)'
        match = re.search(pattern, request.content)
        assert match is not None, f"missing multipart field: {field}"
        return match.group(1).decode()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        service = request.url.host
        self.calls.append(path)

        if self.fail_service == service:
            raise httpx.ConnectError("service unavailable", request=request)

        if path == "/inspect":
            request_id = self._form_value(request, "request_id")
            return httpx.Response(
                200,
                json={
                    "request_id": request_id,
                    "filename": "valid.json",
                    "declared_format": self.declared_format,
                    "detected_format": self.detected_format,
                    "match": self.inspection_match,
                    "reason": self.inspection_reason,
                },
            )

        if path == "/validate":
            request_id = self._form_value(request, "request_id")
            return httpx.Response(
                200,
                json={
                    "request_id": request_id,
                    "format": self.detected_format,
                    "status": self.validation_status,
                    "reason": self.validation_reason,
                },
            )

        if path == "/store":
            request_id = self._form_value(request, "request_id")
            decision = self._form_value(request, "decision")
            stored = decision != "REJECT"
            bucket = (
                "trusted"
                if decision == "ACCEPT"
                else "quarantine" if decision == "QUARANTINE" else None
            )
            return httpx.Response(
                200,
                json={
                    "request_id": request_id,
                    "decision": decision,
                    "stored": stored,
                    "bucket": bucket,
                    "object_name": f"{request_id}/valid.json" if stored else None,
                },
            )

        if path == "/audit/event":
            self.audit_payload = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "id": 1,
                    "request_id": self.audit_payload["request_id"],
                    "stored": True,
                },
            )

        return httpx.Response(404)


@pytest.fixture(autouse=True)
def service_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSPECTOR_URL", "http://inspector")
    monkeypatch.setenv("VALIDATOR_URL", "http://validator")
    monkeypatch.setenv("STORAGE_URL", "http://storage")
    monkeypatch.setenv("AUDIT_URL", "http://audit")


def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def _ingest(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    filename: str = "valid.json",
    content: bytes = b'{"valid": true}',
    content_type: str = "application/json",
) -> httpx.Response:
    async def send_request() -> httpx.Response:
        downstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def override_http_client():
            yield downstream

        app.dependency_overrides[get_http_client] = override_http_client
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.post(
                    "/ingest",
                    files={"file": (filename, content, content_type)},
                )
        finally:
            app.dependency_overrides.clear()
            await downstream.aclose()

    return asyncio.run(send_request())


def test_health_is_preserved() -> None:
    response = _request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "service": "gateway"}


def test_valid_json_runs_full_accept_pipeline() -> None:
    pipeline = MockPipeline()

    response = _ingest(pipeline)

    assert response.status_code == 200
    body = response.json()
    UUID(body["request_id"])
    assert body == {
        "request_id": body["request_id"],
        "filename": "valid.json",
        "declared_format": "JSON",
        "detected_format": "JSON",
        "validation_status": "VALID",
        "decision": "ACCEPT",
        "reason": None,
        "stored": True,
        "bucket": "trusted",
        "object_name": f'{body["request_id"]}/valid.json',
        "audit_stored": True,
    }
    assert pipeline.calls == ["/inspect", "/validate", "/store", "/audit/event"]
    assert pipeline.audit_payload is not None
    assert pipeline.audit_payload["duration_ms"] >= 0


def test_format_mismatch_skips_validator_and_quarantines() -> None:
    pipeline = MockPipeline(
        detected_format="CSV",
        inspection_match=False,
        inspection_reason="FORMAT_MISMATCH",
    )

    response = _ingest(
        pipeline,
        filename="telemetry.json",
        content=b"name,value\ntemp,23\n",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "QUARANTINE"
    assert body["reason"] == "FORMAT_MISMATCH"
    assert body["validation_status"] is None
    assert body["bucket"] == "quarantine"
    assert "/validate" not in pipeline.calls


def test_metadata_mismatch_skips_validator_and_quarantines() -> None:
    pipeline = MockPipeline(
        inspection_match=False,
        inspection_reason="METADATA_MISMATCH",
    )

    response = _ingest(pipeline, content_type="text/csv")

    assert response.status_code == 200
    assert response.json()["decision"] == "QUARANTINE"
    assert response.json()["reason"] == "METADATA_MISMATCH"
    assert "/validate" not in pipeline.calls


def test_format_not_detected_skips_validator_and_rejects() -> None:
    pipeline = MockPipeline(
        declared_format="UNKNOWN",
        detected_format="UNKNOWN",
        inspection_match=False,
        inspection_reason="FORMAT_NOT_DETECTED",
    )

    response = _ingest(
        pipeline,
        filename="unknown.txt",
        content=b"free form text",
        content_type="text/plain",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "REJECT"
    assert body["stored"] is False
    assert body["bucket"] is None
    assert "/validate" not in pipeline.calls


def test_broken_json_is_rejected_after_validation() -> None:
    pipeline = MockPipeline(
        validation_status="INVALID",
        validation_reason="INVALID_JSON",
    )

    response = _ingest(pipeline, filename="broken.json", content=b'{"value":')

    assert response.status_code == 200
    assert response.json()["validation_status"] == "INVALID"
    assert response.json()["decision"] == "REJECT"
    assert response.json()["reason"] == "INVALID_JSON"


def test_unsupported_validator_result_is_rejected() -> None:
    pipeline = MockPipeline(
        validation_status="UNSUPPORTED",
        validation_reason="something else",
    )

    response = _ingest(pipeline)

    assert response.status_code == 200
    assert response.json()["validation_status"] == "UNSUPPORTED"
    assert response.json()["decision"] == "REJECT"
    assert response.json()["reason"] == "UNSUPPORTED_FORMAT"


def test_upload_size_limit_prevents_all_downstream_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "4")
    pipeline = MockPipeline()

    response = _ingest(pipeline, content=b"12345")

    assert response.status_code == 413
    assert pipeline.calls == []


@pytest.mark.parametrize("service", ["inspector", "validator", "storage"])
def test_required_downstream_failure_is_controlled(service: str) -> None:
    pipeline = MockPipeline(fail_service=service)

    response = _ingest(pipeline)

    assert response.status_code == 502
    assert response.json() == {
        "detail": f"{service.capitalize()} service is unavailable"
    }


def test_audit_failure_preserves_decision_without_repeating_storage() -> None:
    pipeline = MockPipeline(fail_service="audit")

    response = _ingest(pipeline)

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ACCEPT"
    assert body["stored"] is True
    assert body["audit_stored"] is False
    assert pipeline.calls.count("/store") == 1
    assert pipeline.calls.count("/audit/event") == 1


def test_user_filename_is_reduced_to_safe_basename() -> None:
    assert safe_filename("../../evil.json") == "evil.json"
    assert safe_filename("..\\..\\evil.json") == "evil.json"
    assert safe_filename("") == "unnamed_file"
