"""Deterministic matching for environmental sound-classifier events."""

import re

from .models import DetectedSoundEvent, MatchResult, StructuredWatchIntent


MINIMUM_SOUND_CONFIDENCE = 0.50

_SEPARATOR_RE = re.compile(r"[_\-]+")
_NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")

_CANONICAL_LABELS = {
    "bell": "doorbell",
    "door bell": "doorbell",
    "door chime": "doorbell",
    "buzzer": "doorbell",
    "emergency siren": "siren",
    "police siren": "siren",
    "ambulance siren": "siren",
    "fire engine siren": "siren",
    "fire alarm": "fire_alarm",
    "smoke alarm": "smoke_alarm",
    "smoke detector": "smoke_alarm",
    "knock": "door_knock",
    "knocking": "door_knock",
    "door knock": "door_knock",
    "dog bark": "dog_bark",
    "dog barking": "dog_bark",
    "barking": "dog_bark",
    "baby cry": "baby_crying",
    "baby crying": "baby_crying",
    "infant cry": "baby_crying",
    "car horn": "car_horn",
    "vehicle horn": "car_horn",
}


def normalize_sound_label(label: str) -> str:
    """Normalize common Apple/custom classifier labels to a stable label."""

    normalized = _SEPARATOR_RE.sub(" ", label.strip().lower())
    normalized = " ".join(_NON_WORD_RE.sub("", normalized).split())
    canonical = _CANONICAL_LABELS.get(normalized, normalized)
    return canonical.replace(" ", "_")


def match_sound_event(
    watch_item: str,
    event: DetectedSoundEvent,
    intent: StructuredWatchIntent | None,
    minimum_confidence: float = MINIMUM_SOUND_CONFIDENCE,
) -> MatchResult:
    """Match a classifier result against the configured environmental sounds."""

    if intent is None or not intent.sound_targets:
        return MatchResult(
            match=False,
            confidence=event.confidence,
            reason="The Watch Item does not include an environmental sound target.",
        )

    if event.confidence < minimum_confidence:
        return MatchResult(
            match=False,
            confidence=event.confidence,
            reason=(
                f"Sound confidence is below the {minimum_confidence:.0%} threshold."
            ),
        )

    detected = normalize_sound_label(event.label)
    matched_target = next(
        (
            target
            for target in intent.sound_targets
            if detected
            in {
                normalize_sound_label(target.label),
                *(normalize_sound_label(alias) for alias in target.aliases),
            }
        ),
        None,
    )
    if matched_target is None:
        expected = ", ".join(target.label for target in intent.sound_targets)
        return MatchResult(
            match=False,
            confidence=event.confidence,
            reason=f"Detected {event.label}, but waiting for: {expected}.",
        )

    label = normalize_sound_label(matched_target.label)
    display_label = label.replace("_", " ").title()
    return MatchResult(
        match=True,
        confidence=event.confidence,
        event_type=f"sound_{label}",
        title=watch_item.strip(),
        detail=f"{display_label} detected",
        reason=(
            f"The {event.source} classifier detected {event.label} at "
            f"{event.confidence:.0%} confidence."
        ),
    )
