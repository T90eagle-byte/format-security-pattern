import asyncio
import json

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import AuditEvent, Base
from app.main import app


engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, expire_on_commit=False)
app.state.session_factory = TestSession


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send_request())


def _payload(**overrides) -> dict:
    payload = {
        "request_id": "test-001",
        "filename": "telemetry.json",
        "declared_format": "JSON",
        "detected_format": "JSON",
        "validation_status": "VALID",
        "decision": "ACCEPT",
        "reason": None,
        "duration_ms": 15.7,
    }
    payload.update(overrides)
    return payload


def _metric_value(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    assert value is not None
    return value


def _labeled_metric_value(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0


def _metric_family_total(name: str) -> float:
    collector = REGISTRY._names_to_collectors[name]
    return sum(
        sample.value
        for metric in collector.collect()
        for sample in metric.samples
        if sample.name == f"{name}_total"
    )


def _event_count() -> int:
    with TestSession() as session:
        return session.scalar(select(func.count()).select_from(AuditEvent)) or 0


@pytest.fixture(autouse=True)
def clear_events():
    with TestSession() as session:
        session.execute(delete(AuditEvent))
        session.commit()


def test_health_checks_database() -> None:
    response = _request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "service": "audit",
        "database": "UP",
    }


def test_accept_creates_event_and_increments_counter() -> None:
    before = _metric_value("accepted_objects_total")

    response = _request("POST", "/audit/event", json=_payload())

    assert response.status_code == 201
    assert response.json()["stored"] is True
    assert _event_count() == 1
    assert _metric_value("accepted_objects_total") == before + 1


def test_json_without_content_type_preserves_existing_contract() -> None:
    response = _request("POST", "/audit/event", content=json.dumps(_payload()))

    assert response.status_code == 201
    assert response.json()["stored"] is True
    assert _event_count() == 1


def test_quarantine_format_mismatch_updates_only_expected_counters() -> None:
    quarantine_before = _metric_value("quarantined_objects_total")
    mismatch_before = _metric_value("format_mismatch_total")
    validation_before = _metric_value("validation_errors_total")

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="QUARANTINE",
            validation_status=None,
            reason="FORMAT_MISMATCH",
        ),
    )

    assert response.status_code == 201
    assert _metric_value("quarantined_objects_total") == quarantine_before + 1
    assert _metric_value("format_mismatch_total") == mismatch_before + 1
    assert _metric_value("validation_errors_total") == validation_before


def test_quarantine_other_reason_does_not_increment_error_counters() -> None:
    quarantine_before = _metric_value("quarantined_objects_total")
    mismatch_before = _metric_value("format_mismatch_total")
    validation_before = _metric_value("validation_errors_total")
    security_before = _metric_family_total("security_events")

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="QUARANTINE",
            validation_status="INVALID",
            reason="POLICY_REVIEW",
        ),
    )

    assert response.status_code == 201
    assert _metric_value("quarantined_objects_total") == quarantine_before + 1
    assert _metric_value("format_mismatch_total") == mismatch_before
    assert _metric_value("validation_errors_total") == validation_before
    assert _metric_family_total("security_events") == security_before


def test_reject_invalid_increments_validation_error() -> None:
    rejected_before = _metric_value("rejected_objects_total")
    validation_before = _metric_value("validation_errors_total")

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="REJECT",
            validation_status="INVALID",
            reason="INVALID_JSON",
        ),
    )

    assert response.status_code == 201
    assert _metric_value("rejected_objects_total") == rejected_before + 1
    assert _metric_value("validation_errors_total") == validation_before + 1


def test_reject_without_validation_error_does_not_increment_other_counters() -> None:
    rejected_before = _metric_value("rejected_objects_total")
    validation_before = _metric_value("validation_errors_total")
    mismatch_before = _metric_value("format_mismatch_total")

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="REJECT",
            validation_status="VALID",
            reason="FORMAT_MISMATCH",
        ),
    )

    assert response.status_code == 201
    assert _metric_value("rejected_objects_total") == rejected_before + 1
    assert _metric_value("validation_errors_total") == validation_before
    assert _metric_value("format_mismatch_total") == mismatch_before


