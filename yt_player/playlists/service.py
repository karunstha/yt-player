from datetime import datetime

from yt_player.core.errors import ServiceError
from yt_player.player.models import PlaybackHistoryEntry, PlaybackHistoryStatus, Track
from yt_player.playlists.models import (
    Playlist,
    PlaylistAddResult,
    PlaylistImportRequest,
    PlaylistImportResult,
    TrackAddFailure,
)
from yt_player.playlists.repository import PlaylistRepository
from yt_player.sources.youtube import materialize_track


class PlaylistService:
    def __init__(self, repository: PlaylistRepository) -> None:
        self.repository = repository

    def create_playlist(self, name: str, description: str | None = None) -> Playlist:
        return self.repository.create_playlist(name, description)

    def list_playlists(self):
        return self.repository.list_playlists()

    def get_playlist(self, playlist_ref: str) -> Playlist:
        return self.repository.get_playlist(playlist_ref)

    def update_playlist(
        self, playlist_ref: str, name: str | None = None, description: str | None = None
    ) -> Playlist:
        return self.repository.update_playlist(playlist_ref, name=name, description=description)

    def delete_playlist(self, playlist_ref: str) -> None:
        self.repository.delete_playlist(playlist_ref)

    def add_track(self, playlist_ref: str, track: Track) -> Playlist:
        playlist, _, skipped = self.repository.add_tracks(playlist_ref, [track])
        if skipped:
            raise ServiceError(
                "PLAYLIST_TRACK_EXISTS",
                f"'{track.title}' is already in playlist '{playlist.name}'.",
                409,
            )
        return playlist

    def add_tracks(
        self,
        playlist_ref: str,
        tracks: list[Track],
        failed: list[TrackAddFailure] | None = None,
    ) -> PlaylistAddResult:
        playlist, added, skipped = self.repository.add_tracks(playlist_ref, tracks)
        return PlaylistAddResult(
            playlist=playlist, added=added, skipped=skipped, failed=failed or []
        )

    def move_track(self, playlist_ref: str, track_ref: str, position: int) -> Playlist:
        return self.repository.move_track(playlist_ref, track_ref, position)

    def remove_track(self, playlist_ref: str, track_ref: str) -> Playlist:
        return self.repository.remove_track(playlist_ref, track_ref)

    def reorder_tracks(self, playlist_ref: str, track_ids: list[str]) -> Playlist:
        return self.repository.reorder_tracks(playlist_ref, track_ids)

    def clear_playlist(self, playlist_ref: str) -> Playlist:
        return self.repository.clear_playlist(playlist_ref)

    def export_playlist(self, playlist_ref: str) -> dict:
        return {"version": 1, "playlists": [self.get_playlist(playlist_ref)]}

    def export_all(self) -> dict:
        playlists = [self.get_playlist(summary.id) for summary in self.repository.list_playlists()]
        return {"version": 1, "playlists": playlists}

    def import_playlists(self, payload: PlaylistImportRequest) -> PlaylistImportResult:
        if payload.version != 1:
            raise ServiceError(
                "UNSUPPORTED_IMPORT_VERSION",
                "Only playlist export version 1 is supported.",
                400,
            )

        imported: list[Playlist] = []
        renamed: dict[str, str] = {}
        for raw_playlist in payload.playlists:
            name = raw_playlist.name
            tracks = [materialize_track(track) for track in raw_playlist.tracks]

            available_name = self.repository.available_playlist_name(name)
            if available_name != name:
                renamed[name] = available_name
            imported.append(
                self.repository.import_playlist(
                    available_name, raw_playlist.description, tracks
                )
            )

        return PlaylistImportResult(imported=imported, renamed=renamed)

    def start_play_history(self, track: Track, position_seconds: float = 0) -> str:
        return self.repository.start_play_history(track, position_seconds=position_seconds)

    def finish_play_history(
        self,
        history_id: str,
        *,
        status: PlaybackHistoryStatus,
        position_seconds: float,
        error: str | None = None,
    ) -> None:
        self.repository.finish_play_history(
            history_id,
            status=status,
            position_seconds=position_seconds,
            error=error,
        )

    def list_play_history(
        self,
        limit: int = 50,
        *,
        started_at_from: datetime | None = None,
        started_at_before: datetime | None = None,
    ) -> list[PlaybackHistoryEntry]:
        return self.repository.list_play_history(
            limit,
            started_at_from=started_at_from,
            started_at_before=started_at_before,
        )
