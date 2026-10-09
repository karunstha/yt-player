from fastapi import FastAPI

from yt_player.player.controller import PlaybackController
from yt_player.player.models import (
    AddTrackRequest,
    QueuePlaylistRequest,
    QueueReplaceRequest,
    QueueState,
)
from yt_player.player.queue_service import QueueService


def register_queue_routes(
    app: FastAPI, *, controller: PlaybackController, queue_service: QueueService
) -> None:
    @app.get("/queue", response_model=QueueState)
    async def queue_get():
        return await queue_service.snapshot()

    @app.post("/queue", response_model=QueueState)
    async def queue_replace(req: QueueReplaceRequest):
        return await controller.replace_queue(req)

    @app.delete("/queue", response_model=QueueState)
    async def queue_clear():
        return await controller.clear_queue()

    @app.post("/queue/tracks", response_model=QueueState)
    async def queue_append_track(req: AddTrackRequest):
        return await controller.append_queue_track(req)

    @app.post("/queue/tracks/next", response_model=QueueState)
    async def queue_insert_track_next(req: AddTrackRequest):
        return await controller.append_queue_track(req, insert_next=True)

    @app.post("/queue/playlists/{playlist_id}", response_model=QueueState)
    async def queue_add_playlist(playlist_id: str, req: QueuePlaylistRequest | None = None):
        req = req or QueuePlaylistRequest()
        return await controller.queue_playlist(
            playlist_id, play_next=req.play_next, shuffle=req.shuffle
        )

    @app.delete("/queue/items/{item_id}", response_model=QueueState)
    async def queue_remove_item(item_id: str):
        return await controller.remove_queue_item(item_id)
