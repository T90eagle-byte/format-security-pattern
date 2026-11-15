import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.presentation import (
    DECISION_LABELS,
    PRESENTATION_LABELS,
    REASON_LABELS,
    VALIDATION_STATUS_LABELS,
)


WEB_TIMEOUT_SECONDS = 3.0
TEMPLATES_DIRECTORY = Path(__file__).resolve().parent / "templates"

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=TEMPLATES_DIRECTORY)
templates.env.globals.update(
    decision_labels=DECISION_LABELS,
    reason_labels=REASON_LABELS,
    validation_status_labels=VALIDATION_STATUS_LABELS,
    presentation_labels=PRESENTATION_LABELS,
)

BROKEN_CSV_CONTENT = (
    "id,value\n"
    + "".join(f"{row},{row * 10}\n" for row in range(1, 50))
    + "50,broken,extra\n"
)

TEST_OBJECT_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "valid-json",
        "title": "Корректный JSON",
        "description": "Небольшой корректный JSON-объект с телеметрией.",
        "filename": "valid.json",
        "mime_type": "application/json",
        "content": '{\n  "sensor": "temperature",\n  "value": 23.4\n}\n',
        "expected": {
            "decision": "ACCEPT",
            "reason": None,
            "bucket": "trusted",
            "validation_status": "VALID",
        },
    },
    {
        "id": "valid-xml",
        "title": "Корректный XML",
        "description": "Корректный XML-документ с одним показанием датчика.",
        "filename": "valid.xml",
        "mime_type": "application/xml",
        "content": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<telemetry><sensor>temperature</sensor><value>23.4</value></telemetry>\n"
        ),
        "expected": {
            "decision": "ACCEPT",
            "reason": None,
            "bucket": "trusted",
            "validation_status": "VALID",
        },
    },
    {
        "id": "valid-csv",
        "title": "Корректный CSV",
        "description": "Табличные данные с согласованным числом колонок.",
        "filename": "valid.csv",
        "mime_type": "text/csv",
        "content": "sensor,value\ntemperature,23.4\nhumidity,48\n",
        "expected": {
            "decision": "ACCEPT",
            "reason": None,
            "bucket": "trusted",
            "validation_status": "VALID",
        },
    },
    {
        "id": "csv-as-json",
        "title": "CSV под видом JSON",
        "description": (
            "Основной сценарий УБИ.050: расширение и MIME заявляют JSON, "
            "но содержимое является корректным CSV."
        ),
        "filename": "telemetry.json",
        "mime_type": "application/json",
        "content": "sensor,value\ntemperature,23.4\nhumidity,48\n",
        "expected": {
            "decision": "QUARANTINE",
            "reason": "FORMAT_MISMATCH",
            "bucket": "quarantine",
            "validation_status": None,
        },
    },
    {
        "id": "metadata-mismatch",
        "title": "Конфликт служебных признаков",
        "description": "Расширение JSON противоречит известному MIME text/csv.",
        "filename": "valid.json",
        "mime_type": "text/csv",
        "content": '{"sensor":"temperature","value":23.4}\n',
        "expected": {
            "decision": "QUARANTINE",
            "reason": "METADATA_MISMATCH",
            "bucket": "quarantine",
            "validation_status": None,
        },
    },
    {
        "id": "broken-json",
        "title": "Повреждённый JSON",
        "description": "Содержимое похоже на JSON, но содержит синтаксическую ошибку.",
        "filename": "broken.json",
        "mime_type": "application/json",
        "content": '{"sensor":"temperature","value":23.4,\n',
        "expected": {
            "decision": "REJECT",
            "reason": "INVALID_JSON",
            "bucket": None,
            "validation_status": "INVALID",
        },
    },
    {
        "id": "broken-xml",
        "title": "Повреждённый XML",
        "description": "XML-подобное содержимое с незакрытым корневым элементом.",
        "filename": "broken.xml",
        "mime_type": "application/xml",
        "content": "<telemetry><sensor>temperature</sensor>\n",
        "expected": {
            "decision": "REJECT",
            "reason": "INVALID_XML",
            "bucket": None,
            "validation_status": "INVALID",
        },
    },
    {
        "id": "broken-csv",
        "title": "Некорректный CSV",
        "description": "Последняя строка нарушает согласованное число колонок таблицы.",
        "filename": "broken.csv",
        "mime_type": "text/csv",
        "content": BROKEN_CSV_CONTENT,
        "expected": {
            "decision": "REJECT",
            "reason": "INVALID_CSV",
            "bucket": None,
            "validation_status": "INVALID",
        },
    },
    {
        "id": "unknown-format",
        "title": "Неизвестный формат",
        "description": "Произвольный текст без признаков JSON, XML или CSV.",
        "filename": "unknown.dat",
        "mime_type": "application/octet-stream",
        "content": "This is an arbitrary payload without a known structure.\n",
        "expected": {
            "decision": "REJECT",
            "reason": "FORMAT_NOT_DETECTED",
            "bucket": None,
            "validation_status": None,
        },
    },
    {
        "id": "empty-json",
        "title": "Пустой JSON",
        "description": "Пустой файл с расширением и MIME формата JSON.",
        "filename": "empty.json",
        "mime_type": "application/json",
        "content": "",
        "expected": {
            "decision": "REJECT",
            "reason": "FORMAT_NOT_DETECTED",
            "bucket": None,
            "validation_status": None,
        },
    },
)


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    state: str
    detail: str | None = None


