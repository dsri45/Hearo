"""FastAPI entry point for the Hearo backend."""

from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import state
from .alerts import create_alert
from .matcher import match_transcript
from .models import Alert, ContractModel, MatchResult, TranscriptEvent, WatchItem
from .realtime import alert_connections


_DASHBOARD_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "index.html"


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

    result = match_transcript(watch_item, event.text)
    emitted_alert: Alert | None = None
    suppressed = False

    if result.match:
        candidate = create_alert(result)
        suppressed = state.alert_deduplicator.check_and_record(candidate)
        if not suppressed:
            emitted_alert = candidate
            await alert_connections.broadcast(emitted_alert)

    state.record_evaluation(event, result, emitted_alert, suppressed)
    return TranscriptEvaluationResponse(
        watch_item=watch_item,
        transcript=event,
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
        watch_item=state.current_watch_item,
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

