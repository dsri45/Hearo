"""Persistent application state plus ephemeral debug-pipeline state."""

from typing import Any

from .alerts import DEFAULT_DEDUPE_WINDOW_SECONDS
from .models import (
    Alert,
    DetectedSoundEvent,
    MatchResult,
    StructuredWatchIntent,
    TranscriptEvent,
)
from .repositories import HearoRepository


_repository = HearoRepository()

# These fields exist only to render the latest dashboard evaluation. Raw
# transcripts are intentionally not written to SQLite.
latest_transcript: TranscriptEvent | None = None
latest_sound_event: DetectedSoundEvent | None = None
latest_match: MatchResult | None = None
latest_alert: Alert | None = None
latest_suppressed: bool = False


def configure_repository(repository: HearoRepository) -> None:
    """Replace the repository, primarily to isolate automated tests."""

    global _repository
    _repository = repository


def set_watch_item(
    text: str,
    structured_intent: dict[str, Any] | None = None,
) -> None:
    """Persist the current Wait For item and its future structured intent."""

    _repository.set_watch_item(text, structured_intent)


def get_watch_item() -> str | None:
    """Return the persisted Wait For item, if one has been configured."""

    return _repository.get_watch_item()


def get_structured_intent() -> dict[str, Any] | None:
    return _repository.get_structured_intent()


def get_watch_intent() -> StructuredWatchIntent | None:
    stored = get_structured_intent()
    return None if stored is None else StructuredWatchIntent.model_validate(stored)


def is_duplicate_alert(alert: Alert) -> bool:
    """Check persisted alert history for a recent duplicate."""

    return _repository.was_alert_emitted_recently(
        event_id=alert.id,
        timestamp=alert.timestamp,
        window_seconds=DEFAULT_DEDUPE_WINDOW_SECONDS,
    )


def save_alert(alert: Alert) -> None:
    _repository.save_alert(alert)


def list_alerts(limit: int = 100) -> list[Alert]:
    return _repository.list_alerts(limit)


def record_evaluation(
    transcript: TranscriptEvent,
    match: MatchResult,
    alert: Alert | None,
    suppressed: bool,
) -> None:
    """Record the latest debug-pipeline evaluation for later inspection."""

    global latest_transcript, latest_sound_event, latest_match, latest_alert
    global latest_suppressed
    latest_transcript = transcript
    latest_sound_event = None
    latest_match = match
    latest_alert = alert
    latest_suppressed = suppressed


def record_sound_evaluation(
    event: DetectedSoundEvent,
    match: MatchResult,
    alert: Alert | None,
    suppressed: bool,
) -> None:
    """Record the latest environmental-sound evaluation for the dashboard."""

    global latest_transcript, latest_sound_event, latest_match, latest_alert
    global latest_suppressed
    latest_transcript = None
    latest_sound_event = event
    latest_match = match
    latest_alert = alert
    latest_suppressed = suppressed


def reset() -> None:
    """Clear the configured repository and ephemeral state for tests."""

    global latest_transcript, latest_sound_event, latest_match, latest_alert
    global latest_suppressed
    _repository.clear_all()
    latest_transcript = None
    latest_sound_event = None
    latest_match = None
    latest_alert = None
    latest_suppressed = False
