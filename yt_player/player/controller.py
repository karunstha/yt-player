import asyncio
import random
from datetime import datetime, timedelta, timezone

from yt_player.core.errors import ServiceError
from yt_player.core.time import utc_now
from yt_player.events.service import EventService
from yt_player.player.models import (
    AddTrackRequest,
    PlaybackState,
    PlaybackHistoryPage,
    PlaybackHistoryStatus,
    PlayRequest,
    QueueReplaceRequest,
    QueueState,
    RepeatMode,
    SearchType,
    Track,
    TrackInput,
)
from yt_player.player.queue_service import QueueService
from yt_player.player.service import PlaybackService
from yt_player.playlists.models import TrackAddFailure
from yt_player.playlists.service import PlaylistService
from yt_player.sources.models import SearchResponse
from yt_player.sources.youtube import YouTubeService, materialize_track


class PlaybackController:
    def __init__(
        self,
        *,
        youtube_service: YouTubeService,
        playback_service: PlaybackService,
        queue_service: QueueService,
        playlist_service: PlaylistService,
        event_service: EventService,
    ) -> None:
        self.youtube_service = youtube_service
        self.playback = playback_service
        self.queue = queue_service
        self.playlists = playlist_service
        self.events = event_service
        self._lock = asyncio.Lock()
        self._active_history_id: str | None = None

    async def search(
        self,
        query: str,
        limit: int = 10,
        requested_by: str | None = None,
        search_type: SearchType | None = None,
    ) -> SearchResponse:
        tracks = await asyncio.to_thread(
            self.youtube_service.search,
            query,
            limit,
            requested_by=requested_by,
            search_type=search_type,
        )
        return SearchResponse(results=tracks)

    async def resolve_track(
        self,
        *,
        url: str | None = None,
        query: str | None = None,
        track: TrackInput | None = None,
        requested_by: str | None = None,
        search_type: SearchType | None = None,
    ) -> Track:
        return await asyncio.to_thread(
            self.youtube_service.resolve,
            url=url,
            query=query,
            track=track,
            requested_by=requested_by,
            search_type=search_type,
        )

    async def resolve_tracks(
        self, items: list[AddTrackRequest], *, concurrency: int = 4
    ) -> tuple[list[Track], list[TrackAddFailure]]:
        """Resolve several tracks, a few at a time, keeping their order.
        Items that fail are reported instead of failing the whole batch."""
        semaphore = asyncio.Semaphore(concurrency)

        async def resolve_one(item: AddTrackRequest) -> Track | TrackAddFailure:
            async with semaphore:
                try:
                    return await self.resolve_track(
                        url=item.url,
                        query=item.query,
                        track=item.track,
                        requested_by=item.requested_by,
                        search_type=item.search_type,
                    )
                except ServiceError as exc:
                    label = item.url or item.query or (item.track.url if item.track else "")
                    return TrackAddFailure(input=label, error=exc.message)

        results = await asyncio.gather(*(resolve_one(item) for item in items))
        tracks = [result for result in results if isinstance(result, Track)]
        failures = [result for result in results if isinstance(result, TrackAddFailure)]
        return tracks, failures

    async def queue_tracks(self) -> list[Track]:
        queue_state = await self.queue.snapshot()
        if not queue_state.items:
            raise ServiceError("QUEUE_EMPTY", "Queue is empty.", 409)
        return [item.track for item in queue_state.items]

    async def queue_playlist(
        self, playlist_ref: str, *, play_next: bool = False, shuffle: bool = False
    ) -> QueueState:
        playlist = self.playlists.get_playlist(playlist_ref)
        if not playlist.tracks:
            raise ServiceError("PLAYLIST_EMPTY", "Playlist has no tracks.", 409)
        tracks = list(playlist.tracks)
        if shuffle:
            random.shuffle(tracks)
        queue_state = await self.queue.extend(tracks, insert_next=play_next)
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        return queue_state

    async def play(self, request: PlayRequest) -> PlaybackState:
        async with self._lock:
            track = await self.resolve_track(
                url=request.url,
                query=request.query,
                track=request.track,
                requested_by=request.requested_by,
                search_type=request.search_type,
            )
            queue_state = await self.queue.replace([track])
            await self._finish_active_history("replaced")
            await self.playback.play(
                track, bluetooth_device_id=request.bluetooth_device_id
            )
            state = await self._state_from(queue_state)
            self._start_history(track, state)

        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        await self.events.broadcast("track.changed", {"track": track.model_dump(mode="json")})
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def play_track(
        self, track: Track, *, bluetooth_device_id: str | None = None
    ) -> PlaybackState:
        request = PlayRequest(
            track=TrackInput(**track.model_dump()),
            bluetooth_device_id=bluetooth_device_id,
        )
        return await self.play(request)

    async def pause(self) -> PlaybackState:
        await self.playback.pause()
        state = await self.get_state()
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def resume(self) -> PlaybackState:
        await self.playback.resume()
        state = await self.get_state()
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def stop(self) -> PlaybackState:
        prior_state = await self.get_state()
        await self.playback.stop()
        await self._finish_active_history("stopped", prior_state)
        state = await self.get_state()
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def seek(self, position_seconds: float) -> PlaybackState:
        await self.playback.seek(position_seconds)
        state = await self.get_state()
        await self.events.broadcast("playback.seeked", state.model_dump(mode="json"))
        return state

    async def set_volume(self, volume: float) -> PlaybackState:
        await self.playback.set_volume(volume)
        state = await self.get_state()
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def set_repeat_mode(self, repeat_mode: RepeatMode) -> PlaybackState:
        await self.playback.set_repeat_mode(repeat_mode)
        state = await self.get_state()
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def set_shuffle(self, shuffle: bool) -> PlaybackState:
        await self.queue.set_shuffle(shuffle)
        await self.playback.set_shuffle(shuffle)
        queue_state = await self.queue.snapshot()
        state = await self._state_from(queue_state)
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def next(self, *, manual: bool = True) -> PlaybackState:
        async with self._lock:
            repeat_mode = await self.playback.repeat_mode()
            item = await self.queue.next(repeat_mode, manual=manual)
            if item is None:
                if manual:
                    prior_state = await self.playback.snapshot()
                    await self.playback.stop()
                    await self._finish_active_history("stopped", prior_state)
                queue_state = await self.queue.snapshot()
                state = await self._state_from(queue_state)
            else:
                if manual:
                    await self._finish_active_history("skipped")
                await self.playback.play(item.track)
                queue_state = await self.queue.snapshot()
                state = await self._state_from(queue_state)
                self._start_history(item.track, state)

        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        if item is not None:
            await self.events.broadcast(
                "track.changed", {"track": item.track.model_dump(mode="json")}
            )
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def previous(self) -> PlaybackState:
        async with self._lock:
            item = await self.queue.previous()
            if item is None:
                raise ServiceError("QUEUE_EMPTY", "Queue is empty.", 409)
            await self._finish_active_history("skipped")
            await self.playback.play(item.track)
            queue_state = await self.queue.snapshot()
            state = await self._state_from(queue_state)
            self._start_history(item.track, state)

        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        await self.events.broadcast("track.changed", {"track": item.track.model_dump(mode="json")})
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def get_state(self) -> PlaybackState:
        queue_state = await self.queue.snapshot()
        return await self._state_from(queue_state)

    async def current_track(self) -> Track:
        track = await self.playback.current_track()
        if track is None:
            raise ServiceError("NO_CURRENT_TRACK", "No track is currently loaded.", 404)
        return track

    def history(
        self,
        *,
        limit: int = 100,
        days: int = 7,
        before: datetime | None = None,
    ) -> PlaybackHistoryPage:
        safe_limit = max(1, min(limit, 500))
        safe_days = max(1, min(days, 31))
        window_end = self._coerce_utc(before or utc_now())
        window_start = window_end - timedelta(days=safe_days)

        entries = self.playlists.list_play_history(
            safe_limit + 1,
            started_at_from=window_start,
            started_at_before=window_end,
        )
        items = entries[:safe_limit]
        next_before = items[-1].started_at if len(entries) > safe_limit and items else None

        return PlaybackHistoryPage(
            items=items,
            window_start=window_start,
            window_end=window_end,
            next_before=next_before,
            limit=safe_limit,
        )

    async def replace_queue(self, request: QueueReplaceRequest) -> QueueState:
        if request.playlist_id:
            playlist = self.playlists.get_playlist(request.playlist_id)
            tracks = playlist.tracks
        elif request.tracks:
            tracks = [
                materialize_track(track, requested_by=request.requested_by)
                for track in request.tracks
            ]
        else:
            tracks = [
                await self.resolve_track(
                    url=request.url,
                    query=request.query,
                    requested_by=request.requested_by,
                    search_type=request.search_type,
                )
            ]

        queue_state = await self.queue.replace(tracks, shuffle=request.shuffle)
        await self.playback.set_shuffle(request.shuffle)
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        return queue_state

    async def append_queue_track(
        self, request: AddTrackRequest, *, insert_next: bool = False
    ) -> QueueState:
        track = await self.resolve_track(
            url=request.url,
            query=request.query,
            track=request.track,
            requested_by=request.requested_by,
            search_type=request.search_type,
        )
        queue_state = (
            await self.queue.insert_next(track)
            if insert_next
            else await self.queue.append(track)
        )
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        return queue_state

    async def remove_queue_item(self, item_ref: str) -> QueueState:
        queue_state, removed_current = await self.queue.remove(item_ref)
        if removed_current:
            current = await self.queue.current()
            if current is None:
                prior_state = await self.playback.snapshot()
                await self.playback.stop()
                await self._finish_active_history("stopped", prior_state)
            else:
                await self._finish_active_history("skipped")
                await self.playback.play(current.track)
        queue_state = await self.queue.snapshot()
        state = await self._state_from(queue_state)
        if removed_current and current is not None:
            self._start_history(current.track, state)
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return queue_state

    async def clear_queue(self) -> QueueState:
        queue_state = await self.queue.clear()
        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        return queue_state

    async def play_playlist(
        self,
        playlist_ref: str,
        *,
        shuffle: bool = False,
        start_index: int = 0,
        bluetooth_device_id: str | None = None,
    ) -> PlaybackState:
        playlist = self.playlists.get_playlist(playlist_ref)
        if not playlist.tracks:
            raise ServiceError("PLAYLIST_EMPTY", "Playlist has no tracks.", 409)

        async with self._lock:
            queue_state = await self.queue.replace(
                playlist.tracks, start_index=start_index, shuffle=shuffle
            )
            await self.playback.set_shuffle(shuffle)
            current = await self.queue.current()
            if current is None:
                raise ServiceError("QUEUE_EMPTY", "Queue is empty.", 409)
            await self._finish_active_history("replaced")
            await self.playback.play(current.track, bluetooth_device_id=bluetooth_device_id)
            state = await self._state_from(queue_state)
            self._start_history(current.track, state)

        await self.events.broadcast("queue.changed", queue_state.model_dump(mode="json"))
        await self.events.broadcast(
            "track.changed", {"track": current.track.model_dump(mode="json")}
        )
        await self.events.broadcast("playback.state_changed", state.model_dump(mode="json"))
        return state

    async def reconcile_playback(self) -> None:
        if await self.playback.status_name() != "playing":
            return

        exit_status = await self.playback.backend_exit_status()
        if exit_status is None:
            return

        if not await self.playback.should_complete_backend_exit(exit_status):
            state = await self.playback.recover_backend_exit(exit_status)
            if state.state == "error":
                await self._finish_active_history("error", state, error=state.error)
            await self.events.broadcast(
                "playback.state_changed", state.model_dump(mode="json")
            )
            return

        completed = await self.playback.mark_completed()
        if completed is not None:
            state = await self.playback.snapshot()
            await self._finish_active_history("completed", state)
            await self.events.broadcast(
                "track.completed", {"track": completed.model_dump(mode="json")}
            )
        await self.next(manual=False)

    async def shutdown(self) -> None:
        prior_state = await self.get_state()
        await self.playback.stop()
        await self._finish_active_history("stopped", prior_state)

    async def _state_from(self, queue_state: QueueState) -> PlaybackState:
        return await self.playback.snapshot(
            queue_index=queue_state.current_index,
            queue_length=queue_state.queue_length,
        )

    def _start_history(self, track: Track, state: PlaybackState) -> None:
        if state.state != "playing":
            return
        self._active_history_id = self.playlists.start_play_history(
            track,
            position_seconds=state.position_seconds,
        )

    async def _finish_active_history(
        self,
        status: PlaybackHistoryStatus,
        state: PlaybackState | None = None,
        *,
        error: str | None = None,
    ) -> None:
        if self._active_history_id is None:
            return

        state = state or await self.playback.snapshot()
        self.playlists.finish_play_history(
            self._active_history_id,
            status=status,
            position_seconds=state.position_seconds,
            error=error or state.error,
        )
        self._active_history_id = None

    def _coerce_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
