"""Alert construction and short-window duplicate suppression."""

import hashlib
import time

from .models import Alert, AlertCategory, MatchResult


DEFAULT_DEDUPE_WINDOW_SECONDS = 60.0


def _event_id(result: MatchResult) -> str:
    """Return a stable ID for the semantic event represented by a match."""

    canonical = "|".join(
        (
            (result.event_type or "").strip().lower(),
            (result.title or "").strip().lower(),
            (result.detail or "").strip().lower(),
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"event-{digest}"


def create_alert(result: MatchResult, timestamp: float | None = None) -> Alert:
    """Convert a positive MatchResult into an Alert for the iPhone."""

    if not result.match:
        raise ValueError("cannot create an alert from a nonmatching result")

    # MatchResult validation guarantees these values for a positive match.
    assert result.title is not None
    assert result.detail is not None

    return Alert(
        id=_event_id(result),
        category=AlertCategory.WATCHLIST_MATCH,
        title=result.title,
        detail=result.detail,
        confidence=result.confidence,
        timestamp=time.time() if timestamp is None else timestamp,
    )


class AlertDeduplicator:
    """Suppress identical semantic events within a bounded time window."""

    def __init__(self, window_seconds: float = DEFAULT_DEDUPE_WINDOW_SECONDS) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds
        self._last_seen: dict[str, float] = {}

    def check_and_record(self, alert: Alert) -> bool:
        """Return True when the alert is a recent duplicate, then record it."""

        cutoff = alert.timestamp - self.window_seconds
        self._last_seen = {
            event_id: seen_at
            for event_id, seen_at in self._last_seen.items()
            if seen_at > cutoff
        }

        previous = self._last_seen.get(alert.id)
        suppressed = previous is not None and alert.timestamp - previous < self.window_seconds
        self._last_seen[alert.id] = alert.timestamp
        return suppressed

    def clear(self) -> None:
        self._last_seen.clear()

