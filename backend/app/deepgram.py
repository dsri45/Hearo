"""Deepgram live transcription helpers."""

from collections.abc import Mapping
from urllib.parse import urlencode


def build_live_url(sample_rate: int) -> str:
    """Build a Deepgram URL for raw, mono, 16-bit PCM audio."""

    query = urlencode(
        {
            "model": "nova-3",
            "language": "en-US",
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true",
            "smart_format": "true",
            "endpointing": 300,
        }
    )
    return f"wss://api.deepgram.com/v1/listen?{query}"


class FinalTranscriptBuffer:
    """Combine finalized chunks until Deepgram marks an utterance complete."""

    def __init__(self) -> None:
        self._chunks: list[str] = []

    def add(self, message: Mapping[str, object]) -> str | None:
        if message.get("type") != "Results":
            return None

        channel = message.get("channel")
        if not isinstance(channel, Mapping):
            return None
        alternatives = channel.get("alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            return None
        alternative = alternatives[0]
        if not isinstance(alternative, Mapping):
            return None

        transcript = alternative.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            return None

        if message.get("is_final") is True:
            self._chunks.append(transcript.strip())

        if message.get("speech_final") is not True:
            return None

        if not self._chunks:
            self._chunks.append(transcript.strip())
        complete_transcript = " ".join(self._chunks)
        self._chunks.clear()
        return complete_transcript

