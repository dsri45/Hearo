# Hearo architecture

## Product boundary

The core feature is **Wait For…**. A user enters a free-text target such as “Flight 312 to Buffalo.” While Rest Mode is active, Hearo listens for relevant announcements and alerts only when a high-confidence match occurs.

Environmental-sound recognition is part of the core experience. A single
iPhone microphone capture feeds two detector lanes: Deepgram for spoken content
and Apple Sound Analysis for non-speech sounds. AirPods automation remains a
stretch goal.

## Components

```text
iPhone app
  |-- saves the current WatchItem locally
  |-- sends configuration and session audio to the backend
  |-- classifies environmental sounds locally with Apple Sound Analysis
  |-- reports sound labels and confidence to `/events/sound`
  |-- receives Alerts from the backend
  `-- converts Alerts into WatchMessages
          |
          `-- Apple Watch UI + foreground haptic

FastAPI backend
  |-- stores the current Watch Item, intent, and alert history in SQLite
  |-- streams iPhone PCM audio to Deepgram over `/ws/audio`
  |-- evaluates completed Deepgram utterances with the transcript matcher
  |-- matches reported environmental-sound events
  `-- creates and broadcasts Alerts
```

## Design rules

- The backend sends semantic `Alert` data, never arbitrary Watch UI commands.
- The iPhone owns the mapping from an `Alert` to a Watch display and haptic.
- Matching must reject near misses involving wrong numbers or destinations.
- Every repeated match emits another alert and future Watch haptic.
- Raw audio and transcripts should be session-scoped unless retention is explicitly added later.

## Shared message contracts (v1)

All message keys use `snake_case`. Unknown keys are rejected so contract drift is
found during development instead of being silently ignored. Text values are
trimmed, and `confidence` is always a number from `0.0` through `1.0`.

### WatchItem

Created by the iPhone and sent to the backend when the user changes what they
are waiting for.

```json
{
  "text": "Someone calling Dana",
  "preset_sounds": ["doorbell"]
}
```

Free text and presets can be used independently or together. Hearo exposes its
curated catalog through `GET /sound-presets`. Each entry includes candidate
Apple Sound Analysis identifiers. Because Apple exposes the authoritative
model inventory at runtime rather than as a fixed directory, the iOS client
intersects those candidates with `SNClassifySoundRequest.knownClassifications`
before showing presets. Checked presets are merged into Gemini's structured
intent deterministically.

### TranscriptEvent

Represents a finalized transcript segment. `start` and `end` are optional,
non-negative seconds relative to the current listening session.

```json
{
  "text": "Flight 312 to Buffalo has moved to gate C12.",
  "start": 12.4,
  "end": 16.8
}
```

### DetectedSoundEvent

Produced by Apple Sound Analysis on the iPhone and sent to the backend. The
dashboard uses the same contract with `dashboard_simulator` as its source.

```json
{
  "label": "doorbell",
  "confidence": 0.91,
  "source": "apple_sound_analysis"
}
```

### MatchResult

Internal structured output from the matcher. A positive match must include
`event_type`, `title`, and `detail`. A negative result may leave those fields
as `null`.

```json
{
  "match": true,
  "confidence": 0.98,
  "event_type": "gate_change",
  "title": "Flight 312",
  "detail": "Gate changed to C12",
  "reason": "The flight number and destination match the watch item."
}
```

### Alert

Sent from the backend to the iPhone. `id` is a stable semantic event identifier,
and `timestamp` is Unix time in seconds (UTC).

Allowed `category` values are `watchlist_match`, `normal_alert`, and `urgent`.

```json
{
  "id": "flight-312-gate-c12",
  "category": "watchlist_match",
  "title": "Flight 312",
  "detail": "Gate changed to C12",
  "confidence": 0.98,
  "timestamp": 1797891200.0
}
```

### WatchMessage

Created by the iPhone from an `Alert` and sent to the Apple Watch. The backend
does not directly select Watch UI behavior.

Allowed `haptic` values are `watchlist_match`, `normal_alert`, and `urgent`.

```json
{
  "haptic": "watchlist_match",
  "title": "Flight 312",
  "detail": "Gate changed to C12"
}
```

## Ownership boundary

```text
Backend matcher -> MatchResult -> Alert -> iPhone -> WatchMessage -> Watch
```

The backend owns semantic interpretation, confidence, alert category, and
stable event IDs. The iPhone owns the mapping from an alert category to a
Watch haptic and presentation. The Watch renders the supplied title and detail.

## Watch-term extraction and prototype matcher

The user does not choose a use-case category. Any free-text Watch Item is split
into strong identifiers (tokens containing digits, such as `47` or `A215`) and
supporting keywords (such as `coffee`, `Patel`, or `Seattle`). The same terms can
later be passed to Deepgram as transcription hints and to the matcher as evidence.

The first matcher is deterministic and operates on finalized transcript text:

```python
match_transcript(watch_item: str, transcript: str) -> MatchResult
```

It requires every strong identifier, enough supporting keywords, and an
actionable event such as boarding, delay, gate/platform change, cancellation,
final call, ready, called, arrival, or availability. Explicitly conflicting
destinations are rejected. Tests cover flights, food orders, trains,
appointments, raffles, prescriptions, and names.

A short direct call is treated as an event in its own right. For example,
`Order 47!` and an isolated `47!` can match a Watch Item containing exact
identifier `47`. The short length and exact-identifier requirements keep longer
background mentions and similar numbers from triggering this fallback.

This is a testable bridge—not the final semantic implementation. Deepgram will
supply transcript text but does not interpret arbitrary intent by itself.
Gemini structures each Watch Item once when it is saved; the inexpensive
deterministic matcher then evaluates transcript segments without changing the
external `MatchResult` contract.

## Gemini intent extraction

Saving a Watch Item invokes Gemini 3.5 Flash-Lite once using a strict Pydantic
Structured Output schema. The result contains a subject, required identifiers
with spoken aliases, context terms, interesting events, and speech-recognition
keyterms, speech targets, and environmental-sound targets. It is stored as JSON
in SQLite. Matching accepts identifier aliases and multi-word terms, while the
Deepgram stream repeats the extracted keyterms as `keyterm` query parameters.
The sound targets configure deterministic matching for classifier results;
Gemini interprets the request but never analyzes raw audio.

If the Gemini key is absent, the request fails, or the response cannot be
validated, Hearo uses the deterministic local extractor. Automated tests always
disable live LLM calls and use fake clients at the provider boundary.

## Debug alert pipeline

Until live audio is connected, `POST /debug/transcript` accepts a manually typed
`TranscriptEvent` and runs the same path that finalized Deepgram transcripts
will use later:

```text
saved WatchItem -> TranscriptEvent -> MatchResult -> Alert broadcast
```

Positive matches become `watchlist_match` alerts with stable IDs derived from
their event type, title, and detail. Every positive match emits an alert, even
when the same semantic event repeats immediately. `GET /debug/state` exposes
the latest evaluation for the local dashboard. The legacy `suppressed` response
field remains for contract compatibility and is always `false` by default.

`POST /events/sound` accepts a `DetectedSoundEvent` from Apple Sound Analysis or
the dashboard simulator. A normalized label must match one of the current
intent's sound targets and meet the 50% confidence threshold. Positive sound
matches enter the same alert broadcast path as speech matches. Sound-only Watch
Items are not evaluated as transcripts, which
prevents the spoken word "doorbell" from impersonating an actual doorbell.

## Live transcription

The backend loads `DEEPGRAM_API_KEY` from `backend/.env` or the process
environment and keeps the credential server-side. The iPhone connects to
`/ws/audio`, sends a start message describing mono 16 kHz linear16 PCM, then
streams binary audio frames. It sends `{"type":"stop"}` to end the session.
The backend forwards the frames to Deepgram's Nova-3 live transcription
endpoint and accumulates its finalized transcript chunks. Each stable chunk is
evaluated immediately while recording, and utterance completion provides a
final fallback evaluation. Every matching stable chunk may emit an alert; only
the same Deepgram message is prevented from being evaluated twice. Interim
transcript results are streamed to the client for responsive live display but
are not evaluated for alerts. Audio,
transcripts, and stream state remain in memory only.

The audio WebSocket currently has no client authentication and is intended for
local development. The iOS app is not yet present in this repository, so a
phone-to-alert end-to-end test requires an iOS client that implements this
audio protocol and captures the specified PCM format.

## Real-time delivery

`GET /dashboard` serves a same-origin browser interface for configuring a Watch
Item, inspecting Gemini's speech/sound listening plan, submitting test
transcripts, and simulating sound-classifier events. It renders the latest
`/debug/state` and connects to `/ws/alerts`.

The WebSocket emits the canonical `Alert` JSON object for every positive match.
This is also the endpoint the iPhone client can consume during the prototype.
Nonmatches do not produce WebSocket messages.

## Persistence

SQLite stores durable prototype state through a SQLAlchemy repository layer.
The default database is `backend/data/hearo.db`; `HEARO_DATABASE_URL` can
override its location. SQLite foreign keys, a five-second busy timeout, and WAL
mode are enabled for the file-backed database.

Persisted data includes:

- the current Watch Item and future structured intent JSON;
- listening-session metadata; and
- emitted alert history.

Raw audio, partial/final transcripts, live WebSocket connections, and the latest
dashboard evaluation are not persisted. Tests replace the development
repository with isolated in-memory or temporary-file SQLite databases.

