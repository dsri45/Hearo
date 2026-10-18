"""WebSocket connection management for real-time alert delivery."""

from fastapi import WebSocket

from .models import Alert


class AlertConnectionManager:
    """Track connected clients and broadcast emitted alerts."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, alert: Alert) -> None:
        payload = alert.model_dump(mode="json")
        disconnected: list[WebSocket] = []
        for websocket in tuple(self._connections):
            try:
                await websocket.send_json(payload)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket)


alert_connections = AlertConnectionManager()
