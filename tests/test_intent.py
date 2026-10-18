"""Tests for Gemini structured intent and the offline fallback."""

import json
from types import SimpleNamespace

from backend.app.intent import GeminiIntentExtractor, local_intent


class FakeInteractions:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.request: dict[str, object] | None = None

    def create(self, **request: object) -> SimpleNamespace:
        self.request = request
        return SimpleNamespace(output_text=json.dumps(self.output))


class FakeClient:
    def __init__(self, output: dict[str, object]) -> None:
        self.interactions = FakeInteractions(output)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_gemini_extractor_parses_strict_structured_output() -> None:
    output = {
        "subject": "coffee order",
        "identifiers": [
            {"value": "47", "aliases": ["forty-seven"], "required": True}
        ],
        "context_terms": ["coffee", "order", "oat latte"],
        "interesting_events": ["ready", "called"],
        "keyterms": ["order 47", "forty-seven", "oat latte"],
        "speech_targets": [
            {"kind": "spoken_announcement", "terms": ["order 47", "oat latte"]}
        ],
        "sound_targets": [],
    }
    fake_client = FakeClient(output)
    extractor = GeminiIntentExtractor(
        api_key="test-key",
        client_factory=lambda **_: fake_client,
    )

    intent = extractor.extract("My oat latte, order 47")

    assert intent.identifiers[0].aliases == ["forty-seven"]
    assert fake_client.interactions.request is not None
    assert fake_client.interactions.request["model"] == "gemini-3.5-flash-lite"
    assert "My oat latte, order 47" in str(
        fake_client.interactions.request["input"]
    )
    response_format = fake_client.interactions.request["response_format"]
    assert isinstance(response_format, dict)
    schema = response_format["schema"]
    assert "$defs" not in schema
    assert schema["required"] == [
        "subject",
        "identifiers",
        "context_terms",
        "interesting_events",
        "keyterms",
        "speech_targets",
        "sound_targets",
    ]
    assert fake_client.closed is True


def test_local_intent_preserves_deterministic_fallback() -> None:
    intent = local_intent("Train A215 to Seattle")

    assert intent.subject == "Train A215 to Seattle"
    assert intent.identifiers[0].value == "a215"
    assert "train" in intent.context_terms
    assert "a215" in intent.keyterms


def test_local_intent_extracts_environmental_sounds() -> None:
    intent = local_intent("Alert me when the doorbell or a siren sounds")

    assert [target.label for target in intent.sound_targets] == ["doorbell", "siren"]
    assert intent.speech_targets == []
