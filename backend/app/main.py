"""FastAPI entry point for the Hearo backend."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from websockets.asyncio.client import connect as connect_websocket

from . import state
from .alerts import create_alert
from .deepgram import FinalTranscriptBuffer, build_live_url
from .intent import extract_intent, local_intent
from .matcher import match_transcript
from .models import (
    Alert,
    ContractModel,
    DetectedSoundEvent,
    MatchResult,
    RestModeSession,
    SoundPreset,
    SoundTarget,
    StructuredWatchIntent,
    TranscriptEvent,
    WatchItem,
)
from .recap import generate_recap
from .realtime import alert_connections
from .sound_catalog import (
    SOUND_PRESETS,
    describe_sound_presets,
    sound_target_for_preset,
)
from .sound_matcher import match_sound_event, normalize_sound_label


_DASHBOARD_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"
_BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_BACKEND_ENV_PATH)
_logger = logging.getLogger(__name__)


class ServiceStatus(ContractModel):
    status: Literal["ok"]
    service: Literal["Hearo"]


class SetWatchItemResponse(ContractModel):
    success: Literal[True]
    watch_item: str


class GetWatchItemResponse(ContractModel):
    watch_item: str | None


class GetWatchIntentResponse(ContractModel):
    intent: StructuredWatchIntent | None


class GetSoundPresetsResponse(ContractModel):
    presets: list[SoundPreset]


class TranscriptEvaluationResponse(ContractModel):
    watch_item: str | None
    transcript: TranscriptEvent
    match_result: MatchResult
    alert: Alert | None
    suppressed: bool


class SoundEvaluationResponse(ContractModel):
    watch_item: str | None
    sound_event: DetectedSoundEvent
    match_result: MatchResult
    alert: Alert | None
    suppressed: bool


class DebugStateResponse(ContractModel):
    watch_item: str | None
    transcript: TranscriptEvent | None
    sound_event: DetectedSoundEvent | None
    match_result: MatchResult | None
    alert: Alert | None
    suppressed: bool


class RestModeStatusResponse(ContractModel):
    active: RestModeSession | None
    latest: RestModeSession | None


app = FastAPI(
    title="Hearo API",
    description="Backend for Hearo's Wait For… experience.",
    version="0.1.0",
)


@app.get("/", response_model=ServiceStatus, tags=["service"])
def root() -> ServiceStatus:
    """Return a small service-identification response."""

    return ServiceStatus(status="ok", service="Hearo")


@app.get("/health", response_model=ServiceStatus, tags=["service"])
def health() -> ServiceStatus:
    """Report whether the API process is available."""

    return ServiceStatus(status="ok", service="Hearo")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the local debug dashboard from the repository."""

    return FileResponse(_DASHBOARD_PATH)


@app.get(
    "/sound-presets",
    response_model=GetSoundPresetsResponse,
    tags=["wait-for"],
)
def get_sound_presets() -> GetSoundPresetsResponse:
    """Return common sounds and their candidate Apple classifier labels."""

    return GetSoundPresetsResponse(presets=list(SOUND_PRESETS))


@app.post(
    "/watch-item",
    response_model=SetWatchItemResponse,
    tags=["wait-for"],
)
def set_watch_item(item: WatchItem) -> SetWatchItemResponse:
    """Set the Wait For item for the current prototype session."""

    preset_ids = [str(preset_id) for preset_id in item.preset_sounds]
    display_text = item.text or describe_sound_presets(preset_ids)
    intent = extract_intent(item.text) if item.text else local_intent(display_text)
    existing_labels = {
        normalize_sound_label(target.label) for target in intent.sound_targets
    }
    preset_targets: list[SoundTarget] = []
    for preset_id in preset_ids:
        target = sound_target_for_preset(preset_id)
        if normalize_sound_label(target.label) not in existing_labels:
            preset_targets.append(target)
            existing_labels.add(normalize_sound_label(target.label))
    intent = intent.model_copy(
        update={"sound_targets": [*intent.sound_targets, *preset_targets]}
    )
    state.set_watch_item(display_text, intent.model_dump(mode="json"))
    return SetWatchItemResponse(success=True, watch_item=display_text)


@app.get(
    "/watch-item",
    response_model=GetWatchItemResponse,
    tags=["wait-for"],
)
def get_watch_item() -> GetWatchItemResponse:
    """Return the current Wait For item."""

    return GetWatchItemResponse(watch_item=state.get_watch_item())


@app.get(
    "/watch-item/intent",
    response_model=GetWatchIntentResponse,
    tags=["wait-for"],
)
def get_watch_intent() -> GetWatchIntentResponse:
    """Return the persisted structured meaning of the current Watch Item."""

    return GetWatchIntentResponse(intent=state.get_watch_intent())


