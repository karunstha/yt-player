from fastapi import FastAPI, Response, status

from yt_player.events.service import EventService
from yt_player.player.controller import PlaybackController
from yt_player.player.models import AddTrackRequest, PlaybackState
from yt_player.playlists.models import (
    AddTracksRequest,
    CreatePlaylistRequest,
    MoveTrackRequest,
    Playlist,
    PlaylistAddResult,
    PlaylistExport,
    PlaylistImportRequest,
    PlaylistImportResult,
    PlayPlaylistRequest,
    ReorderPlaylistRequest,
    UpdatePlaylistRequest,
)
from yt_player.playlists.service import PlaylistService


def register_playlist_routes(
    app: FastAPI,
    *,
    controller: PlaybackController,
    playlist_service: PlaylistService,
    event_service: EventService,
) -> None:
    @app.get("/playlists/export", response_model=PlaylistExport)
    async def playlists_export_all():
        return playlist_service.export_all()

    @app.post("/playlists/import", response_model=PlaylistImportResult)
    async def playlists_import(req: PlaylistImportRequest):
        result = playlist_service.import_playlists(req)
        await event_service.broadcast(
            "playlist.changed",
            {"playlists": [p.model_dump(mode="json") for p in result.imported]},
        )
        return result

    @app.post("/playlists", response_model=Playlist, status_code=status.HTTP_201_CREATED)
    async def playlists_create(req: CreatePlaylistRequest):
        playlist = playlist_service.create_playlist(req.name, req.description)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.get("/playlists")
    async def playlists_list():
        return {"playlists": playlist_service.list_playlists()}

    @app.get("/playlists/{playlist_id}/export", response_model=PlaylistExport)
    async def playlists_export_one(playlist_id: str):
        return playlist_service.export_playlist(playlist_id)

    @app.get("/playlists/{playlist_id}", response_model=Playlist)
    async def playlists_get(playlist_id: str):
        return playlist_service.get_playlist(playlist_id)

    @app.patch("/playlists/{playlist_id}", response_model=Playlist)
    async def playlists_update(playlist_id: str, req: UpdatePlaylistRequest):
        existing = playlist_service.get_playlist(playlist_id)
        name = req.name if "name" in req.model_fields_set else existing.name
        description = (
            req.description if "description" in req.model_fields_set else existing.description
        )
        playlist = playlist_service.update_playlist(
            playlist_id, name=name, description=description
        )
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.delete("/playlists/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def playlists_delete(playlist_id: str):
        playlist_service.delete_playlist(playlist_id)
        await event_service.broadcast("playlist.changed", {"playlist_id": playlist_id})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/playlists/{playlist_id}/tracks", response_model=Playlist)
    async def playlists_add_track(playlist_id: str, req: AddTrackRequest):
        track = await controller.resolve_track(
            url=req.url,
            query=req.query,
            track=req.track,
            requested_by=req.requested_by,
            search_type=req.search_type,
        )
        playlist = playlist_service.add_track(playlist_id, track)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.post("/playlists/{playlist_id}/tracks/current", response_model=Playlist)
    async def playlists_add_current_track(playlist_id: str):
        track = await controller.current_track()
        playlist = playlist_service.add_track(playlist_id, track)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.post("/playlists/{playlist_id}/tracks/bulk", response_model=PlaylistAddResult)
    async def playlists_add_tracks(playlist_id: str, req: AddTracksRequest):
        playlist_service.get_playlist(playlist_id)  # 404 before resolving anything
        tracks, failed = await controller.resolve_tracks(req.items)
        result = playlist_service.add_tracks(playlist_id, tracks, failed)
        if result.added:
            await event_service.broadcast(
                "playlist.changed", {"playlist": result.playlist.model_dump(mode="json")}
            )
        return result

    @app.post("/playlists/{playlist_id}/tracks/queue", response_model=PlaylistAddResult)
    async def playlists_add_queue(playlist_id: str):
        playlist_service.get_playlist(playlist_id)
        result = playlist_service.add_tracks(playlist_id, await controller.queue_tracks())
        if result.added:
            await event_service.broadcast(
                "playlist.changed", {"playlist": result.playlist.model_dump(mode="json")}
            )
        return result

    @app.post("/playlists/{playlist_id}/tracks/{track_id}/move", response_model=Playlist)
    async def playlists_move_track(playlist_id: str, track_id: str, req: MoveTrackRequest):
        playlist = playlist_service.move_track(playlist_id, track_id, req.position)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.post("/playlists/{playlist_id}/tracks/reorder", response_model=Playlist)
    async def playlists_reorder_tracks(playlist_id: str, req: ReorderPlaylistRequest):
        playlist = playlist_service.reorder_tracks(playlist_id, req.track_ids)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.delete("/playlists/{playlist_id}/tracks", response_model=Playlist)
    async def playlists_clear_tracks(playlist_id: str):
        playlist = playlist_service.clear_playlist(playlist_id)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.delete("/playlists/{playlist_id}/tracks/{track_id}", response_model=Playlist)
    async def playlists_remove_track(playlist_id: str, track_id: str):
        playlist = playlist_service.remove_track(playlist_id, track_id)
        await event_service.broadcast(
            "playlist.changed", {"playlist": playlist.model_dump(mode="json")}
        )
        return playlist

    @app.post("/playlists/{playlist_id}/play", response_model=PlaybackState)
    async def playlists_play(playlist_id: str, req: PlayPlaylistRequest | None = None):
        req = req or PlayPlaylistRequest()
        return await controller.play_playlist(
            playlist_id,
            shuffle=req.shuffle,
            start_index=req.start_index,
            bluetooth_device_id=req.bluetooth_device_id,
        )

    @app.post("/playlists/{playlist_id}/shuffle", response_model=PlaybackState)
    async def playlists_shuffle(playlist_id: str, req: PlayPlaylistRequest | None = None):
        req = req or PlayPlaylistRequest(shuffle=True)
        return await controller.play_playlist(
            playlist_id,
            shuffle=True,
            start_index=req.start_index,
            bluetooth_device_id=req.bluetooth_device_id,
        )
