from fastapi import FastAPI, Query

from yt_player.player.controller import PlaybackController
from yt_player.player.models import SearchType
from yt_player.sources.models import SearchResponse


def register_search_routes(app: FastAPI, *, controller: PlaybackController) -> None:
    @app.get("/search", response_model=SearchResponse)
    async def search_tracks(
        q: str = Query(min_length=1),
        count: int = Query(default=10, ge=1, le=50),
        requested_by: str | None = None,
        type: SearchType | None = Query(
            default=None,
            description="songs, music_videos, podcasts, or videos (any YouTube video). "
            "Defaults to DEFAULT_SEARCH_TYPE.",
        ),
    ):
        return await controller.search(q, count, requested_by=requested_by, search_type=type)