@app.post("/rest-mode/start", response_model=RestModeSession, tags=["rest-mode"])
def start_rest_mode() -> RestModeSession:
    """Begin collecting finalized context without retaining raw audio."""

    try:
        return state.start_rest_mode()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/rest-mode", response_model=RestModeStatusResponse, tags=["rest-mode"])
def get_rest_mode() -> RestModeStatusResponse:
    """Return the active Rest Mode interval and most recent saved recap."""

    active = state.get_active_rest_session()
    latest = state.get_latest_rest_session()
    return RestModeStatusResponse(active=active, latest=latest)


@app.post("/rest-mode/stop", response_model=RestModeSession, tags=["rest-mode"])
def stop_rest_mode() -> RestModeSession:
    """Summarize captured context, persist the recap, and purge its timeline."""

    active = state.get_active_rest_session()
    if active is None:
        raise HTTPException(status_code=409, detail="Rest Mode is not active.")
    recap = generate_recap(state.get_rest_events(active.id))
    completed = state.finish_rest_mode(active.id, recap)
    if completed is None:
        raise HTTPException(status_code=409, detail="Rest Mode is no longer active.")
    return completed


@app.post(
    "/debug/transcript",
    response_model=TranscriptEvaluationResponse,
    tags=["debug"],
)
async def evaluate_transcript(event: TranscriptEvent) -> TranscriptEvaluationResponse:
    """Run manually supplied transcript text through the complete alert pipeline."""

    watch_item = state.get_watch_item()
    if watch_item is None and state.get_active_rest_session() is None:
        raise HTTPException(
            status_code=409,
            detail="Set a Watch Item or start Rest Mode before evaluating a transcript.",
        )

    result, emitted_alert, suppressed = await _process_transcript(watch_item, event)
    return TranscriptEvaluationResponse(
        watch_item=watch_item,
        transcript=event,
        match_result=result,
        alert=emitted_alert,
        suppressed=suppressed,
    )


async def _process_transcript(
    watch_item: str | None,
    event: TranscriptEvent,
    *,
    context_text: str | None = None,
    source: str = "debug",
    record_context: bool = True,
) -> tuple[MatchResult, Alert | None, bool]:
    result = (
        MatchResult(match=False, confidence=0, reason="No Watch Item configured")
        if watch_item is None
        else match_transcript(watch_item, event.text, state.get_watch_intent())
    )
    emitted_alert, suppressed = await _emit_match(result)
    if record_context:
        state.record_rest_transcript(
            context_text or event.text,
            important=result.match,
            source=source,
        )
    state.record_evaluation(event, result, emitted_alert, suppressed)
    return result, emitted_alert, suppressed


async def _emit_match(result: MatchResult) -> tuple[Alert | None, bool]:
    """Emit a canonical alert for either speech or environmental sound."""

    emitted_alert: Alert | None = None
    suppressed = False

    if result.match:
        candidate = create_alert(result)
        suppressed = state.is_duplicate_alert(candidate)
        if not suppressed:
            emitted_alert = candidate
            state.save_alert(emitted_alert)
            await alert_connections.broadcast(emitted_alert)

    return emitted_alert, suppressed


@app.post(
    "/events/sound",
    response_model=SoundEvaluationResponse,
    tags=["events"],
)
async def evaluate_sound(event: DetectedSoundEvent) -> SoundEvaluationResponse:
    """Evaluate a sound label produced by Apple Sound Analysis or a simulator."""

    watch_item = state.get_watch_item()
    if watch_item is None and state.get_active_rest_session() is None:
        raise HTTPException(
            status_code=409,
            detail="Set a Watch Item or start Rest Mode before reporting a sound event.",
        )

    result = (
        MatchResult(match=False, confidence=0, reason="No Watch Item configured")
        if watch_item is None
        else match_sound_event(watch_item, event, state.get_watch_intent())
    )
    emitted_alert, suppressed = await _emit_match(result)
    state.record_rest_sound(event, important=result.match)
    state.record_sound_evaluation(event, result, emitted_alert, suppressed)
    return SoundEvaluationResponse(
        watch_item=watch_item,
        sound_event=event,
        match_result=result,
        alert=emitted_alert,
        suppressed=suppressed,
    )


@app.get(
    "/debug/state",
    response_model=DebugStateResponse,
    tags=["debug"],
)
def get_debug_state() -> DebugStateResponse:
    """Return the most recent pipeline evaluation for dashboard development."""

    return DebugStateResponse(
        watch_item=state.get_watch_item(),
        transcript=state.latest_transcript,
        sound_event=state.latest_sound_event,
        match_result=state.latest_match,
        alert=state.latest_alert,
        suppressed=state.latest_suppressed,
    )


