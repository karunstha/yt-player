import asyncio

import pytest
from fastapi.testclient import TestClient

from yt_player.core.errors import ServiceError
from yt_player.events.service import EventService
from yt_player.factory import create_app
from yt_player.lyrics.matching import build_query, parse_lrc, pick_best
from yt_player.lyrics.repository import LyricsRepository
from yt_player.lyrics.service import LyricsService
from yt_player.player.models import QueueItem, QueueState, Track
from yt_player.playlists.repository import PlaylistRepository
from tests.test_media_service import FakeBackend, FakeYoutubeService

SYNCED = "[00:12.50] First line\n[00:15.00] Second line\n[00:15.00]\n"
RECORD = {
    "trackName": "Night Drive",
    "artistName": "Atlas Radio",
    "albumName": "Roads",
    "duration": 245,
    "instrumental": False,
    "plainLyrics": "First line\nSecond line",
    "syncedLyrics": SYNCED,
}


def make_track(source_id="abc", title="Night Drive", artist="Atlas Radio", duration=245):
    return Track(
        id=f"youtube:{source_id}",
        source_id=source_id,
        url=f"https://www.youtube.com/watch?v={source_id}",
        title=title,
        artist=artist,
        duration_seconds=duration,
    )


class FakeProvider:
    name = "fake"

    def __init__(self, record=RECORD, error=None):
        self.record = record
        self.error = error
        self.queries = []

    async def find(self, query):
        self.queries.append(query)
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return self.record


def make_service(tmp_path, provider):
    events = EventService()
    sent = []
    events.add_listener(lambda event_type, data: sent.append((event_type, data)))
    service = LyricsService(
        repository=LyricsRepository(str(tmp_path / "media.sqlite3")),
        provider=provider,
        events=events,
    )
    return service, events, sent


# --- matching ---


@pytest.mark.parametrize(
    ("title", "artist", "expected"),
    [
        ("Night Drive", "Atlas Radio", ("Night Drive", "Atlas Radio")),
        ("Atlas Radio - Night Drive (Official Video)", "AtlasRadioVEVO", ("Night Drive", "Atlas Radio")),
        ("Night Drive [Lyric Video] | 4K", "Atlas Radio - Topic", ("Night Drive", "Atlas Radio")),
        ("Night Drive (feat. Someone)", "Atlas Radio, Someone", ("Night Drive", "Atlas Radio")),
        ("Night Drive - Atlas Radio", "Atlas Radio", ("Night Drive", "Atlas Radio")),
    ],
)
def test_build_query_cleans_upload_titles(title, artist, expected):
    query = build_query(title, artist, 245)
    assert (query.title, query.artist) == expected


def test_parse_lrc_keeps_order_and_blank_lines():
    lines = parse_lrc("[00:15.00] Second\n[00:12.50] First\n[01:00.00][01:30.00] Chorus\n")
    assert [(line.time_seconds, line.text) for line in lines] == [
        (12.5, "First"),
        (15.0, "Second"),
        (60.0, "Chorus"),
        (90.0, "Chorus"),
    ]


def test_pick_best_rejects_a_different_cut_of_the_song():
    query = build_query("Night Drive", "Atlas Radio", 245)
    live = {**RECORD, "duration": 300, "trackName": "Night Drive (Live)"}
    assert pick_best(query, [live]) is None
    assert pick_best(query, [live, RECORD]) is RECORD


def test_pick_best_rejects_candidates_whose_length_is_unknown():
    query = build_query("Night Drive", "Atlas Radio", 245)
    assert pick_best(query, [{**RECORD, "duration": None}]) is None


def test_pick_best_prefers_synced_and_matching_artist():
    query = build_query("Night Drive", "Atlas Radio", 245)
    plain = {**RECORD, "syncedLyrics": None}
    cover = {**RECORD, "artistName": "Cover Band"}
    assert pick_best(query, [plain, cover, RECORD]) is RECORD


def test_pick_best_without_duration_needs_a_plausible_name_match():
    query = build_query("Night Drive", "Atlas Radio", None)
    unrelated = {**RECORD, "trackName": "Something Else", "artistName": "Other"}
    assert pick_best(query, [unrelated]) is None


# --- service ---


