import asyncio
import json
import re
from collections.abc import Callable

import httpx
import pytest

from app.main import app
from app.web import get_web_http_client


@pytest.fixture(autouse=True)
def web_service_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_HEALTH_URL", "http://gateway/health")
    monkeypatch.setenv("INSPECTOR_URL", "http://inspector")
    monkeypatch.setenv("VALIDATOR_URL", "http://validator")
    monkeypatch.setenv("STORAGE_URL", "http://storage")
    monkeypatch.setenv("AUDIT_URL", "http://audit")
    monkeypatch.setenv("MINIO_HEALTH_URL", "http://minio/health")
    monkeypatch.setenv("PROMETHEUS_HEALTH_URL", "http://prometheus/health")
    monkeypatch.setenv("GRAFANA_HEALTH_URL", "http://grafana/health")


def _web_request(
    path: str,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> httpx.Response:
    async def send_request() -> httpx.Response:
        downstream = httpx.AsyncClient(
            transport=httpx.MockTransport(
                handler or (lambda request: httpx.Response(404))
            )
        )

        async def override_web_client():
            yield downstream

        app.dependency_overrides[get_web_http_client] = override_web_client
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.get(path)
        finally:
            app.dependency_overrides.clear()
            await downstream.aclose()

    return asyncio.run(send_request())


def _audit_handler(
    decision: str = "ACCEPT",
    reason: str | None = None,
    validation_status: str = "VALID",
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "request_id": "test-request-id",
                    "timestamp": "2026-09-01T10:00:00Z",
                    "filename": "valid.json",
                    "declared_format": "JSON",
                    "detected_format": "JSON",
                    "validation_status": validation_status,
                    "decision": decision,
                    "reason": reason,
                    "duration_ms": 12.3,
                }
            ],
        )

    return handler


def test_index_returns_html_page() -> None:
    response = _web_request("/")

    assert response.status_code == 200
    assert "Система контроля формата входных данных" in response.text
    assert 'id="ingest-form"' in response.text
    assert all(
        label in response.text
        for label in ("Принято", "Карантин", "Отклонено")
    )


def test_test_objects_returns_page_and_navigation_link() -> None:
    response = _web_request("/test-objects")

    assert response.status_code == 200
    assert "Генератор тестовых объектов" in response.text
    assert "Тестовые объекты" in response.text
    assert 'href="/test-objects"' in response.text


def test_test_objects_contains_all_ten_scenarios() -> None:
    response = _web_request("/test-objects")

    assert response.status_code == 200
    scenario_titles = (
        "Корректный JSON",
        "Корректный XML",
        "Корректный CSV",
        "CSV под видом JSON",
        "Конфликт служебных признаков",
        "Повреждённый JSON",
        "Повреждённый XML",
        "Некорректный CSV",
        "Неизвестный формат",
        "Пустой JSON",
    )
    assert all(title in response.text for title in scenario_titles)
    assert all(
        label in response.text
        for label in (
            "Принято",
            "Карантин",
            "Отклонено",
            "Несоответствие форматов",
            "Некорректная структура JSON",
        )
    )
    assert response.text.count("js-send-scenario") == 10
    assert response.text.count("js-download-scenario") == 10


@pytest.mark.parametrize("path", ["/", "/history", "/quarantine", "/status"])
def test_existing_web_pages_remain_available(path: str) -> None:
    response = _web_request(path)

    assert response.status_code == 200


def test_test_object_javascript_uses_browser_files_and_ingest() -> None:
    response = _web_request("/static/app.js")

    assert response.status_code == 200
    assert 'fetch("/ingest"' in response.text
    assert "new File(" in response.text
    assert "new Blob(" in response.text
    assert "new FormData()" in response.text


