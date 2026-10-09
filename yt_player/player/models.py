import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from yt_player.core.time import utc_now


# What a search looks for: YouTube Music songs, music videos, or podcast
# episodes, or any video on YouTube.
SearchType = Literal["songs", "music_videos", "podcasts", "videos"]


class TrackInput(BaseModel):
    id: Optional[str] = None
    source: str = "youtube"
    source_id: Optional[str] = None
    url: str
    title: Optional[str] = None
    artist: Optional[str] = None
    duration_seconds: Optional[float] = None
    thumbnail_url: Optional[str] = None
    requested_by: Optional[str] = None
    original_query: Optional[str] = None


class Track(BaseModel):
    id: str
    source: str = "youtube"
    source_id: Optional[str] = None
    url: str
    title: str
    artist: Optional[str] = None
    duration_seconds: Optional[float] = None
    thumbnail_url: Optional[str] = None
    requested_by: Optional[str] = None
    original_query: Optional[str] = None


PlaybackStatus = Literal["idle", "loading", "playing", "paused", "stopped", "error"]
RepeatMode = Literal["off", "one", "all"]
PlaybackHistoryStatus = Literal[
    "playing", "completed", "stopped", "skipped", "replaced", "error"
]


class PlaybackState(BaseModel):
    state: PlaybackStatus
    track: Optional[Track] = None
    position_seconds: float = 0
    duration_seconds: Optional[float] = None
    volume: float = Field(default=0.8, ge=0, le=1)
    repeat_mode: RepeatMode = "off"
    shuffle: bool = False
    queue_index: Optional[int] = None
    queue_length: int = 0
    started_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)
    error: Optional[str] = None


class PlaybackHistoryEntry(BaseModel):
    id: str
    track: Track
    requested_by: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: PlaybackHistoryStatus
    start_position_seconds: float = 0
    end_position_seconds: Optional[float] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


class PlaybackHistoryPage(BaseModel):
    items: list[PlaybackHistoryEntry] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    next_before: Optional[datetime] = None
    limit: int


class QueueItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    track: Track
    added_at: datetime = Field(default_factory=utc_now)
    requested_by: Optional[str] = None


class QueueState(BaseModel):
    items: list[QueueItem] = Field(default_factory=list)
    current_index: Optional[int] = None
    queue_length: int = 0


class PlayRequest(BaseModel):
    url: Optional[str] = None
    query: Optional[str] = None
    track: Optional[TrackInput] = None
    bluetooth_device_id: Optional[str] = None
    requested_by: Optional[str] = None
    search_type: Optional[SearchType] = Field(
        default=None, description="What a query searches for. Defaults to DEFAULT_SEARCH_TYPE."
    )

    @model_validator(mode="after")
    def require_one_play_target(self) -> "PlayRequest":
        values = [self.url is not None, self.query is not None, self.track is not None]
        if sum(values) != 1:
            raise ValueError("Provide exactly one of url, query, or track.")
        return self


class SeekRequest(BaseModel):
    position_seconds: float = Field(ge=0)


class VolumeRequest(BaseModel):
    volume: float = Field(ge=0, le=1)


class ShuffleRequest(BaseModel):
    shuffle: bool


class RepeatRequest(BaseModel):
    repeat_mode: RepeatMode


class AddTrackRequest(BaseModel):
    track: Optional[TrackInput] = None
    url: Optional[str] = None
    query: Optional[str] = None
    requested_by: Optional[str] = None
    search_type: Optional[SearchType] = Field(
        default=None, description="What a query searches for. Defaults to DEFAULT_SEARCH_TYPE."
    )

    @model_validator(mode="after")
    def require_one_track_source(self) -> "AddTrackRequest":
        values = [self.track is not None, self.url is not None, self.query is not None]
        if sum(values) != 1:
            raise ValueError("Provide exactly one of track, url, or query.")
        return self


class QueuePlaylistRequest(BaseModel):
    play_next: bool = False
    shuffle: bool = False


class QueueReplaceRequest(BaseModel):
    tracks: list[TrackInput] = Field(default_factory=list)
    playlist_id: Optional[str] = None
    url: Optional[str] = None
    query: Optional[str] = None
    shuffle: bool = False
    requested_by: Optional[str] = None
    search_type: Optional[SearchType] = Field(
        default=None, description="What a query searches for. Defaults to DEFAULT_SEARCH_TYPE."
    )

    @model_validator(mode="after")
    def require_queue_source(self) -> "QueueReplaceRequest":
        values = [
            bool(self.tracks),
            self.playlist_id is not None,
            self.url is not None,
            self.query is not None,
        ]
        if sum(values) != 1:
            raise ValueError("Provide exactly one of tracks, playlist_id, url, or query.")
        return self
