from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from yt_player.core.time import utc_now

LyricsStatus = Literal["found", "not_found"]


class LyricLine(BaseModel):
    # Seconds into the track; None for unsynced (plain) lyrics.
    time_seconds: Optional[float] = None
    text: str


class LyricsMatch(BaseModel):
    """What the provider matched, so clients can show or debug a wrong match."""

    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_seconds: Optional[float] = None


class Lyrics(BaseModel):
    track_id: str
    status: LyricsStatus
    synced: bool = False
    instrumental: bool = False
    lines: list[LyricLine] = Field(default_factory=list)
    source: Optional[str] = None
    match: Optional[LyricsMatch] = None
    # Per-track correction applied by clients: positive shows lines later.
    offset_seconds: float = 0
    fetched_at: datetime = Field(default_factory=utc_now)


class LyricsOffsetRequest(BaseModel):
    offset_seconds: float = Field(ge=-30, le=30)
