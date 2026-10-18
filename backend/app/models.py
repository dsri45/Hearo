"""Shared JSON contracts used by the backend, iPhone, and Apple Watch."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Common behavior for externally visible message contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class AlertCategory(str, Enum):
    """Semantic priority assigned by the backend."""

    WATCHLIST_MATCH = "watchlist_match"
    NORMAL_ALERT = "normal_alert"
    URGENT = "urgent"


class HapticType(str, Enum):
    """Haptic presentation selected by the iPhone for the Watch."""

    WATCHLIST_MATCH = "watchlist_match"
    NORMAL_ALERT = "normal_alert"
    URGENT = "urgent"


class RestEventType(str, Enum):
    """Context captured while the user is in Rest Mode."""

    TRANSCRIPT = "transcript"
    SOUND = "sound"


class SoundPresetID(str, Enum):
    """Curated Hearo presets backed by Apple Sound Analysis labels."""

    DOORBELL = "doorbell"
    SIREN = "siren"
    FIRE_ALARM = "fire_alarm"
    SMOKE_ALARM = "smoke_alarm"
    DOOR_KNOCK = "door_knock"
    DOG_BARK = "dog_bark"
    BABY_CRYING = "baby_crying"
    CAR_HORN = "car_horn"


class WatchItem(ContractModel):
    """Free text and optional prebuilt sounds selected by the user."""

    text: str = Field(default="", max_length=500)
    preset_sounds: list[SoundPresetID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_text_or_preset(self) -> "WatchItem":
        if not self.text and not self.preset_sounds:
            raise ValueError("provide text or select at least one sound preset")
        return self


class ExtractedWatchTerms(ContractModel):
    """Locally extracted terms used for transcription hints and matching."""

    identifiers: list[str]
    keywords: list[str]


class IntentIdentifier(ContractModel):
    """An identifier and equivalent spoken forms extracted from a Watch Item."""

    value: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    required: bool = True


class SpeechTarget(ContractModel):
    """A spoken phrase, name, or announcement the speech lane listens for."""

    kind: str = Field(min_length=1, max_length=50)
    terms: list[str] = Field(default_factory=list, max_length=50)


class SoundTarget(ContractModel):
    """An environmental sound label and equivalent classifier labels."""

    label: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class SoundPreset(ContractModel):
    """A user-facing preset mapped to built-in Apple classifier labels."""

    id: SoundPresetID
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=200)
    apple_classifications: list[str] = Field(min_length=1, max_length=30)


class StructuredWatchIntent(ContractModel):
    """LLM-produced meaning used for keyterms and deterministic matching."""

    subject: str = Field(min_length=1, max_length=200)
    identifiers: list[IntentIdentifier] = Field(default_factory=list, max_length=20)
    context_terms: list[str] = Field(default_factory=list, max_length=50)
    interesting_events: list[str] = Field(default_factory=list, max_length=30)
    keyterms: list[str] = Field(default_factory=list, max_length=100)
    speech_targets: list[SpeechTarget] = Field(default_factory=list, max_length=30)
    sound_targets: list[SoundTarget] = Field(default_factory=list, max_length=30)


class TranscriptEvent(ContractModel):
    """A finalized transcript segment with optional session-relative timing."""

    text: str = Field(min_length=1, max_length=10_000)
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptEvent":
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class DetectedSoundEvent(ContractModel):
    """A sound classification produced by an iPhone or debug simulator."""

    label: str = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="apple_sound_analysis", min_length=1, max_length=100)


class RestModeEvent(ContractModel):
    """A temporary, non-audio event used to produce a Rest Mode recap."""

    sequence: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=36)
    event_type: RestEventType
    text: str = Field(min_length=1, max_length=10_000)
    timestamp: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    important: bool = False
    source: str = Field(default="unknown", min_length=1, max_length=100)


class RestModeRecap(ContractModel):
    """Structured summary shown when the user exits Rest Mode."""

    title: str = Field(min_length=1, max_length=120)
    overview: str = Field(min_length=1, max_length=1_000)
    important_events: list[str] = Field(default_factory=list, max_length=20)
    conversations_and_announcements: list[str] = Field(
        default_factory=list,
        max_length=30,
    )
    environmental_sounds: list[str] = Field(default_factory=list, max_length=20)
    provider: str = Field(default="local", min_length=1, max_length=30)


class RestModeSession(ContractModel):
    """Persisted lifecycle and final recap for one Rest Mode interval."""

    id: str = Field(min_length=1, max_length=36)
    status: Literal["active", "ended"]
    started_at: float = Field(ge=0)
    ended_at: float | None = Field(default=None, ge=0)
    watch_item: str | None = Field(default=None, max_length=500)
    event_count: int = Field(default=0, ge=0)
    recap: RestModeRecap | None = None


class MatchResult(ContractModel):
    """The matcher's structured decision for a transcript segment."""

    match: bool
    confidence: float = Field(ge=0, le=1)
    event_type: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, min_length=1, max_length=500)
    reason: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def require_alert_content_for_match(self) -> "MatchResult":
        if self.match:
            missing = [
                name
                for name in ("event_type", "title", "detail")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    "matched results require: " + ", ".join(missing)
                )
        return self


class Alert(ContractModel):
    """Semantic alert sent from the backend to the iPhone."""

    id: str = Field(min_length=1, max_length=128)
    category: AlertCategory
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    timestamp: float = Field(ge=0)


class WatchMessage(ContractModel):
    """Presentation message sent from the iPhone to the Apple Watch."""

    haptic: HapticType
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=500)

