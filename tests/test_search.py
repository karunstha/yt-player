import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from test_media_service import FakeBackend, FakeYoutubeService
from yt_player.factory import create_app
from yt_player.mcp import server as mcp_server
from yt_player.player.models import SearchType
from yt_player.playlists.repository import PlaylistRepository
from yt_player.sources import youtube
from yt_player.sources.youtube import YouTubeService


class FakeYTMusic:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, filter=None, limit=20):
        self.calls.append((query, filter, limit))
        return self.results


SONG = {
    "videoId": "song1",
    "title": "Bohemian Rhapsody",
    "artists": [{"name": "Queen"}],
    "duration_seconds": 355,
    "thumbnails": [{"url": "small"}, {"url": "large"}],
}
EPISODE = {
    "videoId": "ep1",
    "title": "Sam Altman | Lex Fridman Podcast #367",
    "podcast": {"name": "Lex Fridman Podcast"},
    "thumbnails": [],
}
VIDEO = {
    "id": "vid1",
    "title": "How to make sourdough bread",
    "channel": "Natashas Kitchen",
    "duration": 713,
    "thumbnails": [{"url": "thumb"}],
}


@pytest.mark.parametrize(
    "search_type, ytmusic_filter",
    [("songs", "songs"), ("music_videos", "videos"), ("podcasts", "episodes")],
)
def test_youtube_music_search_types_use_matching_filter(search_type, ytmusic_filter):
    ytmusic = FakeYTMusic([SONG])

    tracks = YouTubeService(ytmusic).search("q", limit=5, search_type=search_type)

    assert ytmusic.calls == [("q", ytmusic_filter, 5)]
    assert tracks[0].id == "youtube:song1"
    assert tracks[0].artist == "Queen"
    assert tracks[0].thumbnail_url == "large"


def test_default_search_type_is_used_when_none_given(monkeypatch):
    monkeypatch.setattr(youtube, "DEFAULT_SEARCH_TYPE", "music_videos")
    ytmusic = FakeYTMusic([SONG])

    YouTubeService(ytmusic).search("q")

    assert ytmusic.calls[0][1] == "videos"


def test_podcast_episode_uses_podcast_name_as_artist():
    track = YouTubeService(FakeYTMusic([EPISODE])).search("q", search_type="podcasts")[0]

    assert (track.title, track.artist, track.duration_seconds) == (
        EPISODE["title"],
        "Lex Fridman Podcast",
        None,
    )


def test_videos_search_all_of_youtube(monkeypatch):
    ytmusic = FakeYTMusic([SONG])
    service = YouTubeService(ytmusic)
    monkeypatch.setattr(service, "search_youtube", lambda query, limit: [VIDEO, {"title": "no id"}])

    tracks = service.search("sourdough", limit=3, search_type="videos")

    assert ytmusic.calls == []
    assert len(tracks) == 1
    assert tracks[0].id == "youtube:vid1"
    assert tracks[0].url == "https://www.youtube.com/watch?v=vid1"
    assert (tracks[0].artist, tracks[0].duration_seconds) == ("Natashas Kitchen", 713.0)
    assert tracks[0].original_query == "sourdough"


def test_search_type_reaches_the_source_from_every_endpoint(tmp_path):
    fake = FakeYoutubeService()
    app = create_app(
        repository=PlaylistRepository(str(tmp_path / "media.sqlite3")),
        youtube_service=fake,
        playback_backend=FakeBackend(),
        connect_bluetooth_on_startup=False,
    )
    with TestClient(app) as client:
        assert client.get("/search", params={"q": "x", "type": "podcasts"}).status_code == 200
        client.post("/player/play", json={"query": "x", "search_type": "videos"})
        client.post("/queue/tracks", json={"query": "y", "search_type": "music_videos"})
        playlist_id = client.post("/playlists", json={"name": "Mix"}).json()["id"]
        client.post(f"/playlists/{playlist_id}/tracks", json={"query": "z", "search_type": "songs"})
        client.post(
            f"/playlists/{playlist_id}/tracks/bulk",
            json={"items": [{"query": "w", "search_type": "videos"}]},
        )
        client.get("/search", params={"q": "x"})

        assert fake.search_types == ["podcasts", "videos", "music_videos", "songs", "videos", None]
        assert client.get("/search", params={"q": "x", "type": "movies"}).status_code == 422


def test_invalid_default_search_type_is_a_config_error():
    env = {**os.environ, "DEFAULT_SEARCH_TYPE": "movies"}
    result = subprocess.run(
        [sys.executable, "-c", "import yt_player.sources.youtube"],
        env=env,
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert "DEFAULT_SEARCH_TYPE must be one of songs, music_videos, podcasts, videos" in result.stderr


def test_mcp_search_type_matches_service():
    assert get_args(mcp_server.SearchType) == get_args(SearchType)


def test_mcp_tools_forward_search_type(monkeypatch):
    sent = []

    class FakeClient:
        async def get(self, path, **kwargs):
            sent.append((path, kwargs["params"]))
            return {}

        async def post(self, path, **kwargs):
            sent.append((path, kwargs["json"]))
            return {}

    monkeypatch.setattr(mcp_server, "client", lambda: FakeClient())
    call = lambda tool, args: asyncio.run(mcp_server.mcp.call_tool(tool, args))

    call("search_media", {"query": "sourdough", "search_type": "videos"})
    call("search_media", {"query": "queen"})
    call("play_media", {"query": "lex fridman", "search_type": "podcasts"})
    call(
        "add_tracks_to_playlist",
        {"playlist_id": "Mix", "tracks": ["https://youtu.be/a", "talk"], "search_type": "videos"},
    )

    assert sent == [
        ("/search", {"q": "sourdough", "count": 10, "type": "videos"}),
        ("/search", {"q": "queen", "count": 10}),
        ("/player/play", {"query": "lex fridman", "search_type": "podcasts"}),
        (
            "/playlists/Mix/tracks/bulk",
            {"items": [{"url": "https://youtu.be/a"}, {"query": "talk", "search_type": "videos"}]},
        ),
    ]
