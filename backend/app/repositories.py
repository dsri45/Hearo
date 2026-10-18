"""Persistence operations used by the application layer."""

import time
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .database import create_database_engine
from .db_models import AlertRow, ApplicationStateRow, Base, ListeningSessionRow
from .models import Alert, AlertCategory


class HearoRepository:
    """Small repository that isolates API code from SQLite details."""

    def __init__(self, database_url: str | None = None, engine: Engine | None = None) -> None:
        self.engine = engine or create_database_engine(database_url)
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def set_watch_item(
        self,
        text: str,
        structured_intent: dict[str, Any] | None = None,
        updated_at: float | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(ApplicationStateRow, 1)
            if row is None:
                row = ApplicationStateRow(
                    id=1,
                    watch_item=text,
                    structured_intent=structured_intent,
                    updated_at=time.time() if updated_at is None else updated_at,
                )
                session.add(row)
            else:
                row.watch_item = text
                row.structured_intent = structured_intent
                row.updated_at = time.time() if updated_at is None else updated_at

    def get_watch_item(self) -> str | None:
        with self._sessions() as session:
            row = session.get(ApplicationStateRow, 1)
            return None if row is None else row.watch_item

    def get_structured_intent(self) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.get(ApplicationStateRow, 1)
            return None if row is None else row.structured_intent

    def start_session(self, watch_item: str, started_at: float | None = None) -> str:
        session_id = str(uuid.uuid4())
        with self._sessions.begin() as session:
            session.add(
                ListeningSessionRow(
                    id=session_id,
                    watch_item=watch_item,
                    started_at=time.time() if started_at is None else started_at,
                    ended_at=None,
                    status="active",
                )
            )
        return session_id

    def end_session(self, session_id: str, ended_at: float | None = None) -> bool:
        with self._sessions.begin() as session:
            row = session.get(ListeningSessionRow, session_id)
            if row is None:
                return False
            row.ended_at = time.time() if ended_at is None else ended_at
            row.status = "ended"
            return True

    def save_alert(self, alert: Alert, session_id: str | None = None) -> None:
        with self._sessions.begin() as session:
            session.add(
                AlertRow(
                    event_id=alert.id,
                    session_id=session_id,
                    category=str(alert.category),
                    title=alert.title,
                    detail=alert.detail,
                    confidence=alert.confidence,
                    timestamp=alert.timestamp,
                )
            )

    def was_alert_emitted_recently(
        self,
        event_id: str,
        timestamp: float,
        window_seconds: float,
    ) -> bool:
        cutoff = timestamp - window_seconds
        with self._sessions() as session:
            statement = select(AlertRow.sequence).where(
                AlertRow.event_id == event_id,
                AlertRow.timestamp > cutoff,
                AlertRow.timestamp <= timestamp,
            )
            return session.scalar(statement.limit(1)) is not None

    def list_alerts(self, limit: int = 100) -> list[Alert]:
        with self._sessions() as session:
            rows = session.scalars(
                select(AlertRow).order_by(AlertRow.timestamp.desc()).limit(limit)
            ).all()
            return [
                Alert(
                    id=row.event_id,
                    category=AlertCategory(row.category),
                    title=row.title,
                    detail=row.detail,
                    confidence=row.confidence,
                    timestamp=row.timestamp,
                )
                for row in rows
            ]

    def clear_all(self) -> None:
        """Delete persisted test data. Production request handlers never call this."""

        with self._sessions.begin() as session:
            session.execute(delete(AlertRow))
            session.execute(delete(ListeningSessionRow))
            session.execute(delete(ApplicationStateRow))
