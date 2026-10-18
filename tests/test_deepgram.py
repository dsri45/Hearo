"""Tests for Deepgram URL configuration and transcript buffering."""

from urllib.parse import parse_qs, urlparse

from backend.app.deepgram import FinalTranscriptBuffer, build_live_url


def test_live_url_repeats_gemini_keyterms() -> None:
    url = build_live_url(16000, ["Order 47", "forty-seven", "oat latte"])
    query = parse_qs(urlparse(url).query)

    assert query["model"] == ["nova-3"]
    assert query["sample_rate"] == ["16000"]
    assert query["keyterm"] == ["Order 47", "forty-seven", "oat latte"]


def test_final_transcript_buffer_combines_final_chunks() -> None:
    buffer = FinalTranscriptBuffer()

    assert buffer.add(
        {
            "type": "Results",
            "is_final": True,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "Order forty"}]},
        }
    ) is None
    assert buffer.add(
        {
            "type": "Results",
            "is_final": True,
            "speech_final": True,
            "channel": {"alternatives": [{"transcript": "seven"}]},
        }
    ) == "Order forty seven"


def test_transcript_buffer_exposes_accumulating_interim_preview() -> None:
    buffer = FinalTranscriptBuffer()

    first = buffer.process(
        {
            "type": "Results",
            "is_final": False,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "Is Dana"}]},
        }
    )
    finalized_chunk = buffer.process(
        {
            "type": "Results",
            "is_final": True,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "Is Dana"}]},
        }
    )
    second = buffer.process(
        {
            "type": "Results",
            "is_final": False,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "here"}]},
        }
    )

    assert first.preview == "Is Dana"
    assert first.complete is None
    assert first.stable is False
    assert finalized_chunk.preview == "Is Dana"
    assert finalized_chunk.stable is True
    assert second.preview == "Is Dana here"
    assert second.complete is None
    assert second.stable is False
