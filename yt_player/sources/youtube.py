import hashlib
import re
from typing import get_args
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic

from yt_player.core.config import DEFAULT_SEARCH_TYPE
from yt_player.core.errors import ServiceError
from yt_player.core.ytdlp import ytdlp_api_options
from yt_player.env import ConfigError
from yt_player.player.models import SearchType, Track, TrackInput


ytmusic = YTMusic()

SEARCH_TYPES: tuple[str, ...] = get_args(SearchType)
# Search types served by YouTube Music, mapped to its search filters. "videos"
# searches all of YouTube through yt-dlp instead.
YTMUSIC_FILTERS = {"songs": "songs", "music_videos": "videos", "podcasts": "episodes"}

if DEFAULT_SEARCH_TYPE not in SEARCH_TYPES:
    raise ConfigError(
        f"DEFAULT_SEARCH_TYPE must be one of {', '.join(SEARCH_TYPES)}, got {DEFAULT_SEARCH_TYPE!r}"
    )


def parse_duration_seconds(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    parts = value.split(":")
    if not all(part.isdigit() for part in parts):
        return None

    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return float(seconds)


def extract_youtube_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "youtu.be" in host:
        return parsed.path.strip("/") or None

    if "youtube.com" not in host:
        return None

    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id

    match = re.match(r"^/(embed|shorts|live)/([^/?#]+)", parsed.path)
    if match:
        return match.group(2)

    return None


def make_track_id(source: str, source_id: str | None, url: str) -> str:
    if source_id:
        return f"{source}:{source_id}"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{source}:url:{digest}"


def materialize_track(track: TrackInput, requested_by: str | None = None) -> Track:
    source = track.source or "youtube"
    source_id = track.source_id or (extract_youtube_id(track.url) if source == "youtube" else None)
    track_id = track.id or make_track_id(source, source_id, track.url)

    return Track(
        id=track_id,
        source=source,
        source_id=source_id,
        url=track.url,
        title=track.title or track.url,
        artist=track.artist,
        duration_seconds=track.duration_seconds,
        thumbnail_url=track.thumbnail_url,
        requested_by=requested_by or track.requested_by,
        original_query=track.original_query,
    )


def _best_thumbnail(result: dict) -> str | None:
    thumbnails = result.get("thumbnails") or []
    if not thumbnails:
        return None
    return thumbnails[-1].get("url")


def _artist_from_result(result: dict) -> str | None:
    artists = result.get("artists") or []
    names = [artist.get("name") for artist in artists if artist.get("name")]
    if names:
        return ", ".join(names)
    podcast = result.get("podcast")
    if isinstance(podcast, dict) and podcast.get("name"):
        return podcast["name"]
    return None


def _track_from_search_result(
    result: dict, query: str | None = None, requested_by: str | None = None
) -> Track:
    source_id = result.get("videoId")
    url = f"https://www.youtube.com/watch?v={source_id}" if source_id else result.get("url", "")
    title = result.get("title") or url
    duration = result.get("duration_seconds")
    if duration is None:
        duration = parse_duration_seconds(result.get("duration"))

    return Track(
        id=make_track_id("youtube", source_id, url),
        source="youtube",
        source_id=source_id,
        url=url,
        title=title,
        artist=_artist_from_result(result),
        duration_seconds=duration,
        thumbnail_url=_best_thumbnail(result),
        requested_by=requested_by,
        original_query=query,
    )


def _track_from_video_result(
    entry: dict, query: str | None = None, requested_by: str | None = None
) -> Track:
    """Track from a yt-dlp search entry (any YouTube video)."""
    source_id = entry["id"]
    url = f"https://www.youtube.com/watch?v={source_id}"
    return Track(
        id=make_track_id("youtube", source_id, url),
        source="youtube",
        source_id=source_id,
        url=url,
        title=entry.get("title") or url,
        artist=entry.get("channel") or entry.get("uploader"),
        duration_seconds=parse_duration_seconds(entry.get("duration")),
        thumbnail_url=_best_thumbnail(entry),
        requested_by=requested_by,
        original_query=query,
    )


def _legacy_track_from_search_result(result: dict) -> dict:
    source_id = result.get("videoId")
    artists = result.get("artists") or []
    album = result.get("album") or {}
    return {
        "title": result.get("title"),
        "videoId": source_id,
        "url": f"https://www.youtube.com/watch?v={source_id}" if source_id else result.get("url"),
        "artists": [artist.get("name") for artist in artists if artist.get("name")],
        "album": album.get("name") if isinstance(album, dict) else None,
        "duration": result.get("duration_seconds") or parse_duration_seconds(result.get("duration")),
        "thumbnail": _best_thumbnail(result),
    }


class YouTubeService:
    def __init__(self, ytmusic_client: YTMusic | None = None) -> None:
        self.ytmusic = ytmusic_client or ytmusic

    def search(
        self,
        query: str,
        limit: int = 10,
        requested_by: str | None = None,
        search_type: SearchType | None = None,
    ) -> list[Track]:
        search_type = search_type or DEFAULT_SEARCH_TYPE
        if search_type == "videos":
            return [
                _track_from_video_result(entry, query=query, requested_by=requested_by)
                for entry in self.search_youtube(query, limit)
                if entry.get("id")
            ]

        results = self.ytmusic.search(query, filter=YTMUSIC_FILTERS[search_type], limit=limit)
        return [
            _track_from_search_result(result, query=query, requested_by=requested_by)
            for result in results[:limit]
            if result.get("videoId")
        ]

    def search_youtube(self, query: str, limit: int) -> list[dict]:
        options = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            **ytdlp_api_options(),
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except Exception as exc:
            raise ServiceError("SEARCH_FAILED", f"YouTube search failed: {exc}", 502) from exc
        return list((info or {}).get("entries") or [])

    def resolve_url(self, url: str, requested_by: str | None = None) -> Track:
        source_id = extract_youtube_id(url)
        fallback = Track(
            id=make_track_id("youtube", source_id, url),
            source="youtube",
            source_id=source_id,
            url=url,
            title=url,
            requested_by=requested_by,
        )

        options = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            **ytdlp_api_options(),
        }

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return fallback

        if not isinstance(info, dict):
            return fallback

        resolved_url = info.get("webpage_url") or url
        resolved_source_id = info.get("id") or source_id
        return Track(
            id=make_track_id("youtube", resolved_source_id, resolved_url),
            source="youtube",
            source_id=resolved_source_id,
            url=resolved_url,
            title=info.get("title") or resolved_url,
            artist=info.get("artist") or info.get("creator"),
            duration_seconds=parse_duration_seconds(info.get("duration")),
            thumbnail_url=info.get("thumbnail"),
            requested_by=requested_by,
        )

    def resolve(
        self,
        *,
        url: str | None = None,
        query: str | None = None,
        track: TrackInput | None = None,
        requested_by: str | None = None,
        search_type: SearchType | None = None,
    ) -> Track:
        if track is not None:
            return materialize_track(track, requested_by=requested_by)
        if url is not None:
            return self.resolve_url(url, requested_by=requested_by)
        if query is not None:
            results = self.search(
                query, limit=1, requested_by=requested_by, search_type=search_type
            )
            if not results:
                raise ServiceError("TRACK_NOT_FOUND", "No tracks found for query.", 404)
            return results[0]
        raise ServiceError("INVALID_TRACK_REQUEST", "Provide a track, URL, or query.", 400)


def music_search(query, limit=10):
    results = ytmusic.search(query, filter="songs")
    return [_legacy_track_from_search_result(result) for result in results[:limit]]
