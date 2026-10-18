"""In-memory application state for the prototype backend."""

from .alerts import AlertDeduplicator
from .models import Alert, MatchResult, TranscriptEvent


current_watch_item: str | None = None
latest_transcript: TranscriptEvent | None = None
latest_match: MatchResult | None = None
latest_alert: Alert | None = None
latest_suppressed: bool = False
alert_deduplicator = AlertDeduplicator()


def set_watch_item(text: str) -> None:
    """Replace the current Wait For item."""

    global current_watch_item
    current_watch_item = text


def get_watch_item() -> str | None:
    """Return the current Wait For item, if one has been configured."""

    return current_watch_item


def record_evaluation(
    transcript: TranscriptEvent,
    match: MatchResult,
    alert: Alert | None,
    suppressed: bool,
) -> None:
    """Record the latest debug-pipeline evaluation for later inspection."""

    global latest_transcript, latest_match, latest_alert, latest_suppressed
    latest_transcript = transcript
    latest_match = match
    latest_alert = alert
    latest_suppressed = suppressed


def reset() -> None:
    """Clear process-local state, primarily for test isolation."""

    global current_watch_item, latest_transcript, latest_match, latest_alert
    global latest_suppressed
    current_watch_item = None
    latest_transcript = None
    latest_match = None
    latest_alert = None
    latest_suppressed = False
    alert_deduplicator.clear()

