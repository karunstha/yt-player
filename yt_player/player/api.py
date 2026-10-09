from datetime import datetime

from fastapi import FastAPI, Query

from yt_player.player.controller import PlaybackController
from yt_player.player.models import (
    PlaybackHistoryPage,
    PlaybackState,
    PlayRequest,
    RepeatRequest,
    SeekRequest,
    ShuffleRequest,
    Track,
    VolumeRequest,
)


def register_player_routes(app: FastAPI, *, controller: PlaybackController) -> None:
    @app.post("/player/play", response_model=PlaybackState)
    async def player_play(req: PlayRequest):
        return await controller.play(req)

    @app.post("/player/pause", response_model=PlaybackState)
    async def player_pause():
        return await controller.pause()

    @app.post("/player/resume", response_model=PlaybackState)
    async def player_resume():
        return await controller.resume()

    @app.post("/player/stop", response_model=PlaybackState)
    async def player_stop():
        return await controller.stop()

    @app.post("/player/seek", response_model=PlaybackState)
    async def player_seek(req: SeekRequest):
        return await controller.seek(req.position_seconds)

    @app.post("/player/next", response_model=PlaybackState)
    async def player_next():
        return await controller.next()

    @app.post("/player/previous", response_model=PlaybackState)
    async def player_previous():
        return await controller.previous()

    @app.post("/player/volume", response_model=PlaybackState)
    async def player_volume(req: VolumeRequest):
        return await controller.set_volume(req.volume)

    @app.post("/player/shuffle", response_model=PlaybackState)
    async def player_shuffle(req: ShuffleRequest):
        return await controller.set_shuffle(req.shuffle)

    @app.post("/player/repeat", response_model=PlaybackState)
    async def player_repeat(req: RepeatRequest):
        return await controller.set_repeat_mode(req.repeat_mode)

    @app.get("/player/state", response_model=PlaybackState)
    async def player_state():
        return await controller.get_state()

    @app.get("/player/current", response_model=Track)
    async def player_current():
        return await controller.current_track()

    @app.get("/player/history", response_model=PlaybackHistoryPage)
    async def player_history(
        limit: int = Query(default=100, ge=1, le=500),
        days: int = Query(default=7, ge=1, le=31),
        before: datetime | None = None,
    ):
        return controller.history(limit=limit, days=days, before=before)
