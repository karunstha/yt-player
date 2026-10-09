import asyncio
import random

from yt_player.core.errors import ServiceError
from yt_player.player.models import QueueItem, QueueState, RepeatMode, Track


class QueueService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._items: list[QueueItem] = []
        self._current_index: int | None = None

    async def snapshot(self) -> QueueState:
        async with self._lock:
            return self._snapshot_unlocked()

    async def replace(
        self, tracks: list[Track], *, start_index: int = 0, shuffle: bool = False
    ) -> QueueState:
        async with self._lock:
            items = [QueueItem(track=track, requested_by=track.requested_by) for track in tracks]
            if shuffle:
                random.shuffle(items)
            self._items = items
            self._current_index = None
            if self._items:
                self._current_index = min(start_index, len(self._items) - 1)
            return self._snapshot_unlocked()

    async def append(self, track: Track) -> QueueState:
        return await self.extend([track])

    async def insert_next(self, track: Track) -> QueueState:
        return await self.extend([track], insert_next=True)

    async def extend(self, tracks: list[Track], *, insert_next: bool = False) -> QueueState:
        """Add tracks in order at the end, or right after the current item."""
        async with self._lock:
            items = [QueueItem(track=track, requested_by=track.requested_by) for track in tracks]
            if not insert_next:
                position = len(self._items)
            elif self._current_index is None:
                position = 0
            else:
                position = self._current_index + 1
            self._items[position:position] = items
            if self._current_index is None and self._items:
                self._current_index = 0
            return self._snapshot_unlocked()

    async def remove(self, item_ref: str) -> tuple[QueueState, bool]:
        async with self._lock:
            index = next(
                (
                    idx
                    for idx, item in enumerate(self._items)
                    if item.id == item_ref or item.track.id == item_ref
                ),
                None,
            )
            if index is None:
                raise ServiceError("QUEUE_ITEM_NOT_FOUND", "Queue item does not exist.", 404)

            removed_current = self._current_index == index
            self._items.pop(index)
            if not self._items:
                self._current_index = None
            elif self._current_index is not None and index < self._current_index:
                self._current_index -= 1
            elif self._current_index is not None and self._current_index >= len(self._items):
                self._current_index = len(self._items) - 1
            return self._snapshot_unlocked(), removed_current

    async def clear(self) -> QueueState:
        async with self._lock:
            self._items = []
            self._current_index = None
            return self._snapshot_unlocked()

    async def current(self) -> QueueItem | None:
        async with self._lock:
            return self._current_unlocked()

    async def next(self, repeat_mode: RepeatMode = "off", *, manual: bool = True) -> QueueItem | None:
        async with self._lock:
            if not self._items:
                self._current_index = None
                return None
            if self._current_index is None:
                self._current_index = 0
                return self._current_unlocked()
            if not manual and repeat_mode == "one":
                return self._current_unlocked()
            if self._current_index + 1 < len(self._items):
                self._current_index += 1
                return self._current_unlocked()
            if repeat_mode == "all":
                self._current_index = 0
                return self._current_unlocked()
            return None

    async def previous(self) -> QueueItem | None:
        async with self._lock:
            if not self._items:
                self._current_index = None
                return None
            if self._current_index is None:
                self._current_index = 0
            elif self._current_index > 0:
                self._current_index -= 1
            return self._current_unlocked()

    async def set_shuffle(self, shuffle: bool) -> QueueState:
        async with self._lock:
            if shuffle and len(self._items) > 1:
                current = self._current_unlocked()
                remaining = [
                    item for idx, item in enumerate(self._items) if idx != self._current_index
                ]
                random.shuffle(remaining)
                self._items = ([current] if current else []) + remaining
                self._current_index = 0 if self._items else None
            return self._snapshot_unlocked()

    def _current_unlocked(self) -> QueueItem | None:
        if self._current_index is None:
            return None
        if self._current_index < 0 or self._current_index >= len(self._items):
            return None
        return self._items[self._current_index]

    def _snapshot_unlocked(self) -> QueueState:
        return QueueState(
            items=list(self._items),
            current_index=self._current_index,
            queue_length=len(self._items),
        )
