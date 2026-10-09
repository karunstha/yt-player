import asyncio

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder
from starlette.websockets import WebSocketDisconnect

from yt_player.events.models import EventMessage


class EventService:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def listen_until_disconnect(self, websocket: WebSocket) -> None:
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            await self.disconnect(websocket)

    async def broadcast(self, event_type: str, data: dict) -> None:
        event = EventMessage(type=event_type, data=data)
        payload = jsonable_encoder(event)
        async with self._lock:
            connections = list(self._connections)

        stale = []
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)

        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections.discard(websocket)
