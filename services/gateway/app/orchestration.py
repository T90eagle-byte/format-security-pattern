import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError


Decision = Literal["ACCEPT", "QUARANTINE", "REJECT"]


class GatewayConfigurationError(RuntimeError):
    pass


class DownstreamServiceError(RuntimeError):
    def __init__(self, service: str, detail: str = "unavailable") -> None:
        super().__init__(f"{service}: {detail}")
        self.public_message = f"{service.capitalize()} service is unavailable"


@dataclass(frozen=True)
class ServiceUrls:
    inspector: str
    validator: str
    storage: str
    audit: str

    @classmethod
    def from_environment(cls) -> "ServiceUrls":
        values: dict[str, str] = {}
        for field_name, environment_name in (
            ("inspector", "INSPECTOR_URL"),
            ("validator", "VALIDATOR_URL"),
            ("storage", "STORAGE_URL"),
            ("audit", "AUDIT_URL"),
        ):
            value = os.getenv(environment_name, "").strip().rstrip("/")
            if not value:
                raise GatewayConfigurationError(environment_name)
            values[field_name] = value
        return cls(**values)


class InspectionResult(BaseModel):
    request_id: str
    filename: str
    declared_format: str
    detected_format: str
    match: bool
    reason: str | None


class ValidationResult(BaseModel):
    request_id: str
    format: str
    status: Literal["VALID", "INVALID", "UNSUPPORTED"]
    reason: str | None


class StorageResult(BaseModel):
    request_id: str
    decision: Decision
    stored: bool
    bucket: str | None
    object_name: str | None


class AuditResult(BaseModel):
    request_id: str
    stored: bool


async def _post_model(
    client: httpx.AsyncClient,
    *,
    service: str,
    url: str,
    response_model: type[BaseModel],
    **kwargs: Any,
) -> BaseModel:
    try:
        response = await client.post(url, **kwargs)
        response.raise_for_status()
        return response_model.model_validate(response.json())
    except (httpx.TimeoutException, httpx.RequestError) as error:
        raise DownstreamServiceError(service) from error
    except (httpx.HTTPStatusError, ValueError, ValidationError) as error:
        raise DownstreamServiceError(service, "invalid response") from error


def _file_part(
    filename: str,
    data: bytes,
    content_type: str,
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, data, content_type)}


async def call_inspector(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    request_id: str,
) -> InspectionResult:
    result = await _post_model(
        client,
        service="inspector",
        url=f"{base_url}/inspect",
        response_model=InspectionResult,
        files=_file_part(filename, data, content_type),
        data={"request_id": request_id},
    )
    assert isinstance(result, InspectionResult)
    if result.request_id != request_id:
        raise DownstreamServiceError("inspector", "mismatched request id")
    return result


def decision_from_inspection(
    inspection: InspectionResult,
) -> tuple[Decision, str] | None:
    if inspection.reason in {"FORMAT_MISMATCH", "METADATA_MISMATCH"}:
        return "QUARANTINE", inspection.reason
    if inspection.reason == "FORMAT_NOT_DETECTED":
        return "REJECT", inspection.reason
    if inspection.match:
        return None
    raise DownstreamServiceError("inspector", "inconsistent decision data")


async def call_validator(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    request_id: str,
    detected_format: str,
) -> ValidationResult:
    result = await _post_model(
        client,
        service="validator",
        url=f"{base_url}/validate",
        response_model=ValidationResult,
        files=_file_part(filename, data, content_type),
        data={
            "request_id": request_id,
            "detected_format": detected_format,
        },
    )
    assert isinstance(result, ValidationResult)
    if result.request_id != request_id:
        raise DownstreamServiceError("validator", "mismatched request id")
    return result


def decision_from_validation(
    validation: ValidationResult,
) -> tuple[Decision, str | None]:
    if validation.status == "VALID":
        return "ACCEPT", None
    if validation.status == "UNSUPPORTED":
        return "REJECT", "UNSUPPORTED_FORMAT"
    if validation.reason:
        return "REJECT", validation.reason
    raise DownstreamServiceError("validator", "missing invalid reason")


async def call_storage(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    request_id: str,
    detected_format: str,
    decision: Decision,
    reason: str | None,
) -> StorageResult:
    form_data = {
        "request_id": request_id,
        "decision": decision,
        "detected_format": detected_format,
    }
    if reason is not None:
        form_data["reason"] = reason

    result = await _post_model(
        client,
        service="storage",
        url=f"{base_url}/store",
        response_model=StorageResult,
        files=_file_part(filename, data, content_type),
        data=form_data,
    )
    assert isinstance(result, StorageResult)
    if result.request_id != request_id or result.decision != decision:
        raise DownstreamServiceError("storage", "inconsistent result")
    return result


async def call_audit(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    payload: dict[str, Any],
) -> bool:
    try:
        result = await _post_model(
            client,
            service="audit",
            url=f"{base_url}/audit/event",
            response_model=AuditResult,
            json=payload,
        )
    except DownstreamServiceError:
        return False

    assert isinstance(result, AuditResult)
    return result.request_id == payload["request_id"] and result.stored
