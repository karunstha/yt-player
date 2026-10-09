import asyncio
from datetime import datetime

from yt_player.core.config import (
    DEFAULT_VOLUME,
    PLAYBACK_COMPLETION_GRACE_SECONDS,
    PLAYBACK_RECOVERY_RETRIES,
)
from yt_player.core.errors import ServiceError
from yt_player.core.time import utc_now
from yt_player.player.backend import BackendExitStatus, FfplayBackend
from yt_player.player.models import PlaybackState, RepeatMode, Track


class PlaybackService:
    def __init__(self, backend: FfplayBackend | None = None, volume: float = DEFAULT_VOLUME) -> None:
        self.backend = backend or FfplayBackend()
        self._lock = asyncio.Lock()
        self._status = "idle"
        self._track: Track | None = None
        self._base_position_seconds = 0.0
        self._started_at: datetime | None = None
        self._updated_at = utc_now()
        self._volume = volume
        self._repeat_mode: RepeatMode = "off"
        self._shuffle = False
        self._error: str | None = None
        self._backend_recovery_attempts = 0

    async def play(
        self,
        track: Track,
        *,
        position_seconds: float = 0,
        bluetooth_device_id: str | None = None,
    ) -> PlaybackState:
        async with self._lock:
            self._status = "loading"
            self._track = track
            self._base_position_seconds = position_seconds
            self._started_at = None
            self._updated_at = utc_now()
            self._error = None
            self._backend_recovery_attempts = 0

        try:
            await asyncio.to_thread(
                self.backend.start_url,
                track.url,
                position_seconds=position_seconds,
                volume=self._volume,
                bluetooth_device_id=bluetooth_device_id,
            )
        except Exception as exc:
            async with self._lock:
                self._status = "error"
                self._error = str(exc)
                self._updated_at = utc_now()
                return self._snapshot_unlocked()

        async with self._lock:
            now = utc_now()
            self._status = "playing"
            self._track = track
            self._base_position_seconds = position_seconds
            self._started_at = now
            self._updated_at = now
            self._error = None
            self._backend_recovery_attempts = 0
            return self._snapshot_unlocked()

    async def pause(self) -> PlaybackState:
        async with self._lock:
            if self._status != "playing":
                return self._snapshot_unlocked()
            self._base_position_seconds = self._position_unlocked()
            self._started_at = None
            self._status = "paused"
            self._updated_at = utc_now()
        await asyncio.to_thread(self.backend.pause)
        return await self.snapshot()

    async def resume(self, bluetooth_device_id: str | None = None) -> PlaybackState:
        async with self._lock:
            if self._track is None:
                raise ServiceError("NO_CURRENT_TRACK", "No track is loaded.", 409)
            if self._status == "playing":
                return self._snapshot_unlocked()
            track = self._track
            position = self._base_position_seconds
            backend_running = self.backend.is_running()

        if backend_running:
            await asyncio.to_thread(self.backend.resume)
        else:
            await asyncio.to_thread(
                self.backend.start_url,
                track.url,
                position_seconds=position,
                volume=self._volume,
                bluetooth_device_id=bluetooth_device_id,
            )

        async with self._lock:
            self._status = "playing"
            self._started_at = utc_now()
            self._updated_at = utc_now()
            self._error = None
            if not backend_running:
                self._backend_recovery_attempts = 0
            return self._snapshot_unlocked()

    async def stop(self) -> PlaybackState:
        await asyncio.to_thread(self.backend.stop)
        async with self._lock:
            self._status = "stopped" if self._track else "idle"
            self._base_position_seconds = 0
            self._started_at = None
            self._updated_at = utc_now()
            self._error = None
            self._backend_recovery_attempts = 0
            return self._snapshot_unlocked()

    async def seek(self, position_seconds: float) -> PlaybackState:
        async with self._lock:
            if self._track is None:
                raise ServiceError("NO_CURRENT_TRACK", "No track is loaded.", 409)
            was_playing = self._status == "playing"
            track = self._track
            self._base_position_seconds = position_seconds
            self._started_at = utc_now() if was_playing else None
            self._updated_at = utc_now()
            self._error = None
            self._backend_recovery_attempts = 0

        if was_playing:
            await asyncio.to_thread(
                self.backend.start_url,
                track.url,
                position_seconds=position_seconds,
                volume=self._volume,
            )
        else:
            await asyncio.to_thread(self.backend.stop)

        return await self.snapshot()

    async def set_volume(self, volume: float) -> PlaybackState:
        async with self._lock:
            self._volume = volume
            should_update_backend = self._status == "playing" and self._track is not None
            position = self._position_unlocked()
            track = self._track
            self._base_position_seconds = position
            self._started_at = utc_now() if should_update_backend else self._started_at
            self._updated_at = utc_now()

        live_volume_changed = False
        if should_update_backend and hasattr(self.backend, "set_volume"):
            live_volume_changed = await asyncio.to_thread(self.backend.set_volume, volume)

        if should_update_backend and not live_volume_changed and track is not None:
            await asyncio.to_thread(
                self.backend.start_url,
                track.url,
                position_seconds=position,
                volume=volume,
            )

        return await self.snapshot()

    async def set_repeat_mode(self, repeat_mode: RepeatMode) -> PlaybackState:
        async with self._lock:
            self._repeat_mode = repeat_mode
            self._updated_at = utc_now()
            return self._snapshot_unlocked()

    async def set_shuffle(self, shuffle: bool) -> PlaybackState:
        async with self._lock:
            self._shuffle = shuffle
            self._updated_at = utc_now()
            return self._snapshot_unlocked()

    async def mark_completed(self) -> Track | None:
        async with self._lock:
            completed = self._track
            if completed is None:
                return None
            self._status = "stopped"
            self._base_position_seconds = completed.duration_seconds or self._position_unlocked()
            self._started_at = None
            self._updated_at = utc_now()
            self._backend_recovery_attempts = 0
            return completed

    async def backend_exit_status(self) -> BackendExitStatus | None:
        if not hasattr(self.backend, "exit_status"):
            return None
        return await asyncio.to_thread(self.backend.exit_status)

    async def should_complete_backend_exit(self, exit_status: BackendExitStatus) -> bool:
        if exit_status.clean:
            return True

        async with self._lock:
            if self._track is None or self._track.duration_seconds is None:
                return False
            remaining = self._track.duration_seconds - self._position_unlocked()
            return remaining <= PLAYBACK_COMPLETION_GRACE_SECONDS

    async def recover_backend_exit(self, exit_status: BackendExitStatus) -> PlaybackState:
        async with self._lock:
            if self._status != "playing" or self._track is None:
                return self._snapshot_unlocked()

            position = self._position_unlocked()
            track = self._track
            volume = self._volume
            message = exit_status.message or "Playback backend exited unexpectedly"

            if self._backend_recovery_attempts >= PLAYBACK_RECOVERY_RETRIES:
                self._status = "error"
                self._base_position_seconds = position
                self._started_at = None
                self._updated_at = utc_now()
                self._error = message
                should_restart = False
            else:
                self._backend_recovery_attempts += 1
                self._status = "loading"
                self._base_position_seconds = position
                self._started_at = None
                self._updated_at = utc_now()
                self._error = (
                    f"{message}; reconnecting "
                    f"({self._backend_recovery_attempts}/{PLAYBACK_RECOVERY_RETRIES})"
                )
                should_restart = True

        if not should_restart:
            await asyncio.to_thread(self.backend.stop)
            return await self.snapshot()

        try:
            await asyncio.to_thread(
                self.backend.start_url,
                track.url,
                position_seconds=position,
                volume=volume,
            )
        except Exception as exc:
            async with self._lock:
                self._status = "error"
                self._base_position_seconds = position
                self._started_at = None
                self._updated_at = utc_now()
                self._error = f"{message}; reconnect failed: {exc}"
                return self._snapshot_unlocked()

        async with self._lock:
            now = utc_now()
            self._status = "playing"
            self._base_position_seconds = position
            self._started_at = now
            self._updated_at = now
            self._error = None
            return self._snapshot_unlocked()

    async def snapshot(
        self, *, queue_index: int | None = None, queue_length: int = 0
    ) -> PlaybackState:
        async with self._lock:
            return self._snapshot_unlocked(queue_index=queue_index, queue_length=queue_length)

    async def current_track(self) -> Track | None:
        async with self._lock:
            return self._track

    async def status_name(self) -> str:
        async with self._lock:
            return self._status

    async def repeat_mode(self) -> RepeatMode:
        async with self._lock:
            return self._repeat_mode

    async def is_backend_running(self) -> bool:
        return await asyncio.to_thread(self.backend.is_running)

    def _snapshot_unlocked(
        self, *, queue_index: int | None = None, queue_length: int = 0
    ) -> PlaybackState:
        return PlaybackState(
            state=self._status,
            track=self._track,
            position_seconds=self._position_unlocked(),
            duration_seconds=self._track.duration_seconds if self._track else None,
            volume=self._volume,
            repeat_mode=self._repeat_mode,
            shuffle=self._shuffle,
            queue_index=queue_index,
            queue_length=queue_length,
            started_at=self._started_at,
            updated_at=self._updated_at,
            error=self._error,
        )

    def _position_unlocked(self) -> float:
        if self._status != "playing" or self._started_at is None:
            return self._base_position_seconds

        elapsed = (utc_now() - self._started_at).total_seconds()
        position = self._base_position_seconds + max(elapsed, 0)
        if self._track and self._track.duration_seconds is not None:
            return min(position, self._track.duration_seconds)
        return position
