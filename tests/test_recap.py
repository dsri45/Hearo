"""Tests for Rest Mode recap generation."""

import json
from types import SimpleNamespace

from backend.app.models import RestModeEvent
from backend.app.recap import GeminiRecapGenerator, local_recap


def event(kind: str, text: str, important: bool = False) -> RestModeEvent:
    return RestModeEvent(
        sequence=1,
        session_id="session-1",
        event_type=kind,
        text=text,
        timestamp=10.0,
        important=important,
        source="test",
    )


def test_local_recap_keeps_broad_context_and_priority_events() -> None:
    recap = local_recap(
        [
            event("transcript", "Lunch is in the kitchen."),
            event("transcript", "Dana, your ride is here.", important=True),
            event("sound", "doorbell"),
        ]
    )

    assert recap.important_events == ["Dana, your ride is here."]
    assert recap.conversations_and_announcements == [
        "Lunch is in the kitchen.",
        "Dana, your ride is here.",
    ]
    assert recap.environmental_sounds == ["doorbell"]


def test_gemini_recap_uses_structured_output() -> None:
    output = {
        "title": "While you rested",
        "overview": "Your ride arrived.",
        "important_events": ["Someone said Dana's ride was here."],
        "conversations_and_announcements": ["Lunch was mentioned."],
        "environmental_sounds": ["A doorbell was detected."],
    }
    captured: dict[str, object] = {}

    class Interactions:
        def create(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(output_text=json.dumps(output))

    class Client:
        interactions = Interactions()

        def close(self) -> None:
            captured["closed"] = True

    generator = GeminiRecapGenerator(
        "test-key",
        client_factory=lambda **_: Client(),
    )
    recap = generator.generate([event("transcript", "Dana, your ride is here.")])

    assert recap.provider == "gemini"
    assert recap.overview == "Your ride arrived."
    assert captured["response_format"]["mime_type"] == "application/json"
    assert captured["closed"] is True
