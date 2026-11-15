from prometheus_client import Counter, Histogram


SECURITY_EVENT_REASONS = frozenset(
    {
        "FORMAT_MISMATCH",
        "METADATA_MISMATCH",
        "FORMAT_NOT_DETECTED",
        "INVALID_JSON",
        "INVALID_XML",
        "INVALID_CSV",
        "UNSUPPORTED_FORMAT",
        "EMPTY_FILE",
    }
)
FORMAT_LABEL_VALUES = frozenset({"JSON", "XML", "CSV", "UNKNOWN"})


accepted_objects_total = Counter(
    "accepted_objects_total",
    "Number of accepted objects recorded by audit",
)
quarantined_objects_total = Counter(
    "quarantined_objects_total",
    "Number of quarantined objects recorded by audit",
)
rejected_objects_total = Counter(
    "rejected_objects_total",
    "Number of rejected objects recorded by audit",
)
format_mismatch_total = Counter(
    "format_mismatch_total",
    "Number of quarantined objects with a format mismatch",
)
validation_errors_total = Counter(
    "validation_errors_total",
    "Number of rejected objects with invalid validation status",
)
inspection_duration_seconds = Histogram(
    "inspection_duration_seconds",
    "End-to-end inspection duration in seconds",
)
security_events_total = Counter(
    "security_events_total",
    "Number of security-relevant audit events by allowlisted reason",
    ("reason",),
)
format_mismatch_pairs_total = Counter(
    "format_mismatch_pairs_total",
    "Number of declared and detected format mismatch pairs",
    ("declared_format", "detected_format"),
)
validation_failures_total = Counter(
    "validation_failures_total",
    "Number of invalid structural validations by format and reason",
    ("format", "reason"),
)


def _format_label(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    return normalized if normalized in FORMAT_LABEL_VALUES else "UNKNOWN"


def record_event(
    *,
    decision: str,
    declared_format: str | None,
    detected_format: str | None,
    validation_status: str | None,
    reason: str | None,
    duration_ms: float,
) -> None:
    if decision == "ACCEPT":
        accepted_objects_total.inc()
    elif decision == "QUARANTINE":
        quarantined_objects_total.inc()
        if reason == "FORMAT_MISMATCH":
            format_mismatch_total.inc()
    elif decision == "REJECT":
        rejected_objects_total.inc()
        if validation_status == "INVALID":
            validation_errors_total.inc()

    if reason in SECURITY_EVENT_REASONS:
        security_events_total.labels(reason=reason).inc()

    if reason == "FORMAT_MISMATCH":
        format_mismatch_pairs_total.labels(
            declared_format=_format_label(declared_format),
            detected_format=_format_label(detected_format),
        ).inc()

    if validation_status == "INVALID" and reason in SECURITY_EVENT_REASONS:
        validation_failures_total.labels(
            format=_format_label(detected_format),
            reason=reason,
        ).inc()

    inspection_duration_seconds.observe(duration_ms / 1000)
