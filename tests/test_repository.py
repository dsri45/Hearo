"""Tests for SQLite persistence and repository behavior."""

from pathlib import Path

from backend.app.models import Alert, RestModeRecap
from backend.app.repositories import HearoRepository


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def test_watch_item_survives_repository_recreation(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "persistence.db")
    first = HearoRepository(url)
    first.set_watch_item(
        "Train A215 to Seattle",
        structured_intent={"identifiers": ["A215"], "keywords": ["Seattle"]},
        updated_at=100.0,
    )

    reopened = HearoRepository(url)

    assert reopened.get_watch_item() == "Train A215 to Seattle"
    assert reopened.get_structured_intent() == {
        "identifiers": ["A215"],
        "keywords": ["Seattle"],
    }


def test_alert_history_supports_persistent_deduplication(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "alerts.db")
    first = HearoRepository(url)
    alert = Alert(
        id="event-order-47",
        category="watchlist_match",
        title="Order 47",
        detail="Name or number announced",
        confidence=0.95,
        timestamp=100.0,
    )
    first.save_alert(alert)

    reopened = HearoRepository(url)

    assert reopened.was_alert_emitted_recently("event-order-47", 159.0, 60.0)
    assert not reopened.was_alert_emitted_recently("event-order-47", 160.0, 60.0)
    assert reopened.list_alerts() == [alert]


def test_listening_session_lifecycle(tmp_path: Path) -> None:
    repository = HearoRepository(sqlite_url(tmp_path / "sessions.db"))

    session_id = repository.start_session("Appointment with Dr Patel", started_at=50.0)

    assert repository.end_session(session_id, ended_at=80.0) is True
    assert repository.end_session("missing-session", ended_at=80.0) is False


def test_rest_mode_persists_recap_and_purges_temporary_events(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "rest-mode.db")
    repository = HearoRepository(url)
    rest = repository.start_rest_session("Someone calling Dana", started_at=10.0)
    repository.add_rest_event(
        rest.id,
        "transcript",
        "Is Dana here?",
        timestamp=12.0,
        important=True,
        source="deepgram",
    )
    recap = RestModeRecap(
        title="What you missed",
        overview="Dana was asked for.",
        important_events=["Someone asked for Dana."],
        conversations_and_announcements=["Someone asked whether Dana was here."],
        environmental_sounds=[],
        provider="gemini",
    )

    completed = repository.finish_rest_session(
        rest.id,
        recap,
        ended_at=20.0,
    )
    reopened = HearoRepository(url)

    assert completed is not None
    assert completed.event_count == 1
    assert reopened.list_rest_events(rest.id) == []
    latest = reopened.get_latest_rest_session()
    assert latest is not None
    assert latest.id == completed.id
    assert latest.status == "ended"
    assert latest.recap == completed.recap
