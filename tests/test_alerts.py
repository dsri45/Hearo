"""Tests for alert creation and duplicate suppression."""

import pytest

from backend.app.alerts import AlertDeduplicator, create_alert
from backend.app.models import MatchResult


def matching_result(detail: str = "Ready for pickup") -> MatchResult:
    return MatchResult(
        match=True,
        confidence=0.97,
        event_type="ready",
        title="Coffee order 47",
        detail=detail,
        reason="Identifier and context match.",
    )


def test_alert_id_is_stable_for_same_semantic_event() -> None:
    first = create_alert(matching_result(), timestamp=100.0)
    second = create_alert(matching_result(), timestamp=110.0)

    assert first.id == second.id
    assert first.timestamp != second.timestamp


def test_different_event_detail_gets_different_id() -> None:
    ready = create_alert(matching_result("Ready for pickup"), timestamp=100.0)
    counter = create_alert(matching_result("Proceed to counter 3"), timestamp=100.0)

    assert ready.id != counter.id


def test_cannot_create_alert_from_nonmatch() -> None:
    result = MatchResult(match=False, confidence=0.99, reason="Wrong number.")

    with pytest.raises(ValueError, match="nonmatching"):
        create_alert(result)


def test_duplicate_is_suppressed_inside_window() -> None:
    deduplicator = AlertDeduplicator(window_seconds=60)
    first = create_alert(matching_result(), timestamp=100.0)
    repeated = create_alert(matching_result(), timestamp=159.0)

    assert deduplicator.check_and_record(first) is False
    assert deduplicator.check_and_record(repeated) is True


def test_event_is_allowed_after_window() -> None:
    deduplicator = AlertDeduplicator(window_seconds=60)
    first = create_alert(matching_result(), timestamp=100.0)
    later = create_alert(matching_result(), timestamp=160.0)

    assert deduplicator.check_and_record(first) is False
    assert deduplicator.check_and_record(later) is False
