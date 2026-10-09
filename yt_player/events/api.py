from fastapi import FastAPI, WebSocket
from fastapi.encoders import jsonable_encoder

from yt_player.events.models import EventMessage
from yt_player.events.service import EventService
from yt_player.player.controller import PlaybackController


def register_events_routes(
    app: FastAPI, *, controller: PlaybackController, event_service: EventService
) -> None:
    @app.websocket("/events")
    async def playback_events(websocket: WebSocket):
        await event_service.connect(websocket)
        state = await controller.get_state()
        await websocket.send_json(
            jsonable_encoder(
                EventMessage(type="playback.sync", data=state.model_dump(mode="json"))
            )
        )
        await event_service.listen_until_disconnect(websocket)
