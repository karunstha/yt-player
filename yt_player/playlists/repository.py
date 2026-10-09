import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from yt_player.core.errors import ServiceError
from yt_player.core.time import utc_now
from yt_player.player.models import PlaybackHistoryEntry, PlaybackHistoryStatus, Track
from yt_player.playlists.models import Playlist, PlaylistSummary


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "playlist"


class PlaylistRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS playlists_name_unique
                    ON playlists(lower(name));

                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT,
                    duration_seconds REAL,
                    thumbnail_url TEXT,
                    requested_by TEXT,
                    original_query TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS playlist_items (
                    id TEXT PRIMARY KEY,
                    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(playlist_id, track_id)
                );

                CREATE TABLE IF NOT EXISTS play_history (
                    id TEXT PRIMARY KEY,
                    track_id TEXT NOT NULL REFERENCES tracks(id),
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    start_position_seconds REAL NOT NULL DEFAULT 0,
                    end_position_seconds REAL,
                    duration_seconds REAL,
                    requested_by TEXT,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS play_history_started_at_idx
                    ON play_history(started_at DESC);

                CREATE INDEX IF NOT EXISTS play_history_track_id_idx
                    ON play_history(track_id);
                """
            )
            self._ensure_play_history_requested_by_column()
            self._conn.commit()

    def create_playlist(self, name: str, description: str | None = None) -> Playlist:
        now = utc_now().isoformat()
        playlist_id = str(uuid.uuid4())
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO playlists(id, name, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (playlist_id, name, description, now, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ServiceError(
                    "PLAYLIST_NAME_EXISTS", "A playlist with that name already exists.", 409
                ) from exc
            return self.get_playlist(playlist_id)

    def list_playlists(self) -> list[PlaylistSummary]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.*, COUNT(pi.id) AS track_count
                FROM playlists p
                LEFT JOIN playlist_items pi ON pi.playlist_id = p.id
                GROUP BY p.id
                ORDER BY lower(p.name)
                """
            ).fetchall()
            return [
                PlaylistSummary(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    track_count=row["track_count"],
                )
                for row in rows
            ]

    def get_playlist(self, playlist_ref: str) -> Playlist:
        with self._lock:
            playlist_row = self._find_playlist_row(playlist_ref)
            if playlist_row is None:
                raise ServiceError("PLAYLIST_NOT_FOUND", "Playlist does not exist.", 404)

            track_rows = self._conn.execute(
                """
                SELECT t.*
                FROM playlist_items pi
                JOIN tracks t ON t.id = pi.track_id
                WHERE pi.playlist_id = ?
                ORDER BY pi.position ASC
                """,
                (playlist_row["id"],),
            ).fetchall()
            return self._playlist_from_rows(playlist_row, track_rows)

    def update_playlist(
        self, playlist_ref: str, name: str | None = None, description: str | None = None
    ) -> Playlist:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            next_name = name if name is not None else playlist.name
            now = utc_now().isoformat()
            try:
                self._conn.execute(
                    """
                    UPDATE playlists
                    SET name = ?, description = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_name, description, now, playlist.id),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ServiceError(
                    "PLAYLIST_NAME_EXISTS", "A playlist with that name already exists.", 409
                ) from exc
            return self.get_playlist(playlist.id)

    def delete_playlist(self, playlist_ref: str) -> None:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist.id,))
            self._conn.commit()

    def add_tracks(
        self, playlist_ref: str, tracks: list[Track]
    ) -> tuple[Playlist, list[Track], list[Track]]:
        """Append tracks in order. Returns the playlist, the added tracks, and the
        tracks skipped because they were already in it (or repeated in `tracks`)."""
        added: list[Track] = []
        skipped: list[Track] = []
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            present = {track.id for track in playlist.tracks}
            next_position = self._next_position(playlist.id)
            for track in tracks:
                if track.id in present:
                    skipped.append(track)
                    continue
                self._save_track(track)
                self._conn.execute(
                    """
                    INSERT INTO playlist_items(id, playlist_id, track_id, position, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), playlist.id, track.id, next_position, utc_now().isoformat()),
                )
                present.add(track.id)
                added.append(track)
                next_position += 1
            if added:
                self._touch_playlist(playlist.id)
            self._conn.commit()
            return self.get_playlist(playlist.id), added, skipped

    def move_track(self, playlist_ref: str, track_ref: str, position: int) -> Playlist:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            rows = self._conn.execute(
                "SELECT id, track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC",
                (playlist.id,),
            ).fetchall()
            index = next(
                (i for i, row in enumerate(rows) if track_ref in (row["track_id"], row["id"])),
                None,
            )
            if index is None:
                raise ServiceError(
                    "PLAYLIST_TRACK_NOT_FOUND", "Track does not exist in playlist.", 404
                )

            item_ids = [row["id"] for row in rows]
            moved = item_ids.pop(index)
            item_ids.insert(min(position, len(item_ids)), moved)
            for new_position, item_id in enumerate(item_ids):
                self._conn.execute(
                    "UPDATE playlist_items SET position = ? WHERE id = ?", (new_position, item_id)
                )
            self._touch_playlist(playlist.id)
            self._conn.commit()
            return self.get_playlist(playlist.id)

    def remove_track(self, playlist_ref: str, track_ref: str) -> Playlist:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            cursor = self._conn.execute(
                """
                DELETE FROM playlist_items
                WHERE playlist_id = ? AND (track_id = ? OR id = ?)
                """,
                (playlist.id, track_ref, track_ref),
            )
            if cursor.rowcount == 0:
                raise ServiceError(
                    "PLAYLIST_TRACK_NOT_FOUND", "Track does not exist in playlist.", 404
                )
            self._touch_playlist(playlist.id)
            self._conn.commit()
            self._normalize_positions(playlist.id)
            return self.get_playlist(playlist.id)

    def reorder_tracks(self, playlist_ref: str, track_ids: list[str]) -> Playlist:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            existing_ids = [track.id for track in playlist.tracks]
            if set(existing_ids) != set(track_ids) or len(existing_ids) != len(track_ids):
                raise ServiceError(
                    "INVALID_PLAYLIST_ORDER",
                    "Reorder request must include every playlist track exactly once.",
                    400,
                )

            for position, track_id in enumerate(track_ids):
                self._conn.execute(
                    """
                    UPDATE playlist_items
                    SET position = ?
                    WHERE playlist_id = ? AND track_id = ?
                    """,
                    (position, playlist.id, track_id),
                )
            self._touch_playlist(playlist.id)
            self._conn.commit()
            return self.get_playlist(playlist.id)

    def clear_playlist(self, playlist_ref: str) -> Playlist:
        with self._lock:
            playlist = self.get_playlist(playlist_ref)
            self._conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist.id,))
            self._touch_playlist(playlist.id)
            self._conn.commit()
            return self.get_playlist(playlist.id)

    def import_playlist(self, name: str, description: str | None, tracks: list[Track]) -> Playlist:
        with self._lock:
            playlist = self.create_playlist(name, description)
            playlist, _, _ = self.add_tracks(playlist.id, tracks)
            return playlist

    def available_playlist_name(self, desired_name: str) -> str:
        with self._lock:
            rows = self._conn.execute("SELECT name FROM playlists").fetchall()
            existing = {row["name"].lower() for row in rows}
            if desired_name.lower() not in existing:
                return desired_name

            base = f"{desired_name} (imported)"
            candidate = base
            index = 2
            while candidate.lower() in existing:
                candidate = f"{base} {index}"
                index += 1
            return candidate

    def start_play_history(self, track: Track, position_seconds: float = 0) -> str:
        now = utc_now().isoformat()
        history_id = str(uuid.uuid4())
        with self._lock:
            self._save_track(track)
            self._conn.execute(
                """
                INSERT INTO play_history(
                    id, track_id, started_at, status, start_position_seconds,
                    duration_seconds, requested_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history_id,
                    track.id,
                    now,
                    "playing",
                    position_seconds,
                    track.duration_seconds,
                    track.requested_by,
                ),
            )
            self._conn.commit()
            return history_id

    def finish_play_history(
        self,
        history_id: str,
        *,
        status: PlaybackHistoryStatus,
        position_seconds: float,
        error: str | None = None,
    ) -> None:
        ended_at = utc_now().isoformat()
        with self._lock:
            self._conn.execute(
                """
                UPDATE play_history
                SET ended_at = ?,
                    status = ?,
                    end_position_seconds = ?,
                    error = ?
                WHERE id = ? AND ended_at IS NULL
                """,
                (ended_at, status, position_seconds, error, history_id),
            )
            self._conn.commit()

    def list_play_history(
        self,
        limit: int = 50,
        *,
        started_at_from: datetime | None = None,
        started_at_before: datetime | None = None,
    ) -> list[PlaybackHistoryEntry]:
        safe_limit = max(1, min(limit, 500))
        clauses = []
        params: list[str | int] = []
        if started_at_from is not None:
            clauses.append("h.started_at >= ?")
            params.append(started_at_from.isoformat())
        if started_at_before is not None:
            clauses.append("h.started_at < ?")
            params.append(started_at_before.isoformat())
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(safe_limit)

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    h.id AS history_id,
                    h.started_at,
                    h.ended_at,
                    h.status,
                    h.start_position_seconds,
                    h.end_position_seconds,
                    h.duration_seconds AS history_duration_seconds,
                    COALESCE(h.requested_by, t.requested_by) AS history_requested_by,
                    h.error,
                    t.*
                FROM play_history h
                JOIN tracks t ON t.id = h.track_id
                {where_clause}
                ORDER BY h.started_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._history_from_row(row) for row in rows]

    def _find_playlist_row(self, playlist_ref: str) -> sqlite3.Row | None:
        row = self._conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_ref,)).fetchone()
        if row is not None:
            return row

        row = self._conn.execute(
            "SELECT * FROM playlists WHERE lower(name) = lower(?)", (playlist_ref,)
        ).fetchone()
        if row is not None:
            return row

        for candidate in self._conn.execute("SELECT * FROM playlists").fetchall():
            if slugify(candidate["name"]) == playlist_ref:
                return candidate
        return None

    def _ensure_play_history_requested_by_column(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(play_history)").fetchall()
        }
        if "requested_by" not in columns:
            self._conn.execute("ALTER TABLE play_history ADD COLUMN requested_by TEXT")

    def _save_track(self, track: Track) -> None:
        now = utc_now().isoformat()
        self._conn.execute(
            """
            INSERT INTO tracks(
                id, source, source_id, url, title, artist, duration_seconds,
                thumbnail_url, requested_by, original_query, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                source_id = excluded.source_id,
                url = excluded.url,
                title = excluded.title,
                artist = excluded.artist,
                duration_seconds = excluded.duration_seconds,
                thumbnail_url = excluded.thumbnail_url,
                requested_by = COALESCE(excluded.requested_by, tracks.requested_by),
                original_query = COALESCE(excluded.original_query, tracks.original_query),
                updated_at = excluded.updated_at
            """,
            (
                track.id,
                track.source,
                track.source_id,
                track.url,
                track.title,
                track.artist,
                track.duration_seconds,
                track.thumbnail_url,
                track.requested_by,
                track.original_query,
                now,
                now,
            ),
        )

    def _next_position(self, playlist_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) AS next_position FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        return int(row["next_position"])

    def _normalize_positions(self, playlist_id: str) -> None:
        rows = self._conn.execute(
            "SELECT id FROM playlist_items WHERE playlist_id = ? ORDER BY position ASC, created_at ASC",
            (playlist_id,),
        ).fetchall()
        for position, row in enumerate(rows):
            self._conn.execute(
                "UPDATE playlist_items SET position = ? WHERE id = ?", (position, row["id"])
            )
        self._conn.commit()

    def _touch_playlist(self, playlist_id: str) -> None:
        self._conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?",
            (utc_now().isoformat(), playlist_id),
        )

    def _playlist_from_rows(self, playlist_row: sqlite3.Row, track_rows: list[sqlite3.Row]) -> Playlist:
        return Playlist(
            id=playlist_row["id"],
            name=playlist_row["name"],
            description=playlist_row["description"],
            created_at=playlist_row["created_at"],
            updated_at=playlist_row["updated_at"],
            tracks=[self._track_from_row(row) for row in track_rows],
        )

    def _track_from_row(self, row: sqlite3.Row) -> Track:
        return Track(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"],
            url=row["url"],
            title=row["title"],
            artist=row["artist"],
            duration_seconds=row["duration_seconds"],
            thumbnail_url=row["thumbnail_url"],
            requested_by=row["requested_by"],
            original_query=row["original_query"],
        )

    def _history_from_row(self, row: sqlite3.Row) -> PlaybackHistoryEntry:
        return PlaybackHistoryEntry(
            id=row["history_id"],
            track=self._track_from_row(row),
            requested_by=row["history_requested_by"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            start_position_seconds=row["start_position_seconds"],
            end_position_seconds=row["end_position_seconds"],
            duration_seconds=row["history_duration_seconds"],
            error=row["error"],
        )
