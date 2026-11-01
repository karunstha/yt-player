import asyncio

import httpx

from yt_player.lyrics.matching import LyricsQuery, pick_best

LRCLIB_URL = "https://lrclib.net/api"
# LRCLIB asks clients to identify themselves.
USER_AGENT = "yt-player (https://github.com/karunstha/yt-player)"


class LrclibProvider:
    """Finds lyrics on LRCLIB, a free database of synced (LRC) lyrics."""

    name = "lrclib"

    def __init__(self, base_url: str = LRCLIB_URL, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> httpx.Response:
        # LRCLIB answers 503/429 under load; a short retry usually succeeds.
        for attempt in range(3):
            response = await client.get(path, params=params)
            if response.status_code not in (429, 500, 502, 503, 504):
                return response
            await asyncio.sleep(0.5 * 2**attempt)
        return response

    async def find(self, query: LyricsQuery) -> dict | None:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            # 1. Exact lookup: LRCLIB matches title, artist, and duration itself.
            if query.artist and query.duration_seconds:
                response = await self._get(
                    client,
                    "/get",
                    params={
                        "track_name": query.title,
                        "artist_name": query.artist,
                        "duration": round(query.duration_seconds),
                    },
                )
                # Anything but a hit (404, or a 5xx when LRCLIB has no exact
                # match cached) falls through to the searches below.
                if response.status_code == 200:
                    found = pick_best(query, [response.json()])
                    if found:
                        return found

            # 2. Field search, then 3. free-text search for messier titles.
            searches = []
            if query.artist:
                searches.append({"track_name": query.title, "artist_name": query.artist})
            searches.append({"q": f"{query.artist or ''} {query.title}".strip()})
            for params in searches:
                response = await self._get(client, "/search", params)
                response.raise_for_status()
                results = response.json()
                found = pick_best(query, results if isinstance(results, list) else [])
                if found:
                    return found
        return None
