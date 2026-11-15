import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import (
    Base,
    AuditEvent,
    create_database_engine,
    create_session_factory,
    get_session,
)
from app.metrics import record_event
from app.schemas import AuditEventCreate, AuditEventCreated, AuditEventRead


SERVICE_NAME = os.getenv("SERVICE_NAME", "audit")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = os.environ["DATABASE_URL"]
    engine = create_database_engine(database_url)

    for attempt in range(1, 11):
        try:
            await run_in_threadpool(Base.metadata.create_all, engine)
            break
        except SQLAlchemyError:
            if attempt == 10:
                engine.dispose()
                raise
            logger.warning("PostgreSQL is not ready; retrying schema initialization")
            await asyncio.sleep(1)

    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    yield
    engine.dispose()


# Preserve the pre-upgrade API behavior for JSON clients omitting Content-Type.
app = FastAPI(
    title=f"{SERVICE_NAME} service", lifespan=lifespan, strict_content_type=False
)


@app.get("/health", tags=["system"], response_model=None)
def health(session: Session = Depends(get_session)) -> dict[str, str] | JSONResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "DOWN",
                "service": SERVICE_NAME,
                "database": "DOWN",
            },
        )

    return {"status": "UP", "service": SERVICE_NAME, "database": "UP"}


@app.post(
    "/audit/event",
    response_model=AuditEventCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_audit_event(
    payload: AuditEventCreate,
    session: Session = Depends(get_session),
) -> AuditEventCreated:
    event = AuditEvent(**payload.model_dump())

    try:
        session.add(event)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit database is unavailable",
        ) from error

    record_event(
        decision=event.decision,
        declared_format=event.declared_format,
        detected_format=event.detected_format,
        validation_status=event.validation_status,
        reason=event.reason,
        duration_ms=event.duration_ms,
    )
    return AuditEventCreated(id=event.id, request_id=event.request_id, stored=True)


@app.get("/events", response_model=list[AuditEventRead])
def list_events(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    session: Session = Depends(get_session),
) -> list[AuditEvent]:
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    return list(session.scalars(statement))


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
