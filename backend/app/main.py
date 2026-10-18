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
from .matcher import match_transcript
from .models import Alert, ContractModel, MatchResult, TranscriptEvent, WatchItem
from .realtime import alert_connections


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


class TranscriptEvaluationResponse(ContractModel):
    watch_item: str
    transcript: TranscriptEvent
    match_result: MatchResult
    alert: Alert | None
    suppressed: bool


class DebugStateResponse(ContractModel):
    watch_item: str | None
    transcript: TranscriptEvent | None
    match_result: MatchResult | None
    alert: Alert | None
    suppressed: bool


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


@app.post(
    "/watch-item",
    response_model=SetWatchItemResponse,
    tags=["wait-for"],
)
def set_watch_item(item: WatchItem) -> SetWatchItemResponse:
    """Set the Wait For item for the current prototype session."""

    state.set_watch_item(item.text)
    return SetWatchItemResponse(success=True, watch_item=item.text)


@app.get(
    "/watch-item",
    response_model=GetWatchItemResponse,
    tags=["wait-for"],
)
def get_watch_item() -> GetWatchItemResponse:
    """Return the current Wait For item."""

    return GetWatchItemResponse(watch_item=state.get_watch_item())


@app.post(
    "/debug/transcript",
    response_model=TranscriptEvaluationResponse,
    tags=["debug"],
)
async def evaluate_transcript(event: TranscriptEvent) -> TranscriptEvaluationResponse:
    """Run manually supplied transcript text through the complete alert pipeline."""

    watch_item = state.get_watch_item()
    if watch_item is None:
        raise HTTPException(
            status_code=409,
            detail="Set a Watch Item before evaluating a transcript.",
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
    watch_item: str,
    event: TranscriptEvent,
) -> tuple[MatchResult, Alert | None, bool]:
    result = match_transcript(watch_item, event.text)
    emitted_alert: Alert | None = None
    suppressed = False

    if result.match:
        candidate = create_alert(result)
        suppressed = state.is_duplicate_alert(candidate)
        if not suppressed:
            emitted_alert = candidate
            state.save_alert(emitted_alert)
            await alert_connections.broadcast(emitted_alert)

    state.record_evaluation(event, result, emitted_alert, suppressed)
    return result, emitted_alert, suppressed


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

    if state.get_watch_item() is None:
        await reject(4409, "Set a Watch Item before listening.")
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
    if watch_item is None:
        await reject(4409, "Set a Watch Item before listening.")
        return

    try:
        async with connect_websocket(
            build_live_url(16000),
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
                        transcript = transcript_buffer.add(result_message)
                        if transcript:
                            await websocket.send_json(
                                {"type": "transcript", "text": transcript}
                            )
                            await _process_transcript(
                                watch_item,
                                TranscriptEvent(text=transcript),
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

