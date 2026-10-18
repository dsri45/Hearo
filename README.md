# Hearo

Hearo helps someone wait for a specific real-world announcement—such as a flight, order number, train, or appointment—and sends an alert when a matching event is heard.

The MVP flow is:

```text
iPhone microphone -> Python backend -> transcript matcher -> iPhone -> Apple Watch haptic
```

## Repository layout

- `backend/` — FastAPI service, matching logic, and Deepgram streaming
- `dashboard/` — local debugging dashboard
- `docs/` — architecture and message-contract documentation
- `ios/` — iPhone and Watch app (created on the Mac)
- `tests/` — backend tests, deterministic cases, and later audio fixtures

## Current status

The backend and dashboard now support live Deepgram transcription through a local microphone stream. The backend loads its key from `backend/.env`, evaluates completed utterances with the deterministic matcher, and emits deduplicated alerts. The v1 backend/iPhone/Watch message contracts and debug transcript path remain available. The iOS and Watch clients have not yet been created.

## Run the backend

From the repository root on Windows:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

Before starting live transcription, copy `backend/.env.example` to
`backend/.env` and set `DEEPGRAM_API_KEY`. The backend loads that file directly
and keeps the key server-side. `/ws/audio` accepts a JSON start message with
`type: start`, `encoding: linear16`, `sample_rate: 16000`, and `channels: 1`,
then mono 16-bit PCM audio frames. Send `{"type":"stop"}` to finish a session.
Completed utterances use the same matcher and alert path as
`POST /debug/transcript`.

Then open `http://localhost:8000/docs` for the interactive API documentation.

The backend automatically creates a local SQLite database at
`backend/data/hearo.db`. The current Watch Item and emitted-alert history survive
server restarts. Database files are ignored by Git. To use a different location,
set `HEARO_DATABASE_URL` in `backend/.env`.

For the pre-audio integration checkpoint, set a Watch Item with
`POST /watch-item`, then submit announcement text to `POST /debug/transcript`.
The response includes the match decision and any emitted alert. Repeating an
identical matching event within 60 seconds is suppressed. The latest evaluation
is available from `GET /debug/state`.

Open `http://localhost:8000/dashboard` for the local testing interface. It uses
`/ws/alerts` to display emitted alerts in real time; the future iPhone client can
consume the same canonical Alert messages.

## MVP order

1. Establish the development environment and repository layout.
2. Define shared JSON message contracts.
3. Build the FastAPI skeleton and deterministic matcher tests.
4. Prove fake alerts end-to-end before adding transcription.
5. Connect the iPhone audio capture to `/ws/audio` and iterate on semantic matching.

See [the architecture notes](docs/architecture.md) for component boundaries.

