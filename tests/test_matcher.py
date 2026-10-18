"""Deterministic matcher tests, including deliberate near misses."""

import json
from pathlib import Path

import pytest

from backend.app.matcher import extract_watch_terms, match_transcript


CASES_PATH = Path(__file__).with_name("cases.json")
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_recorded_case_expectations(case: dict[str, object]) -> None:
    result = match_transcript(
        watch_item=str(case["watch_item"]),
        transcript=str(case["transcript"]),
    )

    assert result.match is case["expected"], result.reason
    if expected_event_type := case.get("event_type"):
        assert result.event_type == expected_event_type
        assert result.title
        assert result.detail


def test_matching_destination_increases_confidence() -> None:
    with_destination = match_transcript(
        "Flight 312 to Buffalo",
        "Flight 312 to Buffalo is now boarding.",
    )
    without_destination = match_transcript(
        "Flight 312 to Buffalo",
        "Flight 312 is now boarding.",
    )

    assert with_destination.confidence > without_destination.confidence


def test_gate_change_extracts_gate_name() -> None:
    result = match_transcript(
        "Flight 312 to Buffalo",
        "Flight 312 to Buffalo has moved to gate c12.",
    )

    assert result.match is True
    assert result.detail == "Gate changed to C12"


def test_empty_watch_item_never_matches() -> None:
    result = match_transcript("   ", "Order 47 is ready for pickup.")

    assert result.match is False
    assert result.confidence == 1.0


def test_empty_transcript_never_matches() -> None:
    result = match_transcript("Order 47", "   ")

    assert result.match is False
    assert result.confidence == 1.0


def test_extracts_identifiers_and_supporting_keywords() -> None:
    terms = extract_watch_terms("I'm waiting for my coffee order number 47")

    assert terms.identifiers == ["47"]
    assert terms.keywords == ["coffee", "order"]


def test_alphanumeric_identifier_requires_exact_token() -> None:
    result = match_transcript(
        "Train A215 to Seattle",
        "Train A251 to Seattle is now boarding.",
    )

    assert result.match is False
    assert "a215" in (result.reason or "")


def test_long_non_event_mention_is_not_treated_as_direct_call() -> None:
    result = match_transcript(
        "My coffee order number 47",
        "I placed coffee order 47 before walking over to the pickup counter.",
    )

    assert result.match is False
    assert result.reason == "No relevant event was announced."

