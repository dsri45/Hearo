# Hearo

Hearo helps someone wait for a specific real-world announcement—such as a flight, order number, train, or appointment—and sends an alert when a matching event is heard.

The MVP flow is:

```text
iPhone microphone -> speech/sound detectors -> Python backend -> iPhone -> Apple Watch haptic
```

## Repository layout

- `backend/` — FastAPI service, matching logic, and Deepgram streaming
- `dashboard/` — local debugging dashboard
- `docs/` — architecture and message-contract documentation
- `ios/` — native SwiftUI iPhone app and Xcode project
- `tests/` — backend tests, deterministic cases, and later audio fixtures

## Current status

The backend and dashboard now support live Deepgram transcription plus environmental-sound events. The native iPhone client is in `ios/Hearo.xcodeproj`. It uses the same backend for Listen, Watch List, alerts, and Rest Mode recaps, while Apple Sound Analysis classifies microphone audio on the iPhone.

The dashboard displays Deepgram's interim transcript while the user is speaking.
Stable finalized chunks enter the matcher immediately while recording; unstable
interim words remain display-only.

Hearo also includes a browser-testable Rest Mode for times when the user removes
their hearing device. While Rest Mode is active, the normal Watch Item alert
pipeline continues, and completed utterances plus reported environmental sounds
form a broader context timeline. On exit, Gemini produces a structured "What I
missed" recap with priority events, conversations and announcements, and sound
context. Hearo never stores raw audio, and the temporary timeline is deleted
after its recap is saved in SQLite.

## Run the backend

On macOS, launch it on all network interfaces so an iPhone on the same Wi-Fi
can reach it:

```sh
backend/.venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

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

Gemini reads `GEMINI_API_KEY` from `backend/.env` (the legacy `LLM_API_KEY`
name also works). Its model defaults to `gemini-3.5-flash-lite` and can be
overridden with `GEMINI_MODEL`. Keys remain server-side and must never be
committed.

For the pre-audio integration checkpoint, set a Watch Item with
`POST /watch-item`, then submit announcement text to `POST /debug/transcript`.
The response includes the match decision and any emitted alert. Every matching
event emits an alert, including immediate repetitions. The latest evaluation is
available from `GET /debug/state`.

Open `http://localhost:8000/app` for the Hearo UI. Its controls connect to the
Watch Item, Rest Mode, transcript and sound simulation endpoints, the live
microphone transcription socket, and the alert WebSocket. The UI restores the
saved Watch Item and recent alerts from the backend when it opens. The earlier
developer dashboard remains available at `http://localhost:8000/dashboard`.

The UI's microphone streams mono 16 kHz PCM to `/ws/audio`; live transcription
requires `DEEPGRAM_API_KEY`. Transcript and sound simulation controls work
without a microphone and send events through the same matcher and alert pipeline.

The app and developer dashboard can exercise the complete non-iOS flow. Start
listening (which starts Rest Mode and the microphone), use live transcription or
the transcript/sound simulators, then end listening to generate the recap. Rest
Mode works without a Watch Item when the user only wants a broad recap. With a
Watch Item configured, matching events still emit immediate alerts while the
broader recap context is collected.

The equivalent API lifecycle is `POST /rest-mode/start`, `GET /rest-mode`, and
`POST /rest-mode/stop`. The iPhone app watches AVAudioSession route changes:
connecting an AirPods/Bluetooth/wired audio route ends Rest Mode, and losing
that route starts it. Rest Mode audio capture can continue while the app is in
the background. iOS does not expose reliable AirPods in-ear state to third-party
apps, and route changes may not wake a suspended app, so transitions should be
validated on the paired iPhone with the app active. A future hearing-aid model
may expose a more reliable wearing-state API. The iPhone project also embeds a
simple Hearo watchOS app: it shows the
latest alert title and gives an active Watch app a notification haptic. The
iPhone sends a title-only notification so watchOS can forward it to the Watch
when the phone is locked or asleep.

## Run the native iPhone app

Open `ios/Hearo.xcodeproj` in Xcode, select the Hearo target, choose your Apple
development team under Signing & Capabilities, and run it on an iPhone. The app
asks for microphone access because it runs Apple Sound Analysis and streams
16 kHz audio through the backend to Deepgram for transcription.

Start the backend on your Mac first. In the app's Profile tab, set Backend
connection to your Mac's local network address, for example
`http://192.168.1.20:8000`; `localhost` on the iPhone refers to the phone itself.
Keep the phone and Mac on the same Wi-Fi network. The Sounds picker reads
`SNClassifySoundRequest.knownClassifications` on-device and sends matching
labels and confidence to `POST /events/sound`. The Name option uses the first
name saved in the app's Profile and checks the live transcript for it.

The Watch companion is the `Hearo Watch App` target in the same Xcode project.
Run the `Hearo` scheme on the paired iPhone to install both apps. Allow Hearo
notifications on first launch and enable Hearo under Apple Watch app → My Watch
→ Notifications. Direct WatchConnectivity haptics work while the Watch app is
active; background notifications use watchOS notification forwarding.

## MVP order

1. Establish the development environment and repository layout.
2. Define shared JSON message contracts.
3. Build the FastAPI skeleton and deterministic matcher tests.
4. Prove fake alerts end-to-end before adding transcription.
5. Connect the iPhone audio capture to `/ws/audio` and iterate on semantic matching.

See [the architecture notes](docs/architecture.md) for component boundaries.

