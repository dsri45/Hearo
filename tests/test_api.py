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


def test_set_watch_item_trims_text(client: TestClient) -> None:
    response = client.post("/watch-item", json={"text": "  Order number 47  "})

    assert response.status_code == 200
    assert response.json()["watch_item"] == "Order number 47"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
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
        "Set a Watch Item before evaluating a transcript."
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


def test_repeated_transcript_is_suppressed(client: TestClient) -> None:
    client.post("/watch-item", json={"text": "Raffle number 82"})
    payload = {"text": "Raffle number 82 has been called."}

    first = client.post("/debug/transcript", json=payload)
    repeated = client.post("/debug/transcript", json=payload)

    assert first.json()["alert"] is not None
    assert first.json()["suppressed"] is False
    assert repeated.json()["alert"] is None
    assert repeated.json()["suppressed"] is True


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