def test_lyrics_are_fetched_once_cached_and_announced(tmp_path):
    async def scenario():
        provider = FakeProvider()
        service, _, sent = make_service(tmp_path, provider)
        track = make_track()

        first, second = await asyncio.gather(service.get(track), service.get(track))
        assert first.status == "found" and first.synced
        assert [(line.time_seconds, line.text) for line in first.lines][:2] == [
            (12.5, "First line"),
            (15.0, "Second line"),
        ]
        assert first.match.album == "Roads"
        assert second.lines == first.lines
        assert len(provider.queries) == 1  # concurrent requests share one lookup

        again = await service.get(track)
        assert again.lines == first.lines
        assert len(provider.queries) == 1  # served from the cache
        assert [event for event, _ in sent] == ["lyrics.ready"]

    asyncio.run(scenario())


def test_misses_are_cached_but_failures_are_not(tmp_path):
    async def scenario():
        provider = FakeProvider(error=RuntimeError("offline"))
        service, _, _ = make_service(tmp_path, provider)
        track = make_track()

        with pytest.raises(ServiceError) as raised:
            await service.get(track)
        assert raised.value.code == "LYRICS_UNAVAILABLE"

        provider.error, provider.record = None, None
        assert (await service.get(track)).status == "not_found"
        assert (await service.get(track)).status == "not_found"
        assert len(provider.queries) == 2

    asyncio.run(scenario())


def test_queue_changes_prefetch_current_and_upcoming_tracks(tmp_path):
    async def scenario():
        provider = FakeProvider()
        service, events, sent = make_service(tmp_path, provider)
        tracks = [make_track(str(index), title=f"Song {index}") for index in range(5)]
        queue = QueueState(
            items=[QueueItem(track=track) for track in tracks], current_index=1, queue_length=5
        )

        await events.broadcast("queue.changed", queue.model_dump(mode="json"))
        await asyncio.gather(*service._background)

        # Current plus the default two ahead; earlier and later tracks are left alone.
        assert sorted(query.title for query in provider.queries) == ["Song 1", "Song 2", "Song 3"]
        ready = {data["track_id"] for event, data in sent if event == "lyrics.ready"}
        assert ready == {"youtube:1", "youtube:2", "youtube:3"}
        # A queued track can now be looked up by id alone.
        assert (await service.get_by_id("youtube:4")).status == "found"
        await service.shutdown()

    asyncio.run(scenario())


def test_offset_survives_a_refetch(tmp_path):
    async def scenario():
        service, _, sent = make_service(tmp_path, FakeProvider())
        track = make_track()
        await service.get(track)

        updated = await service.set_offset(track.id, 0.75)
        assert updated.offset_seconds == 0.75
        assert ("lyrics.offset_changed", {"track_id": track.id, "offset_seconds": 0.75}) in sent

        service.repository.save((await service.get(track)).model_copy(update={"offset_seconds": 0}))
        assert (await service.get(track)).offset_seconds == 0.75

    asyncio.run(scenario())


def test_unknown_track_id_is_not_found(tmp_path):
    async def scenario():
        service, _, _ = make_service(tmp_path, FakeProvider())
        with pytest.raises(ServiceError) as raised:
            await service.get_by_id("youtube:missing")
        assert raised.value.status_code == 404

    asyncio.run(scenario())


# --- API ---


def test_lyrics_routes(tmp_path):
    provider = FakeProvider()
    app = create_app(
        repository=PlaylistRepository(str(tmp_path / "media.sqlite3")),
        youtube_service=FakeYoutubeService(),
        playback_backend=FakeBackend(),
        connect_bluetooth_on_startup=False,
        lyrics_provider=provider,
    )
    with TestClient(app) as client:
        assert client.get("/player/lyrics").status_code == 409

        played = client.post("/player/play", json={"query": "night drive"}).json()
        track_id = played["track"]["id"]

        current = client.get("/player/lyrics")
        assert current.status_code == 200
        assert current.json()["track_id"] == track_id
        assert current.json()["synced"] is True

        by_id = client.get(f"/tracks/{track_id}/lyrics").json()
        assert by_id["lines"][0] == {"time_seconds": 12.5, "text": "First line"}

        offset = client.put(f"/tracks/{track_id}/lyrics/offset", json={"offset_seconds": -1.5})
        assert offset.json()["offset_seconds"] == -1.5
        assert client.get(f"/tracks/{track_id}/lyrics").json()["offset_seconds"] == -1.5

        assert client.put(
            f"/tracks/{track_id}/lyrics/offset", json={"offset_seconds": 99}
        ).status_code == 422
        assert client.get("/tracks/youtube:nope/lyrics").status_code == 404
