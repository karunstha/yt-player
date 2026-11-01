import pytest

from yt_player import factory


class OfflineLyricsProvider:
    """Keeps tests off the network; individual tests inject their own provider."""

    name = "offline"

    async def find(self, query):
        return None


@pytest.fixture(autouse=True)
def offline_lyrics(monkeypatch):
    monkeypatch.setattr(factory, "LrclibProvider", OfflineLyricsProvider)
