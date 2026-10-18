"""Tests for the shared backend/iPhone/Watch message contracts."""

import pytest
from pydantic import ValidationError

from backend.app.models import (
    Alert,
    MatchResult,
    TranscriptEvent,
    WatchItem,
    WatchMessage,
)


def test_watch_item_trims_whitespace() -> None:
    item = WatchItem(text="  Flight 312 to Buffalo  ")

    assert item.model_dump() == {"text": "Flight 312 to Buffalo"}


@pytest.mark.parametrize("text", ["", "   "])
def test_watch_item_rejects_empty_text(text: str) -> None:
    with pytest.raises(ValidationError):
        WatchItem(text=text)


def test_transcript_event_accepts_valid_time_range() -> None:
    event = TranscriptEvent(text="Flight 312 is boarding.", start=1.2, end=4.8)

    assert event.start == 1.2
    assert event.end == 4.8


def test_transcript_event_rejects_reversed_time_range() -> None:
    with pytest.raises(ValidationError, match="end must be"):
        TranscriptEvent(text="Flight 312 is boarding.", start=4.8, end=1.2)


def test_nonmatch_can_omit_alert_content() -> None:
    result = MatchResult(
        match=False,
        confidence=0.99,
        reason="Flight number and destination do not match.",
    )

    assert result.title is None


def test_match_requires_event_type_title_and_detail() -> None:
    with pytest.raises(ValidationError, match="matched results require"):
        MatchResult(match=True, confidence=0.98)


def test_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        MatchResult(match=False, confidence=1.01)


def test_alert_serializes_to_stable_json_shape() -> None:
    alert = Alert(
        id="flight-312-gate-c12",
        category="watchlist_match",
        title="Flight 312",
        detail="Gate changed to C12",
        confidence=0.98,
        timestamp=1_797_891_200.0,
    )

    assert alert.model_dump(mode="json") == {
        "id": "flight-312-gate-c12",
        "category": "watchlist_match",
        "title": "Flight 312",
        "detail": "Gate changed to C12",
        "confidence": 0.98,
        "timestamp": 1_797_891_200.0,
    }


def test_watch_message_uses_known_haptic_type() -> None:
    message = WatchMessage(
        haptic="urgent",
        title="Smoke alarm",
        detail="A smoke alarm may be sounding nearby.",
    )

    assert message.model_dump(mode="json")["haptic"] == "urgent"


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WatchItem(text="Order 47", unexpected=True)
