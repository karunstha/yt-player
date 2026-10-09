from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from yt_player.player.models import AddTrackRequest, Track, TrackInput


def blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


class Playlist(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    tracks: list[Track] = Field(default_factory=list)


class PlaylistSummary(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    track_count: int = 0


class CreatePlaylistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Playlist name cannot be blank.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: Optional[str]) -> Optional[str]:
        return blank_to_none(value)


class UpdatePlaylistRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def clean_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Playlist name cannot be blank.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: Optional[str]) -> Optional[str]:
        return blank_to_none(value)


class ReorderPlaylistRequest(BaseModel):
    track_ids: list[str] = Field(min_length=1)


class MoveTrackRequest(BaseModel):
    position: int = Field(ge=0, description="New 0-based position; past the end moves it last.")


class AddTracksRequest(BaseModel):
    items: list[AddTrackRequest] = Field(min_length=1, max_length=50)


class TrackAddFailure(BaseModel):
    input: str
    error: str


class PlaylistAddResult(BaseModel):
    playlist: Playlist
    added: list[Track] = Field(default_factory=list)
    skipped: list[Track] = Field(
        default_factory=list, description="Tracks that were already in the playlist."
    )
    failed: list[TrackAddFailure] = Field(default_factory=list)


class PlayPlaylistRequest(BaseModel):
    shuffle: bool = False
    start_index: int = Field(default=0, ge=0)
    bluetooth_device_id: Optional[str] = None


class PlaylistExport(BaseModel):
    version: int = 1
    playlists: list[Playlist]


class PlaylistImportEntry(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    tracks: list[TrackInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_import_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Playlist name cannot be blank.")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: Optional[str]) -> Optional[str]:
        return blank_to_none(value)


class PlaylistImportRequest(BaseModel):
    version: int = 1
    playlists: list[PlaylistImportEntry] = Field(default_factory=list)


class PlaylistImportResult(BaseModel):
    imported: list[Playlist]
    renamed: dict[str, str] = Field(default_factory=dict)
