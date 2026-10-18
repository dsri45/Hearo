"""Shared JSON contracts used by the backend, iPhone, and Apple Watch."""

from enum import Enum

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


class WatchItem(ContractModel):
    """Free-text description of the event the user is waiting for."""

    text: str = Field(min_length=1, max_length=500)


class ExtractedWatchTerms(ContractModel):
    """Locally extracted terms used for transcription hints and matching."""

    identifiers: list[str]
    keywords: list[str]


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