def test_duration_is_observed_in_seconds() -> None:
    sum_before = _metric_value("inspection_duration_seconds_sum")

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(duration_ms=2500),
    )

    assert response.status_code == 201
    assert _metric_value("inspection_duration_seconds_sum") == pytest.approx(
        sum_before + 2.5,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "UNKNOWN"),
        ("validation_status", "BROKEN"),
        ("duration_ms", -1),
    ],
)
def test_invalid_input_is_client_error(field: str, value: object) -> None:
    response = _request(
        "POST",
        "/audit/event",
        json=_payload(**{field: value}),
    )

    assert response.status_code == 422
    assert _event_count() == 0


def test_events_returns_latest_first_and_honors_limit() -> None:
    for index in range(3):
        response = _request(
            "POST",
            "/audit/event",
            json=_payload(request_id=f"event-{index}"),
        )
        assert response.status_code == 201

    response = _request("GET", "/events?limit=2")

    assert response.status_code == 200
    assert [item["request_id"] for item in response.json()] == [
        "event-2",
        "event-1",
    ]


def test_events_limit_is_bounded() -> None:
    assert _request("GET", "/events?limit=0").status_code == 422
    assert _request("GET", "/events?limit=201").status_code == 422


def test_metrics_endpoint_contains_project_metrics() -> None:
    response = _request("GET", "/metrics")

    assert response.status_code == 200
    for metric_name in (
        "accepted_objects_total",
        "quarantined_objects_total",
        "rejected_objects_total",
        "format_mismatch_total",
        "validation_errors_total",
        "inspection_duration_seconds",
        "security_events_total",
        "format_mismatch_pairs_total",
        "validation_failures_total",
    ):
        assert metric_name in response.text


def test_format_mismatch_increments_security_reason() -> None:
    labels = {"reason": "FORMAT_MISMATCH"}
    before = _labeled_metric_value("security_events_total", labels)

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="QUARANTINE",
            validation_status=None,
            reason="FORMAT_MISMATCH",
        ),
    )

    assert response.status_code == 201
    assert _labeled_metric_value("security_events_total", labels) == before + 1


def test_metadata_mismatch_increments_security_reason() -> None:
    labels = {"reason": "METADATA_MISMATCH"}
    before = _labeled_metric_value("security_events_total", labels)

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="QUARANTINE",
            validation_status=None,
            reason="METADATA_MISMATCH",
        ),
    )

    assert response.status_code == 201
    assert _labeled_metric_value("security_events_total", labels) == before + 1


def test_format_mismatch_pair_json_to_csv_is_counted() -> None:
    labels = {"declared_format": "JSON", "detected_format": "CSV"}
    before = _labeled_metric_value("format_mismatch_pairs_total", labels)

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            declared_format="JSON",
            detected_format="CSV",
            decision="QUARANTINE",
            validation_status=None,
            reason="FORMAT_MISMATCH",
        ),
    )

    assert response.status_code == 201
    assert _labeled_metric_value("format_mismatch_pairs_total", labels) == before + 1


def test_invalid_json_is_counted_by_format_and_reason() -> None:
    labels = {"format": "JSON", "reason": "INVALID_JSON"}
    before = _labeled_metric_value("validation_failures_total", labels)

    response = _request(
        "POST",
        "/audit/event",
        json=_payload(
            decision="REJECT",
            validation_status="INVALID",
            reason="INVALID_JSON",
        ),
    )

    assert response.status_code == 201
    assert _labeled_metric_value("validation_failures_total", labels) == before + 1


def test_accept_without_reason_does_not_increment_security_events() -> None:
    before = _metric_family_total("security_events")

    response = _request("POST", "/audit/event", json=_payload())

    assert response.status_code == 201
    assert _metric_family_total("security_events") == before


def test_security_metrics_use_only_low_cardinality_labels() -> None:
    expected_labels = {
        "security_events": ("reason",),
        "format_mismatch_pairs": ("declared_format", "detected_format"),
        "validation_failures": ("format", "reason"),
    }

    for metric_name, labels in expected_labels.items():
        collector = REGISTRY._names_to_collectors[metric_name]
        assert collector._labelnames == labels
        assert "request_id" not in collector._labelnames
        assert "filename" not in collector._labelnames
