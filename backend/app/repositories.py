"""Persistence operations used by the application layer."""

import time
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .database import create_database_engine
from .db_models import (
    AlertRow,
    ApplicationStateRow,
    Base,
    ListeningSessionRow,
    RestEventRow,
    RestSessionRow,
)
from .models import (
    Alert,
    AlertCategory,
    RestEventType,
    RestModeEvent,
    RestModeRecap,
    RestModeSession,
)


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

    def start_rest_session(
        self,
        watch_item: str | None,
        started_at: float | None = None,
    ) -> RestModeSession:
        session_id = str(uuid.uuid4())
        timestamp = time.time() if started_at is None else started_at
        with self._sessions.begin() as session:
            active = session.scalar(
                select(RestSessionRow).where(RestSessionRow.status == "active").limit(1)
            )
            if active is not None:
                raise ValueError("Rest Mode is already active")
            session.add(
                RestSessionRow(
                    id=session_id,
                    watch_item=watch_item,
                    started_at=timestamp,
                    ended_at=None,
                    status="active",
                    recap=None,
                )
            )
        return RestModeSession(
            id=session_id,
            watch_item=watch_item,
            started_at=timestamp,
            status="active",
        )

    def get_active_rest_session(self) -> RestModeSession | None:
        with self._sessions() as session:
            row = session.scalar(
                select(RestSessionRow)
                .where(RestSessionRow.status == "active")
                .order_by(RestSessionRow.started_at.desc())
                .limit(1)
            )
            return None if row is None else self._rest_session_contract(session, row)

    def get_latest_rest_session(self) -> RestModeSession | None:
        with self._sessions() as session:
            row = session.scalar(
                select(RestSessionRow)
                .order_by(RestSessionRow.started_at.desc())
                .limit(1)
            )
            return None if row is None else self._rest_session_contract(session, row)

    def add_rest_event(
        self,
        session_id: str,
        event_type: RestEventType,
        text: str,
        *,
        timestamp: float | None = None,
        confidence: float | None = None,
        important: bool = False,
        source: str = "unknown",
    ) -> RestModeEvent:
        with self._sessions.begin() as session:
            parent = session.get(RestSessionRow, session_id)
            if parent is None or parent.status != "active":
                raise ValueError("Rest Mode session is not active")
            row = RestEventRow(
                session_id=session_id,
                event_type=(
                    event_type.value
                    if isinstance(event_type, RestEventType)
                    else str(event_type)
                ),
                text=text,
                timestamp=time.time() if timestamp is None else timestamp,
                confidence=confidence,
                important=important,
                source=source,
            )
            session.add(row)
            session.flush()
            return self._rest_event_contract(row)

    def list_rest_events(self, session_id: str) -> list[RestModeEvent]:
        with self._sessions() as session:
            rows = session.scalars(
                select(RestEventRow)
                .where(RestEventRow.session_id == session_id)
                .order_by(RestEventRow.timestamp, RestEventRow.sequence)
            ).all()
            return [self._rest_event_contract(row) for row in rows]

    def finish_rest_session(
        self,
        session_id: str,
        recap: RestModeRecap,
        ended_at: float | None = None,
        *,
        purge_events: bool = True,
    ) -> RestModeSession | None:
        with self._sessions.begin() as session:
            row = session.get(RestSessionRow, session_id)
            if row is None or row.status != "active":
                return None
            event_count = len(
                session.scalars(
                    select(RestEventRow.sequence).where(
                        RestEventRow.session_id == session_id
                    )
                ).all()
            )
            row.ended_at = time.time() if ended_at is None else ended_at
            row.status = "ended"
            row.recap = recap.model_dump(mode="json")
            result = RestModeSession(
                id=row.id,
                watch_item=row.watch_item,
                started_at=row.started_at,
                ended_at=row.ended_at,
                status="ended",
                event_count=event_count,
                recap=recap,
            )
            if purge_events:
                session.execute(
                    delete(RestEventRow).where(RestEventRow.session_id == session_id)
                )
            return result

    @staticmethod
    def _rest_event_contract(row: RestEventRow) -> RestModeEvent:
        return RestModeEvent(
            sequence=row.sequence,
            session_id=row.session_id,
            event_type=RestEventType(row.event_type),
            text=row.text,
            timestamp=row.timestamp,
            confidence=row.confidence,
            important=row.important,
            source=row.source,
        )

    @staticmethod
    def _rest_session_contract(session: Any, row: RestSessionRow) -> RestModeSession:
        event_count = len(
            session.scalars(
                select(RestEventRow.sequence).where(RestEventRow.session_id == row.id)
            ).all()
        )
        return RestModeSession(
            id=row.id,
            watch_item=row.watch_item,
            started_at=row.started_at,
            ended_at=row.ended_at,
            status=row.status,
            event_count=event_count,
            recap=None if row.recap is None else RestModeRecap.model_validate(row.recap),
        )

    def clear_all(self) -> None:
        """Delete persisted test data. Production request handlers never call this."""

        with self._sessions.begin() as session:
            session.execute(delete(RestEventRow))
            session.execute(delete(RestSessionRow))
            session.execute(delete(AlertRow))
            session.execute(delete(ListeningSessionRow))
            session.execute(delete(ApplicationStateRow))
