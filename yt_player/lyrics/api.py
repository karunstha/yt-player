from fastapi import FastAPI

from yt_player.core.errors import ServiceError
from yt_player.lyrics.models import Lyrics, LyricsOffsetRequest
from yt_player.lyrics.service import LyricsService
from yt_player.player.controller import PlaybackController


def register_lyrics_routes(
    app: FastAPI, *, controller: PlaybackController, lyrics_service: LyricsService
) -> None:
    @app.get("/player/lyrics", response_model=Lyrics)
    async def current_lyrics():
        state = await controller.get_state()
        if state.track is None:
            raise ServiceError("NO_CURRENT_TRACK", "No track is loaded.", 409)
        return await lyrics_service.get(state.track)

    @app.get("/tracks/{track_id}/lyrics", response_model=Lyrics)
    async def track_lyrics(track_id: str):
        return await lyrics_service.get_by_id(track_id)

    @app.put("/tracks/{track_id}/lyrics/offset", response_model=Lyrics)
    async def set_lyrics_offset(track_id: str, req: LyricsOffsetRequest):
        return await lyrics_service.set_offset(track_id, req.offset_seconds)