@app.websocket("/ws/alerts")
async def alert_websocket(websocket: WebSocket) -> None:
    """Stream newly emitted Alerts to connected iPhone and dashboard clients."""

    await alert_connections.connect(websocket)
    try:
        while True:
            # Clients can send lightweight keepalive messages. Alerts flow in
            # the opposite direction through the shared connection manager.
            await websocket.receive_text()
    except WebSocketDisconnect:
        alert_connections.disconnect(websocket)


@app.websocket("/ws/audio")
async def audio_websocket(websocket: WebSocket) -> None:
    """Forward PCM audio to Deepgram and evaluate completed utterances."""

    await websocket.accept()

    async def reject(code: int, detail: str) -> None:
        await websocket.send_json({"type": "error", "detail": detail})
        await websocket.close(code=code, reason=detail)

    if state.get_watch_item() is None and state.get_active_rest_session() is None:
        await reject(4409, "Set a Watch Item or start Rest Mode before listening.")
        return

    api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        await reject(1011, "DEEPGRAM_API_KEY is missing from backend/.env.")
        return

    try:
        start_message = await websocket.receive_json()
    except (WebSocketDisconnect, json.JSONDecodeError):
        return
    if (
        not isinstance(start_message, dict)
        or start_message.get("type") != "start"
        or start_message.get("encoding") != "linear16"
        or start_message.get("sample_rate") != 16000
        or start_message.get("channels") != 1
    ):
        await reject(4400, "Start with mono linear16 audio at 16000 Hz.")
        return

    watch_item = state.get_watch_item()
    try:
        watch_intent = state.get_watch_intent()
        keyterms = watch_intent.keyterms if watch_intent is not None else []
        async with connect_websocket(
            build_live_url(16000, keyterms=keyterms),
            additional_headers={"Authorization": f"Token {api_key}"},
            max_size=None,
        ) as deepgram:
            await websocket.send_json({"type": "ready"})
            transcript_buffer = FinalTranscriptBuffer()
            client_receive = asyncio.create_task(websocket.receive())
            deepgram_receive = asyncio.create_task(deepgram.recv())
            try:
                while True:
                    completed, _ = await asyncio.wait(
                        {task for task in (client_receive, deepgram_receive) if task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if client_receive in completed:
                        message = client_receive.result()
                        if message["type"] == "websocket.disconnect":
                            break
                        audio = message.get("bytes")
                        text = message.get("text")
                        if audio is not None:
                            await deepgram.send(audio)
                        elif text is not None:
                            try:
                                command = json.loads(text)
                            except json.JSONDecodeError:
                                command = None
                            if isinstance(command, dict) and command.get("type") == "stop":
                                await deepgram.send(json.dumps({"type": "CloseStream"}))
                                client_receive = None
                                continue
                        client_receive = asyncio.create_task(websocket.receive())

                    if deepgram_receive in completed:
                        raw_result = deepgram_receive.result()
                        result_message = json.loads(raw_result)
                        if result_message.get("type") == "Error":
                            raise RuntimeError(
                                result_message.get("description", "Deepgram returned an error.")
                            )
                        progress = transcript_buffer.process(result_message)
                        if progress.preview:
                            await websocket.send_json(
                                {
                                    "type": "transcript",
                                    "text": progress.preview,
                                    "final": progress.complete is not None,
                                    "stable": progress.stable,
                                }
                            )
                        evaluated_stable_chunk = False
                        if progress.stable and progress.preview:
                            await _process_transcript(
                                watch_item,
                                TranscriptEvent(text=progress.preview),
                                context_text=progress.complete,
                                source="deepgram",
                                record_context=progress.complete is not None,
                            )
                            evaluated_stable_chunk = True
                        if progress.complete and not evaluated_stable_chunk:
                            await _process_transcript(
                                watch_item,
                                TranscriptEvent(text=progress.complete),
                                context_text=progress.complete,
                                source="deepgram",
                            )
                        deepgram_receive = asyncio.create_task(deepgram.recv())
            finally:
                for task in (client_receive, deepgram_receive):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (client_receive, deepgram_receive) if task),
                    return_exceptions=True,
                )
    except WebSocketDisconnect:
        return
    except Exception as error:
        _logger.exception("Deepgram audio stream failed")
        try:
            await websocket.send_json(
                {"type": "error", "detail": str(error)[:300]}
            )
            await websocket.close(code=1011, reason="Deepgram streaming failed.")
        except (RuntimeError, WebSocketDisconnect):
            pass

