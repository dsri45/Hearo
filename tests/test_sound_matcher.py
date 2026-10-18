"""Tests for deterministic environmental-sound matching."""

from backend.app.models import DetectedSoundEvent, SoundTarget, StructuredWatchIntent
from backend.app.sound_matcher import match_sound_event, normalize_sound_label


def _intent(*labels: str) -> StructuredWatchIntent:
    return StructuredWatchIntent(
        subject="environmental sound",
        sound_targets=[SoundTarget(label=label) for label in labels],
    )


def test_normalizes_common_classifier_aliases() -> None:
    assert normalize_sound_label("Door Bell") == "doorbell"
    assert normalize_sound_label("emergency_siren") == "siren"
    assert normalize_sound_label("dog-barking") == "dog_bark"


def test_matches_requested_sound() -> None:
    result = match_sound_event(
        "Tell me when the doorbell rings",
        DetectedSoundEvent(label="door chime", confidence=0.88),
        _intent("doorbell"),
    )

    assert result.match is True
    assert result.event_type == "sound_doorbell"
    assert result.confidence == 0.88


def test_rejects_sound_not_requested() -> None:
    result = match_sound_event(
        "Tell me when the doorbell rings",
        DetectedSoundEvent(label="siren", confidence=0.97),
        _intent("doorbell"),
    )

    assert result.match is False
    assert "waiting for: doorbell" in (result.reason or "")


def test_rejects_low_confidence_sound() -> None:
    result = match_sound_event(
        "Tell me when the doorbell rings",
        DetectedSoundEvent(label="doorbell", confidence=0.49),
        _intent("doorbell"),
    )

    assert result.match is False
    assert "50%" in (result.reason or "")


def test_accepts_sound_at_confidence_threshold() -> None:
    result = match_sound_event(
        "Tell me when the doorbell rings",
        DetectedSoundEvent(label="doorbell", confidence=0.50),
        _intent("doorbell"),
    )

    assert result.match is True