def test_presentation_mappings_are_exposed_to_browser() -> None:
    response = _web_request("/")

    match = re.search(
        r'<script id="presentation-labels" type="application/json">(.*?)</script>',
        response.text,
    )
    assert match is not None
    labels = json.loads(match.group(1))
    assert labels["decision"] == {
        "ACCEPT": "Принято",
        "QUARANTINE": "Карантин",
        "REJECT": "Отклонено",
    }
    assert labels["reason"]["FORMAT_MISMATCH"] == "Несоответствие форматов"
    assert labels["reason"]["INVALID_JSON"] == "Некорректная структура JSON"
    assert labels["validation_status"]["VALID"] == "Корректна"


def test_history_returns_page() -> None:
    response = _web_request("/history", _audit_handler())

    assert response.status_code == 200
    assert "История обработки" in response.text
    assert "test-request-id" in response.text


def test_quarantine_returns_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/objects/quarantine"
        return httpx.Response(
            200,
            json=[
                {
                    "object_name": "request/telemetry.json",
                    "size": 42,
                    "last_modified": "2026-09-01T10:00:00Z",
                    "metadata": {
                        "reason": "FORMAT_MISMATCH",
                        "detected_format": "CSV",
                        "request_id": "request",
                    },
                }
            ],
        )

    response = _web_request("/quarantine", handler)

    assert response.status_code == 200
    assert "Объекты в карантине" in response.text
    assert "request/telemetry.json" in response.text
    assert "Несоответствие форматов" in response.text


def test_status_returns_page_when_components_are_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"status": "UP"}
        if request.url.host == "audit":
            payload["database"] = "UP"
        return httpx.Response(200, json=payload)

    response = _web_request("/status", handler)

    assert response.status_code == 200
    assert "Состояние системы" in response.text
    assert response.text.count(">UP<") == 9


@pytest.mark.parametrize(
    ("decision", "localized", "css_class"),
    [
        ("ACCEPT", "Принято", "decision--accept"),
        ("QUARANTINE", "Карантин", "decision--quarantine"),
        ("REJECT", "Отклонено", "decision--reject"),
    ],
)
def test_history_renders_localized_decision(
    decision: str,
    localized: str,
    css_class: str,
) -> None:
    response = _web_request("/history", _audit_handler(decision))

    assert response.status_code == 200
    assert localized in response.text
    assert css_class in response.text


@pytest.mark.parametrize(
    ("reason", "localized"),
    [
        ("FORMAT_MISMATCH", "Несоответствие форматов"),
        ("INVALID_JSON", "Некорректная структура JSON"),
    ],
)
def test_history_renders_localized_reason(reason: str, localized: str) -> None:
    response = _web_request("/history", _audit_handler("REJECT", reason))

    assert response.status_code == 200
    assert localized in response.text


@pytest.mark.parametrize(
    ("validation_status", "localized"),
    [
        ("VALID", "Корректна"),
        ("INVALID", "Некорректна"),
        ("UNSUPPORTED", "Не поддерживается"),
    ],
)
def test_history_renders_localized_validation_status(
    validation_status: str,
    localized: str,
) -> None:
    response = _web_request(
        "/history",
        _audit_handler(validation_status=validation_status),
    )

    assert response.status_code == 200
    assert localized in response.text


def test_unknown_presentation_value_has_safe_fallback() -> None:
    response = _web_request(
        "/history",
        _audit_handler("NEW_DECISION", "NEW_REASON", "NEW_STATUS"),
    )

    assert response.status_code == 200
    assert "NEW_DECISION" in response.text
    assert "NEW_REASON" in response.text
    assert "NEW_STATUS" in response.text


def test_unavailable_audit_does_not_break_history() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    response = _web_request("/history", handler)

    assert response.status_code == 200
    assert "Сервис истории временно недоступен" in response.text


def test_one_unavailable_health_endpoint_does_not_break_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "validator":
            raise httpx.ConnectError("unavailable", request=request)
        payload = {"status": "UP"}
        if request.url.host == "audit":
            payload["database"] = "UP"
        return httpx.Response(200, json=payload)

    response = _web_request("/status", handler)

    assert response.status_code == 200
    assert "Структурная проверка" in response.text
    assert "status-card--down" in response.text
    assert ">DOWN<" in response.text
