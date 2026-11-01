import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from yt_player.core.time import utc_now
from yt_player.lyrics.models import Lyrics

# Lyrics databases grow, so a miss is retried after a while instead of forever.
NOT_FOUND_RETRY_AFTER = timedelta(days=7)


class LyricsRepository:
    """Caches lookups (including misses) and per-track offsets in SQLite."""

    def __init__(self, database_path: str) -> None:
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lyrics (
                    track_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    offset_seconds REAL NOT NULL DEFAULT 0,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, track_id: str) -> Lyrics | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, status, offset_seconds, fetched_at FROM lyrics WHERE track_id = ?",
                (track_id,),
            ).fetchone()
        if row is None:
            return None
        if row["status"] == "not_found":
            fetched_at = datetime.fromisoformat(row["fetched_at"])
            if utc_now() - fetched_at > NOT_FOUND_RETRY_AFTER:
                return None
        lyrics = Lyrics.model_validate(json.loads(row["payload"]))
        lyrics.offset_seconds = row["offset_seconds"]
        return lyrics

    def save(self, lyrics: Lyrics) -> Lyrics:
        with self._lock:
            # Keep any offset the user already set for this track.
            existing = self._conn.execute(
                "SELECT offset_seconds FROM lyrics WHERE track_id = ?", (lyrics.track_id,)
            ).fetchone()
            if existing is not None:
                lyrics.offset_seconds = existing["offset_seconds"]
            self._conn.execute(
                """
                INSERT INTO lyrics (track_id, payload, status, offset_seconds, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    payload = excluded.payload,
                    status = excluded.status,
                    fetched_at = excluded.fetched_at
                """,
                (
                    lyrics.track_id,
                    lyrics.model_dump_json(exclude={"offset_seconds"}),
                    lyrics.status,
                    lyrics.offset_seconds,
                    lyrics.fetched_at.isoformat(),
                ),
            )
            self._conn.commit()
        return lyrics

    def set_offset(self, track_id: str, offset_seconds: float) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE lyrics SET offset_seconds = ? WHERE track_id = ?",
                (offset_seconds, track_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0