async def get_web_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=WEB_TIMEOUT_SECONDS) as client:
        yield client


def _service_url(environment_name: str) -> str | None:
    value = os.getenv(environment_name, "").strip().rstrip("/")
    return value or None


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"active_page": "upload"},
    )


@router.get("/test-objects", response_class=HTMLResponse)
async def test_objects(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="test_objects.html",
        context={
            "active_page": "test-objects",
            "scenarios": TEST_OBJECT_SCENARIOS,
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    client: Annotated[httpx.AsyncClient, Depends(get_web_http_client)],
) -> HTMLResponse:
    events: list[dict[str, Any]] = []
    error: str | None = None
    audit_url = _service_url("AUDIT_URL")

    if audit_url is None:
        error = "Сервис истории временно недоступен."
    else:
        try:
            payload = await _get_json(client, f"{audit_url}/events?limit=100")
            if not isinstance(payload, list):
                raise ValueError("Unexpected Audit response")
            events = [item for item in payload if isinstance(item, dict)]
            events.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        except (httpx.HTTPError, ValueError):
            error = "Сервис истории временно недоступен."

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "active_page": "history",
            "events": events,
            "error": error,
        },
    )


@router.get("/quarantine", response_class=HTMLResponse)
async def quarantine(
    request: Request,
    client: Annotated[httpx.AsyncClient, Depends(get_web_http_client)],
) -> HTMLResponse:
    objects: list[dict[str, Any]] = []
    error: str | None = None
    storage_url = _service_url("STORAGE_URL")

    if storage_url is None:
        error = "Сервис карантина временно недоступен."
    else:
        try:
            payload = await _get_json(client, f"{storage_url}/objects/quarantine")
            if not isinstance(payload, list):
                raise ValueError("Unexpected Storage response")
            objects = [item for item in payload if isinstance(item, dict)]
            objects.sort(
                key=lambda item: str(item.get("last_modified", "")),
                reverse=True,
            )
        except (httpx.HTTPError, ValueError):
            error = "Сервис карантина временно недоступен."

    return templates.TemplateResponse(
        request=request,
        name="quarantine.html",
        context={
            "active_page": "quarantine",
            "objects": objects,
            "error": error,
        },
    )


async def _probe_component(
    client: httpx.AsyncClient,
    name: str,
    url: str | None,
) -> tuple[ComponentStatus, dict[str, Any]]:
    if url is None:
        return ComponentStatus(name=name, state="DOWN"), {}

    try:
        response = await client.get(url)
        response.raise_for_status()
        try:
            decoded_payload = response.json()
        except ValueError:
            decoded_payload = {}

        payload = decoded_payload if isinstance(decoded_payload, dict) else {}

        state_value = str(payload.get("status", "UP")).upper()
        state = "UP" if state_value in {"UP", "OK", "HEALTHY"} else "DOWN"
        return ComponentStatus(name=name, state=state), payload
    except httpx.HTTPError:
        return ComponentStatus(name=name, state="DOWN"), {}


@router.get("/status", response_class=HTMLResponse)
async def system_status(
    request: Request,
    client: Annotated[httpx.AsyncClient, Depends(get_web_http_client)],
) -> HTMLResponse:
    targets = (
        (
            "Сервис приёма данных",
            os.getenv("GATEWAY_HEALTH_URL", "http://localhost:8080/health"),
        ),
        (
            "Определение формата",
            f"{_service_url('INSPECTOR_URL')}/health"
            if _service_url("INSPECTOR_URL")
            else None,
        ),
        (
            "Структурная проверка",
            f"{_service_url('VALIDATOR_URL')}/health"
            if _service_url("VALIDATOR_URL")
            else None,
        ),
        (
            "Хранилище",
            f"{_service_url('STORAGE_URL')}/health"
            if _service_url("STORAGE_URL")
            else None,
        ),
        (
            "Аудит",
            f"{_service_url('AUDIT_URL')}/health"
            if _service_url("AUDIT_URL")
            else None,
        ),
        ("MinIO", _service_url("MINIO_HEALTH_URL")),
        ("Prometheus", _service_url("PROMETHEUS_HEALTH_URL")),
        ("Grafana", _service_url("GRAFANA_HEALTH_URL")),
    )
    results = await asyncio.gather(
        *(_probe_component(client, name, url) for name, url in targets)
    )

    components = [result[0] for result in results]
    audit_payload = results[4][1]
    database_state = (
        "UP" if str(audit_payload.get("database", "")).upper() == "UP" else "DOWN"
    )
    components.insert(
        5,
        ComponentStatus(name="PostgreSQL", state=database_state),
    )

    return templates.TemplateResponse(
        request=request,
        name="status.html",
        context={
            "active_page": "status",
            "components": components,
        },
    )
