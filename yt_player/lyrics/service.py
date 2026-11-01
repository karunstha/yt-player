import asyncio
import logging
from typing import Protocol

from yt_player.core.errors import ServiceError
from yt_player.events.service import EventService
from yt_player.lyrics.matching import LyricsQuery, build_query, parse_lrc, parse_plain
from yt_player.lyrics.models import Lyrics, LyricsMatch
from yt_player.lyrics.repository import LyricsRepository
from yt_player.player.models import QueueState, Track

logger = logging.getLogger(__name__)


class LyricsProvider(Protocol):
    name: str

    async def find(self, query: LyricsQuery) -> dict | None: ...


class LyricsService:
    """Finds lyrics for tracks, caches them, and fetches upcoming tracks early.

    It listens to playback events, so lyrics for the current and next few
    queued tracks are usually ready before a client asks. Each fresh lookup is
    announced with a `lyrics.ready` event.
    """

    def __init__(
        self,
        *,
        repository: LyricsRepository,
        provider: LyricsProvider,
        events: EventService,
        prefetch_ahead: int = 2,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.events = events
        self.prefetch_ahead = prefetch_ahead
        self._tracks: dict[str, Track] = {}
        self._in_flight: dict[str, asyncio.Task[Lyrics]] = {}
        self._background: set[asyncio.Task] = set()
        events.add_listener(self._on_event)

    async def get(self, track: Track) -> Lyrics:
        self._tracks[track.id] = track
        cached = await asyncio.to_thread(self.repository.get, track.id)
        if cached is not None:
            return cached
        task = self._in_flight.get(track.id)
        if task is None:
            task = asyncio.create_task(self._fetch(track))
            self._in_flight[track.id] = task
            task.add_done_callback(lambda _: self._in_flight.pop(track.id, None))
        # Shield so a client giving up does not cancel a lookup others share.
        return await asyncio.shield(task)

    async def get_by_id(self, track_id: str) -> Lyrics:
        cached = await asyncio.to_thread(self.repository.get, track_id)
        if cached is not None:
            return cached
        track = self._tracks.get(track_id)
        if track is None:
            raise ServiceError(
                "TRACK_NOT_FOUND",
                "Track is not playing or queued, so its lyrics cannot be looked up.",
                404,
            )
        return await self.get(track)

    async def set_offset(self, track_id: str, offset_seconds: float) -> Lyrics:
        lyrics = await self.get_by_id(track_id)
        await asyncio.to_thread(self.repository.set_offset, track_id, offset_seconds)
        lyrics.offset_seconds = offset_seconds
        await self.events.broadcast("lyrics.offset_changed", {
            "track_id": track_id,
            "offset_seconds": offset_seconds,
        })
        return lyrics

    async def shutdown(self) -> None:
        tasks = [*self._background, *self._in_flight.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch(self, track: Track) -> Lyrics:
        query = build_query(track.title, track.artist, track.duration_seconds)
        try:
            record = await self.provider.find(query)
        except ServiceError:
            raise
        except Exception as exc:
            # Not cached: a network blip should not hide lyrics for a week.
            logger.warning("Lyrics lookup failed for %s: %s", track.id, exc)
            raise ServiceError(
                "LYRICS_UNAVAILABLE", "The lyrics service could not be reached.", 502
            ) from exc

        lyrics = self._to_lyrics(track.id, record)
        lyrics = await asyncio.to_thread(self.repository.save, lyrics)
        await self.events.broadcast("lyrics.ready", lyrics.model_dump(mode="json"))
        return lyrics

    def _to_lyrics(self, track_id: str, record: dict | None) -> Lyrics:
        if record is None:
            return Lyrics(track_id=track_id, status="not_found")
        match = LyricsMatch(
            title=str(record.get("trackName") or ""),
            artist=record.get("artistName"),
            album=record.get("albumName"),
            duration_seconds=record.get("duration"),
        )
        common = {"track_id": track_id, "status": "found", "source": self.provider.name, "match": match}
        if record.get("instrumental"):
            return Lyrics(**common, instrumental=True)
        synced = parse_lrc(record.get("syncedLyrics") or "")
        if synced:
            return Lyrics(**common, synced=True, lines=synced)
        return Lyrics(**common, lines=parse_plain(record.get("plainLyrics") or ""))

    def _on_event(self, event_type: str, data: dict) -> None:
        if event_type == "track.changed":
            track = Track.model_validate(data["track"])
            self._prefetch([track])
        elif event_type == "queue.changed":
            queue = QueueState.model_validate(data)
            for item in queue.items:
                self._tracks[item.track.id] = item.track
            start = queue.current_index or 0
            upcoming = queue.items[start : start + 1 + self.prefetch_ahead]
            self._prefetch([item.track for item in upcoming])

    def _prefetch(self, tracks: list[Track]) -> None:
        for track in tracks:
            if track.id in self._in_flight:
                continue
            task = asyncio.create_task(self._prefetch_one(track))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _prefetch_one(self, track: Track) -> None:
        try:
            await self.get(track)
        except ServiceError:
            pass  # Already logged; a client request will retry.
