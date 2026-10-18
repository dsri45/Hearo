"""Deterministic matcher tests, including deliberate near misses."""

import json
from pathlib import Path

import pytest

from backend.app.matcher import extract_watch_terms, match_transcript
from backend.app.models import (
    IntentIdentifier,
    SoundTarget,
    SpeechTarget,
    StructuredWatchIntent,
)


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


def test_structured_intent_identifier_alias_can_match() -> None:
    intent = StructuredWatchIntent(
        subject="coffee order",
        identifiers=[
            IntentIdentifier(value="47", aliases=["forty seven"], required=True)
        ],
        context_terms=["coffee", "order"],
        interesting_events=["ready", "called"],
        keyterms=["order 47", "forty seven"],
    )

    result = match_transcript(
        "My coffee order number 47",
        "Order forty seven!",
        intent,
    )

    assert result.match is True
    assert result.event_type == "called"


def test_structured_intent_adds_custom_event_phrase() -> None:
    intent = StructuredWatchIntent(
        subject="visitor badge",
        identifiers=[IntentIdentifier(value="B12", aliases=[], required=True)],
        context_terms=["badge"],
        interesting_events=["window opened"],
        keyterms=["badge B12"],
    )

    result = match_transcript(
        "Visitor badge B12",
        "Badge B12, the collection window opened.",
        intent,
    )

    assert result.match is True
    assert result.event_type == "window_opened"


def test_sound_only_intent_does_not_match_spoken_sound_label() -> None:
    intent = StructuredWatchIntent(
        subject="doorbell",
        context_terms=["doorbell"],
        interesting_events=["rings"],
        keyterms=["doorbell"],
        sound_targets=[SoundTarget(label="doorbell")],
    )

    result = match_transcript(
        "Tell me when the doorbell rings",
        "The doorbell rings",
        intent,
    )

    assert result.match is False
    assert result.reason == "This Watch Item only uses environmental sound detection."


@pytest.mark.parametrize(
    "transcript",
    [
        "Dana!",
        "Calling Dana!",
        "Paging Dana",
        "Is Dana here?",
        "We're looking for Dana",
        "Dana, please come to the front desk",
        "Can Dana come up?",
        "Where is Dana?",
        (
            "The app listens for several things. Hi, is Dana here? Dana? "
            "It can also listen for a gate change or boarding announcement."
        ),
    ],
)
def test_optional_name_call_identifier_matches_mixed_intent(transcript: str) -> None:
    intent = StructuredWatchIntent(
        subject="Dana being called or a doorbell",
        identifiers=[
            IntentIdentifier(value="Dana", aliases=["Dayna"], required=False)
        ],
        context_terms=["calls", "doorbell"],
        interesting_events=["called", "rings"],
        keyterms=["Dana", "Dayna", "doorbell"],
        speech_targets=[SpeechTarget(kind="name_call", terms=["Dana", "Dayna"])],
        sound_targets=[SoundTarget(label="doorbell")],
    )

    result = match_transcript(
        "Alert me if someone calls Dana or if the doorbell rings",
        transcript,
        intent,
    )

    assert result.match is True
    assert result.event_type == "called"


@pytest.mark.parametrize(
    "transcript",
    [
        "Dana is not here",
        "I talked to Dana yesterday",
        "The meeting about Dana is tomorrow",
        "I had lunch with Dana",
    ],
)
def test_background_name_mentions_do_not_match(transcript: str) -> None:
    intent = StructuredWatchIntent(
        subject="Dana being called",
        identifiers=[IntentIdentifier(value="Dana", required=False)],
        context_terms=["calls"],
        interesting_events=["called"],
        keyterms=["Dana"],
        speech_targets=[SpeechTarget(kind="name_call", terms=["Dana"])],
    )

    result = match_transcript("Someone calling Dana", transcript, intent)

    assert result.match is False

