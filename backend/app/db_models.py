"""SQLAlchemy persistence models. These are not external API contracts."""

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationStateRow(Base):
    __tablename__ = "application_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    watch_item: Mapped[str | None] = mapped_column(String(500), nullable=True)
    structured_intent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class ListeningSessionRow(Base):
    __tablename__ = "listening_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watch_item: Mapped[str] = mapped_column(String(500), nullable=False)
    started_at: Mapped[float] = mapped_column(Float, nullable=False)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_event_time", "event_id", "timestamp"),)

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("listening_sessions.id"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
