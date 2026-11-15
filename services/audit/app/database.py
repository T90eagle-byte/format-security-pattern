from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

from fastapi import Request
from sqlalchemy import DateTime, Float, Index, Integer, String, create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_request_id", "request_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    declared_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)


def create_database_engine(database_url: str) -> Engine:
    options: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        options["connect_args"] = {"connect_timeout": 5}
    return create_engine(database_url, **options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session
