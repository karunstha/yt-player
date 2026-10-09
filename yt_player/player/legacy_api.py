import random

from fastapi import FastAPI

from yt_player.player.controller import PlaybackController
from yt_player.player.models import PlaybackState, PlayRequest, Track, TrackInput
from yt_player.sources.models import SearchRequest
from yt_player.sources.youtube import music_search


def register_legacy_routes(app: FastAPI, *, controller: PlaybackController) -> None:
    @app.post("/play")
    async def legacy_play(req: PlayRequest):
        state = await controller.play(req)
        return {
            "status": state.state,
            "url": state.track.url if state.track else req.url,
            "state": state,
        }

    @app.post("/stop")
    async def legacy_stop():
        await controller.stop()
        return {"status": "stopped"}

    @app.get("/status")
    async def legacy_status():
        state = await controller.get_state()
        return {
            "playing": state.state == "playing",
            "url": state.track.url if state.track else None,
            "state": state,
        }

    @app.post("/search")
    def legacy_search(req: SearchRequest):
        return music_search(req.query, req.count)

    @app.post("/play-search")
    async def legacy_play_search(req: SearchRequest):
        results = await controller.search(req.query, 20, requested_by=req.requested_by)
        if not results.results:
            return {"error": "No tracks found"}
        pick = results.results[0]
        state = await controller.play(
            PlayRequest(
                track=track_to_input(pick),
                bluetooth_device_id=req.bluetooth_device_id,
            )
        )
        return legacy_track_response(state.state, pick, state)

    @app.post("/shuffle")
    async def legacy_shuffle(req: SearchRequest):
        results = await controller.search(req.query, 30, requested_by=req.requested_by)
        if not results.results:
            return {"error": "No tracks found"}
        pick = random.choice(results.results)
        state = await controller.play(
            PlayRequest(
                track=track_to_input(pick),
                bluetooth_device_id=req.bluetooth_device_id,
            )
        )
        return legacy_track_response(state.state, pick, state, mode="shuffle")


def track_to_input(track: Track) -> TrackInput:
    return TrackInput(**track.model_dump())


def legacy_track_response(
    status_name: str, track: Track, state: PlaybackState, mode: str | None = None
) -> dict:
    payload = {
        "status": status_name,
        "title": track.title,
        "artists": [track.artist] if track.artist else [],
        "album": None,
        "duration": track.duration_seconds,
        "url": track.url,
        "state": state,
    }
    if mode is not None:
        payload["mode"] = mode
    return payload
