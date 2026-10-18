"""Integration tests for the initial FastAPI surface."""

import pytest
from fastapi.testclient import TestClient

from backend.app import state
from backend.app.main import app


@pytest.fixture(autouse=True)
def reset_in_memory_state() -> None:
    state.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_root_identifies_service(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Hearo"}


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Hearo"}


def test_watch_item_is_initially_unset(client: TestClient) -> None:
    response = client.get("/watch-item")

    assert response.status_code == 200
    assert response.json() == {"watch_item": None}


def test_set_then_get_watch_item(client: TestClient) -> None:
    set_response = client.post(
        "/watch-item",
        json={"text": "Flight 312 to Buffalo"},
    )

    assert set_response.status_code == 200
    assert set_response.json() == {
        "success": True,
        "watch_item": "Flight 312 to Buffalo",
    }

    get_response = client.get("/watch-item")
    assert get_response.status_code == 200
    assert get_response.json() == {"watch_item": "Flight 312 to Buffalo"}

    intent_response = client.get("/watch-item/intent")
    assert intent_response.status_code == 200
    intent = intent_response.json()["intent"]
    assert intent["identifiers"][0]["value"] == "312"
    assert "flight" in intent["context_terms"]


def test_set_watch_item_trims_text(client: TestClient) -> None:
    response = client.post("/watch-item", json={"text": "  Order number 47  "})

    assert response.status_code == 200
    assert response.json()["watch_item"] == "Order number 47"


def test_lists_apple_backed_sound_presets(client: TestClient) -> None:
    response = client.get("/sound-presets")

    assert response.status_code == 200
    presets = {preset["id"]: preset for preset in response.json()["presets"]}
    assert "doorbell" in presets
    assert "doorbell" in presets["doorbell"]["apple_classifications"]
    assert "siren" in presets


def test_sets_watch_item_from_presets_without_free_text(client: TestClient) -> None:
    response = client.post(
        "/watch-item",
        json={"text": "", "preset_sounds": ["doorbell", "siren"]},
    )

    assert response.status_code == 200
    assert response.json()["watch_item"] == "Doorbell or Siren"
    intent = client.get("/watch-item/intent").json()["intent"]
    assert [target["label"] for target in intent["sound_targets"]] == [
        "doorbell",
        "siren",
    ]
    assert intent["speech_targets"] == []


def test_merges_preset_with_free_text_intent(client: TestClient) -> None:
    response = client.post(
        "/watch-item",
        json={"text": "Someone calling Dana", "preset_sounds": ["doorbell"]},
    )

    assert response.status_code == 200
    intent = client.get("/watch-item/intent").json()["intent"]
    assert intent["sound_targets"][0]["label"] == "doorbell"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "", "preset_sounds": ["not_an_apple_preset"]},
        {"text": "Order 47", "unexpected": True},
    ],
)
def test_set_watch_item_rejects_invalid_payloads(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    response = client.post("/watch-item", json=payload)

    assert response.status_code == 422
    assert state.get_watch_item() is None


def test_transcript_requires_watch_item(client: TestClient) -> None:
    response = client.post(
        "/debug/transcript",
        json={"text": "Order 47 is ready for pickup."},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Set a Watch Item or start Rest Mode before evaluating a transcript."
    )


def test_matching_transcript_emits_alert(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "My coffee order number 47"})

    response = client.post(
        "/debug/transcript",
        json={"text": "Order 47 is ready for pickup."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["match_result"]["match"] is True
    assert body["alert"]["category"] == "watchlist_match"
    assert body["alert"]["detail"] == "Ready for pickup"
    assert body["suppressed"] is False


def test_nonmatching_transcript_does_not_emit_alert(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "My coffee order number 47"})

    response = client.post(
        "/debug/transcript",
        json={"text": "Order 74 is ready for pickup."},
    )

    body = response.json()
    assert body["match_result"]["match"] is False
    assert body["alert"] is None
    assert body["suppressed"] is False


def test_matching_environmental_sound_emits_alert(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Alert me when the doorbell rings"})

    response = client.post(
        "/events/sound",
        json={"label": "door chime", "confidence": 0.91, "source": "simulator"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["match_result"]["match"] is True
    assert body["match_result"]["event_type"] == "sound_doorbell"
    assert body["alert"]["detail"] == "Doorbell detected"
    assert body["sound_event"]["source"] == "simulator"


def test_unrequested_or_uncertain_sound_does_not_alert(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Alert me when the doorbell rings"})

    wrong_sound = client.post(
        "/events/sound",
        json={"label": "siren", "confidence": 0.95},
    )
    uncertain = client.post(
        "/events/sound",
        json={"label": "doorbell", "confidence": 0.42},
    )

    assert wrong_sound.json()["match_result"]["match"] is False
    assert uncertain.json()["match_result"]["match"] is False
    assert uncertain.json()["alert"] is None


def test_sound_event_requires_watch_item(client: TestClient) -> None:
    response = client.post(
        "/events/sound",
        json={"label": "doorbell", "confidence": 0.9},
    )

    assert response.status_code == 409


def test_repeated_sound_event_emits_each_time(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Wait for the doorbell"})
    payload = {"label": "doorbell", "confidence": 0.9}

    first = client.post("/events/sound", json=payload)
    repeated = client.post("/events/sound", json=payload)

    assert first.json()["alert"] is not None
    assert repeated.json()["alert"] is not None
    assert repeated.json()["suppressed"] is False


def test_repeated_transcript_emits_each_time(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Raffle number 82"})
    payload = {"text": "Raffle number 82 has been called."}

    first = client.post("/debug/transcript", json=payload)
    repeated = client.post("/debug/transcript", json=payload)

    assert first.json()["alert"] is not None
    assert first.json()["suppressed"] is False
    assert repeated.json()["alert"] is not None
    assert repeated.json()["suppressed"] is False


def test_debug_state_reports_latest_evaluation(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Train A215 to Seattle"})
    client.post(
        "/debug/transcript",
        json={"text": "Train A215 to Seattle is now boarding at platform 4."},
    )

    response = client.get("/debug/state")

    assert response.status_code == 200
    body = response.json()
    assert body["watch_item"] == "Train A215 to Seattle"
    assert body["transcript"]["text"].startswith("Train A215")
    assert body["match_result"]["event_type"] == "boarding"
    assert body["alert"] is not None


def test_dashboard_is_served(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Hearo Debug Dashboard" in response.text
    assert "/ws/alerts" in response.text
    assert "/events/sound" in response.text
    assert "Gemini listening plan" in response.text
    assert "What I missed" in response.text


def test_websocket_receives_emitted_alert(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Raffle number 82"})

    with client.websocket_connect("/ws/alerts") as websocket:
        response = client.post(
            "/debug/transcript",
            json={"text": "Raffle number 82 has been called."},
        )
        message = websocket.receive_json()

    assert response.status_code == 200
    assert message["id"] == response.json()["alert"]["id"]
    assert message["category"] == "watchlist_match"
    assert message["detail"] == "Now being called"


def test_rest_mode_collects_broad_context_and_returns_recap(client: TestClient) -> None:
    started = client.post("/rest-mode/start")
    assert started.status_code == 200
    assert started.json()["status"] == "active"

    speech = client.post(
        "/debug/transcript",
        json={"text": "The meeting moved to the room across the hall."},
    )
    sound = client.post(
        "/events/sound",
        json={"label": "doorbell", "confidence": 0.91, "source": "simulator"},
    )
    assert speech.status_code == 200
    assert speech.json()["match_result"]["match"] is False
    assert sound.status_code == 200

    active = client.get("/rest-mode").json()["active"]
    assert active["event_count"] == 2

    stopped = client.post("/rest-mode/stop")
    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "ended"
    assert body["event_count"] == 2
    assert body["recap"]["provider"] == "local"
    assert body["recap"]["conversations_and_announcements"] == [
        "The meeting moved to the room across the hall."
    ]
    assert body["recap"]["environmental_sounds"] == ["doorbell"]
    assert client.get("/rest-mode").json()["active"] is None


def test_rest_mode_rejects_duplicate_start_and_stop_when_inactive(
    client: TestClient,
) -> None:
    assert client.post("/rest-mode/stop").status_code == 409
    assert client.post("/rest-mode/start").status_code == 200
    assert client.post("/rest-mode/start").status_code == 409
