import asyncio
import sqlite3

from fastapi.testclient import TestClient

from yt_player.core.errors import ServiceError
from yt_player.events.service import EventService
from yt_player.factory import create_app
from yt_player.player.backend import BackendExitStatus
from yt_player.player.controller import PlaybackController
from yt_player.player.models import AddTrackRequest, PlayRequest, Track
from yt_player.player.queue_service import QueueService
from yt_player.player.service import PlaybackService
from yt_player.playlists.repository import PlaylistRepository
from yt_player.playlists.service import PlaylistService


class FakeYoutubeService:
    def __init__(self):
        self.search_types = []
        self.tracks = {
            "alpha": make_track("alpha", title="Alpha Song"),
            "beta": make_track("beta", title="Beta Song"),
            "gamma": make_track("gamma", title="Gamma Song"),
        }

    def search(self, query, limit=10, requested_by=None, search_type=None):
        self.search_types.append(search_type)
        if query == "missing":
            raise ServiceError("TRACK_NOT_FOUND", "No tracks found for query.", 404)
        track = self.tracks.get(query, make_track(query, title=query.title()))
        return [track.model_copy(update={"requested_by": requested_by, "original_query": query})][
            :limit
        ]

    def resolve_url(self, url, requested_by=None):
        source_id = url.rsplit("=", 1)[-1]
        return make_track(source_id, requested_by=requested_by)

    def resolve(self, *, url=None, query=None, track=None, requested_by=None, search_type=None):
        if track is not None:
            return Track(
                id=track.id or f"{track.source}:{track.source_id}",
                source=track.source,
                source_id=track.source_id,
                url=track.url,
                title=track.title or track.url,
                artist=track.artist,
                duration_seconds=track.duration_seconds,
                thumbnail_url=track.thumbnail_url,
                requested_by=requested_by or track.requested_by,
                original_query=track.original_query,
            )
        if url is not None:
            return self.resolve_url(url, requested_by=requested_by)
        return self.search(query, 1, requested_by=requested_by, search_type=search_type)[0]


class FakeBackend:
    def __init__(self):
        self.started_urls = []
        self.started_calls = []
        self.volume_changes = []
        self.live_volume_supported = True
        self.running = False
        self.exit_status_result = None

    def start_url(self, url, **kwargs):
        self.started_urls.append(url)
        self.started_calls.append({"url": url, **kwargs})
        self.running = True
        self.exit_status_result = None

    def stop(self):
        self.running = False

    def pause(self):
        self.running = False

    def resume(self):
        self.running = True

    def set_volume(self, volume):
        self.volume_changes.append(volume)
        return self.live_volume_supported

    def is_running(self):
        return self.running

    def exit_status(self):
        if self.running:
            return None
        return self.exit_status_result


def make_track(source_id, title=None, requested_by=None, duration_seconds=180):
    return Track(
        id=f"youtube:{source_id}",
        source="youtube",
        source_id=source_id,
        url=f"https://www.youtube.com/watch?v={source_id}",
        title=title or f"Track {source_id}",
        artist="Test Artist",
        duration_seconds=duration_seconds,
        thumbnail_url=f"https://img.example/{source_id}.jpg",
        requested_by=requested_by,
    )


def make_client(tmp_path):
    repository = PlaylistRepository(str(tmp_path / "media.sqlite3"))
    backend = FakeBackend()
    app = create_app(
        repository=repository,
        youtube_service=FakeYoutubeService(),
        playback_backend=backend,
        connect_bluetooth_on_startup=False,
    )
    return TestClient(app), backend


def make_controller(tmp_path, backend):
    repository = PlaylistRepository(str(tmp_path / "media.sqlite3"))
    controller = PlaybackController(
        youtube_service=FakeYoutubeService(),
        playback_service=PlaybackService(backend),
        queue_service=QueueService(),
        playlist_service=PlaylistService(repository),
        event_service=EventService(),
    )
    return controller, repository


