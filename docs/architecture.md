# Hearo architecture

## Product boundary

The core feature is **Wait For…**. A user enters a free-text target such as “Flight 312 to Buffalo.” While Rest Mode is active, Hearo listens for relevant announcements and alerts only when a high-confidence match occurs.

Generic sound recognition and AirPods automation are stretch goals and must not introduce a second audio pipeline.

## Components

```text
iPhone app
  |-- saves the current WatchItem locally
  |-- sends configuration and session audio to the backend
  |-- receives Alerts from the backend
  `-- converts Alerts into WatchMessages
          |
          `-- Apple Watch UI + foreground haptic

FastAPI backend
  |-- stores current session state in memory for the prototype
  |-- streams iPhone PCM audio to Deepgram over `/ws/audio`
  |-- evaluates completed Deepgram utterances with the transcript matcher
  `-- creates and deduplicates Alerts
```

## Design rules

- The backend sends semantic `Alert` data, never arbitrary Watch UI commands.
- The iPhone owns the mapping from an `Alert` to a Watch display and haptic.
- Matching must reject near misses involving wrong numbers or destinations.
- Repeated announcements must not trigger repeated haptics inside the dedupe window.
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
  "text": "Flight 312 to Buffalo"
}
```

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

Sent from the backend to the iPhone. `id` is a stable deduplication key for the
real-world event, and `timestamp` is Unix time in seconds (UTC).

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
deduplication IDs. The iPhone owns the mapping from an alert category to a
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
supply transcript text but does not interpret arbitrary intent by itself. A
later LLM-backed extractor can structure the Watch Item once when it is saved;
the inexpensive deterministic matcher can then evaluate transcript segments
against that structure without changing the external `MatchResult` contract.

## Debug alert pipeline

Until live audio is connected, `POST /debug/transcript` accepts a manually typed
`TranscriptEvent` and runs the same path that finalized Deepgram transcripts
will use later:

```text
saved WatchItem -> TranscriptEvent -> MatchResult -> Alert -> dedupe decision
```

Positive matches become `watchlist_match` alerts with stable IDs derived from
their event type, title, and detail. Repeating the same semantic event within 60
seconds is marked `suppressed` and emits no second alert. `GET /debug/state`
exposes the latest evaluation for the local dashboard.

## Live transcription

The backend loads `DEEPGRAM_API_KEY` from `backend/.env` or the process
environment and keeps the credential server-side. The iPhone connects to
`/ws/audio`, sends a start message describing mono 16 kHz linear16 PCM, then
streams binary audio frames. It sends `{"type":"stop"}` to end the session.
The backend forwards the frames to Deepgram's Nova-3 live transcription
endpoint, buffers finalized transcript chunks until an utterance is complete,
and passes each utterance through the existing matcher, alert, and
deduplication pipeline. Interim transcript results are not evaluated. Audio,
transcripts, and stream state remain in memory only.

The audio WebSocket currently has no client authentication and is intended for
local development. The iOS app is not yet present in this repository, so a
phone-to-alert end-to-end test requires an iOS client that implements this
audio protocol and captures the specified PCM format.

## Real-time delivery

`GET /dashboard` serves a same-origin browser interface for configuring a Watch
Item and submitting test transcripts. It renders the latest `/debug/state` and
connects to `/ws/alerts`.

The WebSocket emits the canonical `Alert` JSON object whenever a new alert
passes duplicate suppression. This is also the endpoint the iPhone client can
consume during the prototype. Nonmatches and suppressed duplicates do not
produce WebSocket messages.

## Persistence

SQLite stores durable prototype state through a SQLAlchemy repository layer.
The default database is `backend/data/hearo.db`; `HEARO_DATABASE_URL` can
override its location. SQLite foreign keys, a five-second busy timeout, and WAL
mode are enabled for the file-backed database.

Persisted data includes:

- the current Watch Item and future structured intent JSON;
- listening-session metadata; and
- emitted alerts, which also provide deduplication history across restarts.

Raw audio, partial/final transcripts, live WebSocket connections, and the latest
dashboard evaluation are not persisted. Tests replace the development
repository with isolated in-memory or temporary-file SQLite databases.

