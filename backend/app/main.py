"""FastAPI entry point for the Hearo backend."""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from websockets.asyncio.client import connect as connect_websocket

from . import state
from .alerts import create_alert
from .deepgram import FinalTranscriptBuffer, build_live_url
from .intent import extract_intent, local_intent, match_transcript_with_gemini
from .models import (
    AddWatchlistItem,
    Alert,
    AlertCategory,
    AlertPreferences,
    ContractModel,
    DetectedSoundEvent,
    MatchResult,
    RestModeEvent,
    RestModeSession,
    SoundPreset,
    SoundTarget,
    StructuredWatchIntent,
    TranscriptEvent,
    WatchItem,
    WatchlistRecord,
    WatchlistToggle,
    WatchlistUpdateResponse,
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
_APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.html"
_WATCH_PREVIEW_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "watch-preview.html"
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


class GetAlertsResponse(ContractModel):
    alerts: list[Alert]


class ClearAlertsResponse(ContractModel):
    cleared: int


class GetWatchlistResponse(ContractModel):
    items: list[WatchlistRecord]


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


class RestModeEventsResponse(ContractModel):
    events: list[RestModeEvent]


_CONFIDENCE_THRESHOLDS = {"early": 0.25, "balanced": 0.50, "certain": 0.80}
def _active_watchlist_items() -> list[WatchlistRecord]:
    now = time.time()
    return [
        item
        for item in state.list_watchlist_items()
        if item.enabled and (item.expires_at is None or item.expires_at > now)
    ]


def _always_on_sound_targets() -> list[tuple[str, SoundTarget]]:
    preferences = state.get_alert_preferences()
    targets = [
        ("Smoke alarm", SoundTarget(label="smoke_alarm", aliases=["smoke alarm", "smoke detector"])),
        ("Fire alarm", SoundTarget(label="fire_alarm", aliases=["fire alarm"])),
    ]
    if preferences.always_on_sirens_and_horns:
        targets.append(("Car horn", SoundTarget(label="car_horn", aliases=["vehicle horn", "honking"])))
    if preferences.always_on_doorbell_and_knocking:
        targets.extend([
            ("Doorbell", SoundTarget(label="doorbell", aliases=["door bell", "door chime", "bell", "buzzer"])),
            ("Door knock", SoundTarget(label="door_knock", aliases=["knock", "knocking"])),
        ])
    if preferences.always_on_glass_breaking:
        targets.append(("Glass breaking", SoundTarget(label="glass_breaking", aliases=["breaking glass", "glass shatter", "shattering glass"])))
    return targets


def _minimum_alert_confidence() -> float:
    return _CONFIDENCE_THRESHOLDS[state.get_alert_preferences().confidence]


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


@app.get("/app", include_in_schema=False)
def app_page() -> FileResponse:
    """Serve the Hearo UI supplied for backend integration."""

    return FileResponse(_APP_PATH)


@app.get("/watch-preview", include_in_schema=False)
def watch_preview_page() -> FileResponse:
    """Serve a browser preview of the Hearo Watch alert screen."""

    return FileResponse(_WATCH_PREVIEW_PATH)


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


@app.get("/alerts", response_model=GetAlertsResponse, tags=["events"])
def get_alerts() -> GetAlertsResponse:
    """Return recent persisted alerts so clients can restore their alert view."""

    return GetAlertsResponse(alerts=state.list_alerts())


@app.delete("/alerts", response_model=ClearAlertsResponse, tags=["events"])
def clear_alerts() -> ClearAlertsResponse:
    return ClearAlertsResponse(cleared=state.clear_alerts())


@app.get("/watchlist", response_model=GetWatchlistResponse, tags=["wait-for"])
def get_watchlist() -> GetWatchlistResponse:
    return GetWatchlistResponse(items=state.list_watchlist_items())


@app.post("/watchlist", response_model=WatchlistRecord, tags=["wait-for"])
def add_watchlist_item(item: AddWatchlistItem) -> WatchlistRecord:
    preset_ids = [str(preset_id) for preset_id in item.preset_sounds]
    known_presets = {str(preset.id): preset.title for preset in SOUND_PRESETS}
    titles = [known_presets.get(preset_id, preset_id.replace("_", " ").title()) for preset_id in preset_ids]
    display_text = item.text or (", ".join(titles) if len(titles) != 2 else " or ".join(titles))
    if item.text and item.kind == "any":
        # Saving a generic Watch List entry must not wait on Gemini. The raw
        # request is sent to Gemini when matching live transcripts; local
        # intent extraction here supplies only Deepgram recognition hints.
        intent = local_intent(item.text)
    elif item.text:
        intent = extract_intent(item.text)
    else:
        intent = StructuredWatchIntent(subject=display_text)
    existing = {normalize_sound_label(target.label) for target in intent.sound_targets}
    targets = list(intent.sound_targets)
    for preset_id in preset_ids:
        target = (
            sound_target_for_preset(preset_id)
            if preset_id in known_presets
            else SoundTarget(label=preset_id.replace("_", " "))
        )
        normalized = normalize_sound_label(target.label)
        if normalized not in existing:
            targets.append(target)
            existing.add(normalized)
    intent = intent.model_copy(update={"sound_targets": targets})
    record = state.add_watchlist_item(
        str(uuid.uuid4()),
        display_text,
        item.kind,
        preset_ids,
        intent.model_dump(mode="json"),
        item.expires_at,
    )
    state.set_watch_item(display_text, intent.model_dump(mode="json"))
    return record


@app.patch(
    "/watchlist/{item_id}",
    response_model=WatchlistUpdateResponse,
    tags=["wait-for"],
)
def toggle_watchlist_item(item_id: str, update: WatchlistToggle) -> WatchlistUpdateResponse:
    if not state.set_watchlist_item_enabled(item_id, update.enabled):
        raise HTTPException(status_code=404, detail="Watchlist item not found.")
    return WatchlistUpdateResponse(success=True)


@app.delete(
    "/watchlist/{item_id}",
    response_model=WatchlistUpdateResponse,
    tags=["wait-for"],
)
def remove_watchlist_item(item_id: str) -> WatchlistUpdateResponse:
    if not state.delete_watchlist_item(item_id):
        raise HTTPException(status_code=404, detail="Watchlist item not found.")
    return WatchlistUpdateResponse(success=True)


@app.get("/alert-preferences", response_model=AlertPreferences, tags=["alerts"])
def get_alert_preferences() -> AlertPreferences:
    return state.get_alert_preferences()


@app.put("/alert-preferences", response_model=AlertPreferences, tags=["alerts"])
def set_alert_preferences(preferences: AlertPreferences) -> AlertPreferences:
    return state.set_alert_preferences(preferences)


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


@app.get("/rest-mode/events", response_model=RestModeEventsResponse, tags=["rest-mode"])
def get_rest_mode_events() -> RestModeEventsResponse:
    active = state.get_active_rest_session()
    return RestModeEventsResponse(
        events=[] if active is None else state.get_rest_events(active.id)
    )


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
    if not _active_watchlist_items() and state.get_active_rest_session() is None:
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
    matched_targets: set[str] | None = None,
    matched_results: dict[str, MatchResult] | None = None,
) -> tuple[MatchResult, Alert | None, bool]:
    now = time.time()
    all_items = state.list_watchlist_items()
    items = [
        item
        for item in all_items
        if item.enabled and (item.expires_at is None or item.expires_at > now)
    ]
    targets = [
        (item.text, item.kind)
        for item in items
        if not item.preset_sounds
        and not (
            item.intent is not None
            and item.intent.sound_targets
            and not item.intent.speech_targets
        )
    ]
    if not all_items and watch_item is not None:
        legacy_intent = state.get_watch_intent()
        if not (
            legacy_intent is not None
            and legacy_intent.sound_targets
            and not legacy_intent.speech_targets
        ):
            targets.append((watch_item, "any"))

    matches: list[tuple[MatchResult, Alert | None, bool]] = []
    misses: list[MatchResult] = []
    for target, target_kind in targets:
        if matched_targets is not None and target in matched_targets:
            previous_match = (matched_results or {}).get(target)
            if previous_match is not None:
                matches.append((previous_match, None, True))
            continue
        if target_kind == "name":
            name_heard = re.search(
                rf"(?<![\w]){re.escape(target)}(?![\w])", event.text, re.IGNORECASE
            ) is not None
            candidate = (
                MatchResult(
                    match=True,
                    confidence=0.99,
                    event_type="name_detected",
                    title="Name detected",
                    detail=event.text.strip(),
                    reason=f"The transcript included the profile name {target}.",
                )
                if name_heard
                else MatchResult(
                    match=False,
                    confidence=0,
                    reason="The profile name was not present in this transcript.",
                )
            )
        else:
            try:
                candidate = await asyncio.to_thread(
                    match_transcript_with_gemini,
                    target,
                    event.text,
                )
            except Exception:
                _logger.exception("Gemini transcript comparison failed")
                candidate = MatchResult(
                    match=False,
                    confidence=0,
                    reason="Gemini could not compare this transcript with the Watch List.",
                )
        if candidate.match:
            if target_kind != "name":
                candidate = candidate.model_copy(update={"title": target})
            # Use a finalized Deepgram utterance so alerts include surrounding
            # words and each utterance generates only one Gemini comparison.
            if target_kind == "name" and source == "deepgram" and not record_context:
                continue
            if matched_targets is not None:
                matched_targets.add(target)
            if matched_results is not None:
                matched_results[target] = candidate
            emitted, was_suppressed = await _emit_match(candidate)
            matches.append((candidate, emitted, was_suppressed))
        else:
            misses.append(candidate)
    if matches:
        result, emitted_alert, suppressed = matches[0]
    else:
        result = misses[0] if misses else MatchResult(
            match=False,
            confidence=0,
            reason="No enabled generic Watch List items are configured for speech matching.",
        )
        emitted_alert, suppressed = None, False
    if record_context:
        state.record_rest_transcript(
            context_text or event.text,
            important=bool(matches)
            or bool(
                matched_targets
                and any(target in matched_targets for target in targets)
            ),
            source=source,
        )
    state.record_evaluation(event, result, emitted_alert, suppressed)
    return result, emitted_alert, suppressed


