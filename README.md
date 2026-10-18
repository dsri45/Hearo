# Hearo

Hearo helps someone wait for a specific real-world announcement—such as a flight, order number, train, or appointment—and sends an alert when a matching event is heard.

The MVP flow is:

```text
iPhone microphone -> Python backend -> transcript matcher -> iPhone -> Apple Watch haptic
```

## Repository layout

- `backend/` — FastAPI service, matching logic, and later Deepgram integration
- `dashboard/` — local debugging dashboard
- `docs/` — architecture and message-contract documentation
- `ios/` — iPhone and Watch app (created on the Mac)
- `tests/` — backend tests, deterministic cases, and later audio fixtures

## Current status

The initial cross-platform repository structure is in place. The backend uses a Python 3.12 virtual environment at `backend/.venv`, with its dependencies pinned in `backend/requirements.txt`. The v1 backend/iPhone/Watch message contracts are implemented, documented, and covered by tests. The first FastAPI endpoints support health checks and setting or reading the process-local Wait For item. An offline deterministic matcher extracts identifiers and supporting keywords from arbitrary Watch Items and exercises varied use cases without external services.

## Run the backend

From the repository root on Windows:

```powershell
backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

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
5. Add Deepgram streaming and semantic matching.

See [the architecture notes](docs/architecture.md) for component boundaries.