def test_playlist_crud(tmp_path):
    with make_client(tmp_path)[0] as client:
        created = client.post(
            "/playlists", json={"name": "Favorites", "description": "Saved songs"}
        )
        assert created.status_code == 201
        playlist = created.json()
        assert playlist["name"] == "Favorites"
        assert playlist["tracks"] == []

        listed = client.get("/playlists").json()["playlists"]
        assert listed[0]["track_count"] == 0

        updated = client.patch(
            f"/playlists/{playlist['id']}", json={"name": "Daily Favorites"}
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "Saved songs"

        deleted = client.delete(f"/playlists/{playlist['id']}")
        assert deleted.status_code == 204
        missing = client.get(f"/playlists/{playlist['id']}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "PLAYLIST_NOT_FOUND"


def test_add_remove_and_reorder_playlist_tracks(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist = client.post("/playlists", json={"name": "Nepali Pop"}).json()
        playlist_id = playlist["id"]

        first = client.post(
            f"/playlists/{playlist_id}/tracks", json={"query": "alpha"}
        ).json()
        second = client.post(
            f"/playlists/{playlist_id}/tracks", json={"query": "beta"}
        ).json()

        assert [track["source_id"] for track in second["tracks"]] == ["alpha", "beta"]

        track_ids = [track["id"] for track in second["tracks"]]
        reordered = client.post(
            f"/playlists/{playlist_id}/tracks/reorder",
            json={"track_ids": list(reversed(track_ids))},
        ).json()
        assert [track["source_id"] for track in reordered["tracks"]] == ["beta", "alpha"]

        removed = client.delete(
            f"/playlists/{playlist_id}/tracks/{first['tracks'][0]['id']}"
        ).json()
        assert [track["source_id"] for track in removed["tracks"]] == ["beta"]


def test_add_current_track_to_playlist(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist = client.post("/playlists", json={"name": "Favorites"}).json()
        play_response = client.post("/player/play", json={"query": "alpha"})
        assert play_response.status_code == 200

        updated = client.post(f"/playlists/{playlist['id']}/tracks/current")
        assert updated.status_code == 200
        assert updated.json()["tracks"][0]["source_id"] == "alpha"


def test_queue_append_insert_remove_and_clear(tmp_path):
    with make_client(tmp_path)[0] as client:
        replaced = client.post(
            "/queue",
            json={
                "tracks": [
                    make_track("alpha").model_dump(),
                    make_track("beta").model_dump(),
                ]
            },
        ).json()
        assert replaced["queue_length"] == 2

        inserted = client.post("/queue/tracks/next", json={"query": "gamma"}).json()
        assert [item["track"]["source_id"] for item in inserted["items"]] == [
            "alpha",
            "gamma",
            "beta",
        ]

        gamma_item_id = inserted["items"][1]["id"]
        removed = client.delete(f"/queue/items/{gamma_item_id}").json()
        assert [item["track"]["source_id"] for item in removed["items"]] == [
            "alpha",
            "beta",
        ]

        cleared = client.delete("/queue").json()
        assert cleared["queue_length"] == 0
        assert cleared["items"] == []


def test_playlist_play_next_previous(tmp_path):
    client, backend = make_client(tmp_path)
    with client:
        playlist = client.post("/playlists", json={"name": "Morning"}).json()
        for query in ("alpha", "beta", "gamma"):
            client.post(f"/playlists/{playlist['id']}/tracks", json={"query": query})

        started = client.post(f"/playlists/{playlist['id']}/play", json={}).json()
        assert started["track"]["source_id"] == "alpha"
        assert backend.started_urls[-1].endswith("alpha")

        next_state = client.post("/player/next").json()
        assert next_state["track"]["source_id"] == "beta"

        previous_state = client.post("/player/previous").json()
        assert previous_state["track"]["source_id"] == "alpha"


def test_playback_history_schema_created(tmp_path):
    database_path = tmp_path / "media.sqlite3"
    repository = PlaylistRepository(str(database_path))
    try:
        with sqlite3.connect(database_path) as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(play_history)").fetchall()
            }

        assert {
            "id",
            "track_id",
            "started_at",
            "ended_at",
            "status",
            "start_position_seconds",
            "end_position_seconds",
            "duration_seconds",
            "requested_by",
            "error",
        }.issubset(columns)
    finally:
        repository.close()


def test_playback_history_migrates_requested_by_column(tmp_path):
    database_path = tmp_path / "media.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE play_history (
                id TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                start_position_seconds REAL NOT NULL DEFAULT 0,
                end_position_seconds REAL,
                duration_seconds REAL,
                error TEXT
            )
            """
        )

    repository = PlaylistRepository(str(database_path))
    try:
        with sqlite3.connect(database_path) as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(play_history)").fetchall()
            }
        assert "requested_by" in columns
    finally:
        repository.close()


def test_playback_history_records_play_stop_and_skip(tmp_path):
    client, _backend = make_client(tmp_path)
    with client:
        play_response = client.post(
            "/player/play", json={"query": "alpha", "requested_by": "Hermes"}
        )
        assert play_response.status_code == 200
        stopped = client.post("/player/stop")
        assert stopped.status_code == 200

        playlist = client.post("/playlists", json={"name": "History"}).json()
        client.post(f"/playlists/{playlist['id']}/tracks", json={"query": "alpha"})
        client.post(f"/playlists/{playlist['id']}/tracks", json={"query": "beta"})
        client.post(f"/playlists/{playlist['id']}/play", json={})
        skipped = client.post("/player/next")
        assert skipped.status_code == 200

        page = client.get("/player/history").json()
        history = page["items"]

        assert [entry["track"]["source_id"] for entry in history[:3]] == [
            "beta",
            "alpha",
            "alpha",
        ]
        assert page["limit"] == 100
        assert page["window_start"] is not None
        assert page["window_end"] is not None
        assert [entry["status"] for entry in history[:3]] == [
            "playing",
            "skipped",
            "stopped",
        ]
        assert history[2]["requested_by"] == "Hermes"
        assert history[1]["ended_at"] is not None
        assert history[2]["ended_at"] is not None


def test_playback_history_freezes_requested_by_per_play(tmp_path):
    client, _backend = make_client(tmp_path)
    with client:
        client.post("/player/play", json={"query": "alpha", "requested_by": "Ada"})
        client.post("/player/stop")
        client.post("/player/play", json={"query": "alpha", "requested_by": "Grace"})
        client.post("/player/stop")

        page = client.get("/player/history?limit=2").json()
        history = page["items"]

        assert [entry["requested_by"] for entry in history] == ["Grace", "Ada"]
        assert [entry["track"]["source_id"] for entry in history] == ["alpha", "alpha"]


def test_playback_history_paginates_with_next_before(tmp_path):
    client, _backend = make_client(tmp_path)
    with client:
        client.post("/player/play", json={"query": "alpha", "requested_by": "Ada"})
        client.post("/player/stop")
        client.post("/player/play", json={"query": "beta", "requested_by": "Grace"})
        client.post("/player/stop")

        first_page = client.get("/player/history?limit=1").json()
        second_page = client.get(
            "/player/history",
            params={"limit": 1, "before": first_page["next_before"]},
        ).json()

        assert first_page["items"][0]["requested_by"] == "Grace"
        assert first_page["next_before"] is not None
        assert second_page["items"][0]["requested_by"] == "Ada"


def test_volume_change_uses_live_backend_volume_without_restart(tmp_path):
    client, backend = make_client(tmp_path)
    with client:
        play_response = client.post("/player/play", json={"query": "alpha"})
        assert play_response.status_code == 200
        started_count = len(backend.started_urls)

        volume_response = client.post("/player/volume", json={"volume": 0.35})

        assert volume_response.status_code == 200
        assert len(backend.started_urls) == started_count
        assert backend.volume_changes == [0.35]


def test_playback_service_recovers_backend_failure_from_current_position():
    async def scenario():
        backend = FakeBackend()
        service = PlaybackService(backend)
        await service.play(make_track("alpha", duration_seconds=240))
        await service.seek(42)

        backend.running = False
        exit_status = BackendExitStatus(yt_returncode=1, player_returncode=0)
        backend.exit_status_result = exit_status

        state = await service.recover_backend_exit(exit_status)

        assert state.state == "playing"
        assert state.error is None
        assert backend.started_urls[-1].endswith("alpha")
        assert backend.started_calls[-1]["position_seconds"] >= 42

    asyncio.run(scenario())


def test_reconcile_recovers_failed_backend_without_advancing_queue(tmp_path):
    async def scenario():
        backend = FakeBackend()
        controller, repository = make_controller(tmp_path, backend)
        try:
            await controller.play(PlayRequest(query="alpha"))
            await controller.append_queue_track(AddTrackRequest(query="beta"))

            backend.running = False
            backend.exit_status_result = BackendExitStatus(
                yt_returncode=1,
                player_returncode=0,
            )

            await controller.reconcile_playback()
            state = await controller.get_state()
            queue_state = await controller.queue.snapshot()

            assert state.state == "playing"
            assert state.track.source_id == "alpha"
            assert queue_state.current_index == 0
            assert backend.started_urls[-1].endswith("alpha")
        finally:
            await controller.shutdown()
            repository.close()

    asyncio.run(scenario())


def test_playlist_import_export_renames_duplicates(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist = client.post("/playlists", json={"name": "Road Trip"}).json()
        client.post(f"/playlists/{playlist['id']}/tracks", json={"query": "alpha"})

        exported = client.get(f"/playlists/{playlist['id']}/export")
        assert exported.status_code == 200
        payload = exported.json()
        assert payload["version"] == 1

        imported = client.post("/playlists/import", json=payload)
        assert imported.status_code == 200
        body = imported.json()
        assert body["renamed"] == {"Road Trip": "Road Trip (imported)"}
        assert body["imported"][0]["name"] == "Road Trip (imported)"
        assert body["imported"][0]["tracks"][0]["source_id"] == "alpha"

        bad_import = client.post("/playlists/import", json={"version": 2, "playlists": []})
        assert bad_import.status_code == 400
        assert bad_import.json()["error"]["code"] == "UNSUPPORTED_IMPORT_VERSION"


def track_ids(playlist):
    return [track["source_id"] for track in playlist["tracks"]]


def test_adding_a_duplicate_track_reports_it(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist_id = client.post("/playlists", json={"name": "Mix"}).json()["id"]
        client.post(f"/playlists/{playlist_id}/tracks", json={"query": "alpha"})

        response = client.post(f"/playlists/{playlist_id}/tracks", json={"query": "alpha"})

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "PLAYLIST_TRACK_EXISTS"
        assert error["message"] == "'Alpha Song' is already in playlist 'Mix'."

        client.post("/player/play", json={"query": "alpha"})
        current = client.post(f"/playlists/{playlist_id}/tracks/current")
        assert current.status_code == 409


def test_bulk_add_reports_added_skipped_and_failed(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist_id = client.post("/playlists", json={"name": "Mix"}).json()["id"]
        client.post(f"/playlists/{playlist_id}/tracks", json={"query": "gamma"})

        response = client.post(
            f"/playlists/{playlist_id}/tracks/bulk",
            json={
                "items": [
                    {"query": "alpha"},
                    {"query": "missing"},
                    {"url": "https://www.youtube.com/watch?v=beta"},
                    {"query": "gamma"},
                    {"query": "alpha"},
                ]
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert [t["source_id"] for t in body["added"]] == ["alpha", "beta"]
        assert [t["source_id"] for t in body["skipped"]] == ["gamma", "alpha"]
        assert body["failed"] == [{"input": "missing", "error": "No tracks found for query."}]
        assert track_ids(body["playlist"]) == ["gamma", "alpha", "beta"]

        missing = client.post("/playlists/nope/tracks/bulk", json={"items": [{"query": "alpha"}]})
        assert missing.status_code == 404


def test_move_playlist_track(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist_id = client.post("/playlists", json={"name": "Mix"}).json()["id"]
        client.post(
            f"/playlists/{playlist_id}/tracks/bulk",
            json={"items": [{"query": "alpha"}, {"query": "beta"}, {"query": "gamma"}]},
        )

        moved = client.post(
            f"/playlists/{playlist_id}/tracks/youtube:gamma/move", json={"position": 0}
        )
        assert track_ids(moved.json()) == ["gamma", "alpha", "beta"]

        to_end = client.post(
            f"/playlists/{playlist_id}/tracks/youtube:gamma/move", json={"position": 99}
        )
        assert track_ids(to_end.json()) == ["alpha", "beta", "gamma"]

        unknown = client.post(
            f"/playlists/{playlist_id}/tracks/youtube:nope/move", json={"position": 0}
        )
        assert unknown.status_code == 404


def test_save_queue_to_playlist(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist_id = client.post("/playlists", json={"name": "Friday"}).json()["id"]

        empty = client.post(f"/playlists/{playlist_id}/tracks/queue")
        assert empty.status_code == 409
        assert empty.json()["error"]["code"] == "QUEUE_EMPTY"

        for query in ["beta", "alpha", "gamma"]:
            client.post("/queue/tracks", json={"query": query})
        client.post(f"/playlists/{playlist_id}/tracks", json={"query": "alpha"})

        result = client.post(f"/playlists/{playlist_id}/tracks/queue").json()

        assert [t["source_id"] for t in result["added"]] == ["beta", "gamma"]
        assert [t["source_id"] for t in result["skipped"]] == ["alpha"]
        assert track_ids(result["playlist"]) == ["alpha", "beta", "gamma"]


def test_queue_playlist_appends_or_plays_next_without_starting(tmp_path):
    client, backend = make_client(tmp_path)
    with client:
        playlist_id = client.post("/playlists", json={"name": "Mix"}).json()["id"]
        client.post(
            f"/playlists/{playlist_id}/tracks/bulk",
            json={"items": [{"query": "beta"}, {"query": "gamma"}]},
        )
        client.post("/player/play", json={"query": "alpha"})
        client.post("/queue/tracks", json={"query": "delta"})
        started_before = len(backend.started_urls)

        at_end = client.post(f"/queue/playlists/{playlist_id}").json()
        assert [i["track"]["source_id"] for i in at_end["items"]] == [
            "alpha", "delta", "beta", "gamma"
        ]

        client.delete("/queue")
        client.post("/player/play", json={"query": "alpha"})
        client.post("/queue/tracks", json={"query": "delta"})
        next_up = client.post(
            f"/queue/playlists/{playlist_id}", json={"play_next": True}
        ).json()
        assert [i["track"]["source_id"] for i in next_up["items"]] == [
            "alpha", "beta", "gamma", "delta"
        ]
        assert next_up["current_index"] == 0
        assert len(backend.started_urls) == started_before + 1  # only the second /player/play

        empty_id = client.post("/playlists", json={"name": "Empty"}).json()["id"]
        assert client.post(f"/queue/playlists/{empty_id}").status_code == 409


def test_import_skips_duplicate_tracks_within_a_playlist(tmp_path):
    track = {
        "source": "youtube",
        "source_id": "alpha",
        "url": "https://www.youtube.com/watch?v=alpha",
        "title": "Alpha Song",
    }
    with make_client(tmp_path)[0] as client:
        response = client.post(
            "/playlists/import",
            json={"version": 1, "playlists": [{"name": "Dupes", "tracks": [track, track]}]},
        )

        assert response.status_code == 200
        assert len(response.json()["imported"][0]["tracks"]) == 1


def test_playlist_description_can_be_kept_changed_and_cleared(tmp_path):
    with make_client(tmp_path)[0] as client:
        playlist_id = client.post(
            "/playlists", json={"name": "Mix", "description": "Old"}
        ).json()["id"]

        renamed = client.patch(f"/playlists/{playlist_id}", json={"name": "Mix 2"}).json()
        assert (renamed["name"], renamed["description"]) == ("Mix 2", "Old")

        cleared = client.patch(f"/playlists/{playlist_id}", json={"description": None}).json()
        assert (cleared["name"], cleared["description"]) == ("Mix 2", None)

        blank = client.post("/playlists", json={"name": "Blank", "description": "   "}).json()
        assert blank["description"] is None
