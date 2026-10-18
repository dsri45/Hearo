"""
Hearo: diarization check

Sends a recorded test scene to Deepgram, prints who said what and when,
and saves the full result next to the audio file. That saved file becomes
a test fixture for the auto mode harness, so you never pay Deepgram twice
for the same recording.

Setup (once):
    pip install requests python-dotenv
    Create a file named .env next to this script containing:
        DEEPGRAM_API_KEY=your_key_here

Usage:
    python diarization_check.py scenes/scene1.m4a

What to look for in the output:
    1. Does the number of speakers match the number of real people?
    2. Does any single speaker's lines mix two different people?
       (That's the failure auto mode can't recover from.)
    3. Are your group's lines and the other table's lines on different speakers?
"""

import json
import mimetypes
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
API_KEY = os.getenv("DEEPGRAM_API_KEY")

URL = "https://api.deepgram.com/v1/listen"
PARAMS = {
    "model": "nova-3",           # swap if Deepgram's docs list a newer model
    "diarize_model": "latest",   # speaker labels (replaces the old diarize=true)
    "punctuate": "true",
    "smart_format": "true",
    "utterances": "true",        # group words into sentences per speaker
}


def transcribe(path: Path) -> dict:
    content_type = mimetypes.guess_type(str(path))[0] or "audio/wav"
    with open(path, "rb") as f:
        resp = requests.post(
            URL,
            params=PARAMS,
            headers={
                "Authorization": f"Token {API_KEY}",
                "Content-Type": content_type,
            },
            data=f,
            timeout=300,
        )
    if resp.status_code != 200:
        sys.exit(f"Deepgram error {resp.status_code}: {resp.text}")
    return resp.json()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python diarization_check.py <audio file>")
    if not API_KEY:
        sys.exit("Missing DEEPGRAM_API_KEY. Put it in a .env file next to this script.")

    audio = Path(sys.argv[1])
    if not audio.exists():
        sys.exit(f"File not found: {audio}")

    print(f"Sending {audio.name} to Deepgram...")
    result = transcribe(audio)

    saved = audio.with_suffix(".deepgram.json")
    saved.write_text(json.dumps(result, indent=2))

    utterances = result.get("results", {}).get("utterances", [])
    if not utterances:
        sys.exit("No speech came back. Check that the recording has audible voices.")

    talk_time = defaultdict(float)
    turns = defaultdict(int)

    print(f"\n=== Transcript: {audio.name} ===\n")
    for u in utterances:
        speaker = u["speaker"]
        talk_time[speaker] += u["end"] - u["start"]
        turns[speaker] += 1
        print(f"[{u['start']:6.1f}s - {u['end']:6.1f}s]  Speaker {speaker}:  {u['transcript']}")

    print("\n=== Speaker summary ===\n")
    print(f"Deepgram heard {len(talk_time)} speakers.\n")
    for speaker in sorted(talk_time):
        print(f"Speaker {speaker}:  {turns[speaker]:3d} turns,  {talk_time[speaker]:6.1f}s of speech")

    print(f"\nFull result saved to {saved} (reuse this in the auto mode harness).")


if __name__ == "__main__":
    main()
