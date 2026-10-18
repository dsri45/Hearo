"""Deepgram live transcription helpers."""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode


def build_live_url(sample_rate: int, keyterms: list[str] | None = None) -> str:
    """Build a Deepgram URL for raw, mono, 16-bit PCM audio."""

    parameters: list[tuple[str, str | int]] = [
        ("model", "nova-3"),
        ("language", "en-US"),
        ("encoding", "linear16"),
        ("sample_rate", sample_rate),
        ("channels", 1),
        ("interim_results", "true"),
        ("smart_format", "true"),
        ("endpointing", 300),
    ]
    parameters.extend(
        ("keyterm", term.strip())
        for term in (keyterms or [])[:100]
        if term.strip()
    )
    query = urlencode(parameters)
    return f"wss://api.deepgram.com/v1/listen?{query}"


@dataclass(frozen=True)
class TranscriptProgress:
    """Displayable speech so far and an optional completed utterance."""

    preview: str | None = None
    complete: str | None = None
    stable: bool = False


class FinalTranscriptBuffer:
    """Combine finalized chunks until Deepgram marks an utterance complete."""

    def __init__(self) -> None:
        self._chunks: list[str] = []

    def process(self, message: Mapping[str, object]) -> TranscriptProgress:
        if message.get("type") != "Results":
            return TranscriptProgress()

        channel = message.get("channel")
        if not isinstance(channel, Mapping):
            return TranscriptProgress()
        alternatives = channel.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            return TranscriptProgress()
        alternative = alternatives[0]
        if not isinstance(alternative, Mapping):
            return TranscriptProgress()

        transcript = alternative.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            return TranscriptProgress()

        transcript = transcript.strip()
        is_final = message.get("is_final") is True
        speech_final = message.get("speech_final") is True

        if is_final:
            self._chunks.append(transcript)

        preview_parts = [*self._chunks]
        if not is_final:
            preview_parts.append(transcript)
        preview = " ".join(preview_parts)

        if not speech_final:
            return TranscriptProgress(preview=preview, stable=is_final)

        if not self._chunks:
            self._chunks.append(transcript)
        complete_transcript = " ".join(self._chunks)
        self._chunks.clear()
        return TranscriptProgress(
            preview=complete_transcript,
            complete=complete_transcript,
            stable=is_final,
        )

    def add(self, message: Mapping[str, object]) -> str | None:
        """Compatibility helper returning only a completed utterance."""

        return self.process(message).complete