async def _emit_match(
    result: MatchResult,
    category: AlertCategory | None = None,
) -> tuple[Alert | None, bool]:
    """Emit a canonical alert for either speech or environmental sound."""

    emitted_alert: Alert | None = None
    suppressed = False

    if result.match:
        candidate = create_alert(result)
        if category is not None:
            candidate = candidate.model_copy(update={"category": category})
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
    items = _active_watchlist_items()
    threshold = _minimum_alert_confidence()
    candidates: list[tuple[str, MatchResult]] = []
    for item in items:
        if item.intent is not None and item.intent.sound_targets:
            result = match_sound_event(
                item.text,
                event,
                item.intent,
                minimum_confidence=threshold,
            )
            candidates.append((item.text, result))
    for title, target in _always_on_sound_targets():
        intent = StructuredWatchIntent(subject=title, sound_targets=[target])
        candidates.append((title, match_sound_event(title, event, intent, threshold)))

    emitted_matches: list[tuple[MatchResult, Alert | None, bool]] = []
    emitted_semantics: set[tuple[str | None, str | None, str | None]] = set()
    for _, result in candidates:
        semantic = (result.event_type, result.title, result.detail)
        if result.match and semantic not in emitted_semantics:
            emitted_semantics.add(semantic)
            alert_category = (
                AlertCategory.URGENT
                if result.event_type in {
                    "sound_smoke_alarm",
                    "sound_fire_alarm",
                    "sound_siren",
                }
                else None
            )
            emitted, was_suppressed = await _emit_match(result, alert_category)
            emitted_matches.append((result, emitted, was_suppressed))
    if emitted_matches:
        result, emitted_alert, suppressed = emitted_matches[0]
    else:
        result = candidates[0][1] if candidates else MatchResult(
            match=False,
            confidence=event.confidence,
            reason="No sound target matched this event.",
        )
        emitted_alert, suppressed = None, False
    state.record_rest_sound(event, important=bool(emitted_matches))
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

    if not _active_watchlist_items() and state.get_active_rest_session() is None:
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
        keyterms = list(dict.fromkeys(
            term
            for item in _active_watchlist_items()
            if item.intent is not None
            for term in item.intent.keyterms
        ))[:100]
        async with connect_websocket(
            build_live_url(16000, keyterms=keyterms),
            additional_headers={"Authorization": f"Token {api_key}"},
            max_size=None,
        ) as deepgram:
            await websocket.send_json({"type": "ready"})
            transcript_buffer = FinalTranscriptBuffer()
            matched_targets: set[str] = set()
            matched_results: dict[str, MatchResult] = {}
            finalized_matches: deque[str] = deque()
            match_ready = asyncio.Event()
            matcher_stopping = False

            async def process_match_queue() -> None:
                """Run Gemini separately so matching never stalls audio forwarding."""
                while True:
                    await match_ready.wait()
                    if finalized_matches:
                        text = finalized_matches.popleft()
                    else:
                        match_ready.clear()
                        if matcher_stopping:
                            return
                        continue

                    await _process_transcript(
                        watch_item,
                        TranscriptEvent(text=text),
                        context_text=text,
                        source="deepgram",
                        record_context=True,
                        matched_targets=matched_targets,
                        matched_results=matched_results,
                    )
                    matched_targets.clear()
                    matched_results.clear()

                    if not finalized_matches:
                        match_ready.clear()

            matcher_task = asyncio.create_task(process_match_queue())
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
                        if progress.complete:
                            finalized_matches.append(progress.complete)
                            match_ready.set()
                        deepgram_receive = asyncio.create_task(deepgram.recv())
            finally:
                matcher_stopping = True
                match_ready.set()
                for task in (client_receive, deepgram_receive):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (client_receive, deepgram_receive) if task),
                    return_exceptions=True,
                )
                try:
                    await asyncio.wait_for(asyncio.shield(matcher_task), timeout=5)
                except asyncio.TimeoutError:
                    matcher_task.cancel()
                    await asyncio.gather(matcher_task, return_exceptions=True)
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

