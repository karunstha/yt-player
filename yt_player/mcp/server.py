from typing import Any, Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from yt_player.env import ConfigError
from yt_player.mcp.client import MediaServiceClient
from yt_player.mcp.config import (
    MCP_HOST,
    MCP_MESSAGE_PATH,
    MCP_PORT,
    MCP_SSE_PATH,
    MCP_STREAMABLE_HTTP_PATH,
    MCP_TRANSPORT,
    MEDIA_SERVICE_URL,
)


McpTransport = Literal["stdio", "sse", "streamable-http"]
# Mirrors yt_player.player.models.SearchType without importing the service code.
SearchType = Literal["songs", "music_videos", "podcasts", "videos"]
SEARCH_TYPE_HELP = (
    "search_type picks what a query looks for: songs (YouTube Music songs), music_videos, "
    "podcasts (podcast episodes), or videos (anything on YouTube, e.g. tutorials, talks, "
    "live streams). Omit it to use the service default, normally songs."
)
SUPPORTED_TRANSPORTS: set[str] = {"stdio", "sse", "streamable-http"}


mcp = MCPServer(
    "yt-player",
    title="YouTube Media Playback Service",
    description="Control the local media playback service over its HTTP API.",
    instructions=(
        "Use these tools to search media, control playback, manage queues, "
        "and manage playlists. The media service must already be running. "
        + SEARCH_TYPE_HELP
    ),
)


def client() -> MediaServiceClient:
    return MediaServiceClient()


@mcp.tool()
async def health() -> dict[str, Any]:
    """Return the configured media service URL and current playback state."""
    state = await client().get("/player/state")
    return {"media_service_url": MEDIA_SERVICE_URL, "state": state}


@mcp.tool()
async def get_playback_state() -> dict[str, Any]:
    """Get the canonical playback state."""
    return await client().get("/player/state")


@mcp.tool()
async def get_current_track() -> dict[str, Any]:
    """Get the current track."""
    return await client().get("/player/current")


@mcp.tool()
async def search_media(
    query: str, count: int = 10, search_type: SearchType | None = None
) -> dict[str, Any]:
    """Search YouTube and return normalized tracks. search_type: songs (default),
    music_videos, podcasts (podcast episodes), or videos (anything on YouTube)."""
    params = compact({"q": query, "count": count, "type": search_type})
    return await client().get("/search", params=params)


@mcp.tool()
async def play_media(
    query: str | None = None,
    url: str | None = None,
    requested_by: str | None = None,
    search_type: SearchType | None = None,
) -> dict[str, Any]:
    """Play media by search query or URL."""
    payload = compact(
        {"query": query, "url": url, "requested_by": requested_by, "search_type": search_type}
    )
    return await client().post("/player/play", json=payload)


@mcp.tool()
async def pause_playback() -> dict[str, Any]:
    """Pause playback."""
    return await client().post("/player/pause")


@mcp.tool()
async def resume_playback() -> dict[str, Any]:
    """Resume playback."""
    return await client().post("/player/resume")


@mcp.tool()
async def stop_playback() -> dict[str, Any]:
    """Stop playback."""
    return await client().post("/player/stop")


@mcp.tool()
async def seek_playback(position_seconds: float) -> dict[str, Any]:
    """Seek the current track to an absolute position in seconds."""
    return await client().post(
        "/player/seek", json={"position_seconds": position_seconds}
    )


@mcp.tool()
async def next_track() -> dict[str, Any]:
    """Skip to the next queue item."""
    return await client().post("/player/next")


@mcp.tool()
async def previous_track() -> dict[str, Any]:
    """Return to the previous queue item."""
    return await client().post("/player/previous")


@mcp.tool()
async def set_volume(volume: float) -> dict[str, Any]:
    """Set player volume from 0.0 to 1.0."""
    return await client().post("/player/volume", json={"volume": volume})


@mcp.tool()
async def get_playback_history(
    days: int = 7,
    before: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Get one playback history page. Use next_before to fetch older pages."""
    return await client().get(
        "/player/history",
        params=compact({"days": days, "before": before, "limit": limit}),
    )


@mcp.tool()
async def set_shuffle(shuffle: bool) -> dict[str, Any]:
    """Enable or disable queue shuffle."""
    return await client().post("/player/shuffle", json={"shuffle": shuffle})


@mcp.tool()
async def set_repeat(repeat_mode: Literal["off", "one", "all"]) -> dict[str, Any]:
    """Set repeat mode."""
    return await client().post("/player/repeat", json={"repeat_mode": repeat_mode})


@mcp.tool()
async def get_queue() -> dict[str, Any]:
    """Inspect the active playback queue."""
    return await client().get("/queue")


@mcp.tool()
async def replace_queue_with_playlist(
    playlist_id: str,
    shuffle: bool = False,
) -> dict[str, Any]:
    """Replace the active queue with tracks from a playlist without starting playback."""
    return await client().post(
        "/queue", json={"playlist_id": playlist_id, "shuffle": shuffle}
    )


@mcp.tool()
async def append_queue_track(
    query: str | None = None,
    url: str | None = None,
    requested_by: str | None = None,
    search_type: SearchType | None = None,
) -> dict[str, Any]:
    """Append a track to the active queue by query or URL."""
    payload = compact(
        {"query": query, "url": url, "requested_by": requested_by, "search_type": search_type}
    )
    return await client().post("/queue/tracks", json=payload)


@mcp.tool()
async def insert_queue_track_next(
    query: str | None = None,
    url: str | None = None,
    requested_by: str | None = None,
    search_type: SearchType | None = None,
) -> dict[str, Any]:
    """Insert a track after the current queue item by query or URL."""
    payload = compact(
        {"query": query, "url": url, "requested_by": requested_by, "search_type": search_type}
    )
    return await client().post("/queue/tracks/next", json=payload)


@mcp.tool()
async def queue_playlist(
    playlist_id: str,
    play_next: bool = False,
    shuffle: bool = False,
) -> dict[str, Any]:
    """Add a playlist's tracks to the current queue without replacing it, at the end
    or right after the current track. Does not start playback by itself."""
    return await client().post(
        f"/queue/playlists/{playlist_id}", json={"play_next": play_next, "shuffle": shuffle}
    )


@mcp.tool()
async def remove_queue_item(item_id: str) -> dict[str, Any]:
    """Remove an item from the active queue by queue item ID or track ID."""
    return await client().delete(f"/queue/items/{item_id}")


@mcp.tool()
async def clear_queue() -> dict[str, Any]:
    """Clear the active queue."""
    return await client().delete("/queue")


@mcp.tool()
async def list_playlists() -> dict[str, Any]:
    """List persistent playlists."""
    return await client().get("/playlists")


@mcp.tool()
async def get_playlist(playlist_id: str) -> dict[str, Any]:
    """Get a playlist by ID, exact name, or slug."""
    return await client().get(f"/playlists/{playlist_id}")


@mcp.tool()
async def create_playlist(name: str, description: str | None = None) -> dict[str, Any]:
    """Create a persistent playlist."""
    return await client().post(
        "/playlists", json=compact({"name": name, "description": description})
    )


@mcp.tool()
async def rename_playlist(
    playlist_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename a playlist and/or change its description. Leave a field out to keep it;
    pass description="" to remove the description."""
    if name is None and description is None:
        raise ToolError("Provide a new name, a description, or both.")
    payload: dict[str, Any] = compact({"name": name})
    if description is not None:
        payload["description"] = description.strip() or None
    return await client().patch(f"/playlists/{playlist_id}", json=payload)


@mcp.tool()
async def delete_playlist(playlist_id: str) -> dict[str, Any]:
    """Delete a playlist."""
    return await client().delete(f"/playlists/{playlist_id}")


@mcp.tool()
async def add_track_to_playlist(
    playlist_id: str,
    query: str | None = None,
    url: str | None = None,
    requested_by: str | None = None,
    search_type: SearchType | None = None,
) -> dict[str, Any]:
    """Add a track to a playlist by query or URL. Fails if it is already in the playlist."""
    payload = compact(
        {"query": query, "url": url, "requested_by": requested_by, "search_type": search_type}
    )
    return await client().post(f"/playlists/{playlist_id}/tracks", json=payload)


@mcp.tool()
async def add_tracks_to_playlist(
    playlist_id: str,
    tracks: list[str],
    requested_by: str | None = None,
    search_type: SearchType | None = None,
) -> dict[str, Any]:
    """Add several tracks to a playlist in order. Each entry is a URL or a search query;
    search_type applies to the queries. The result lists tracks added, skipped (already in
    the playlist), and failed."""
    items = []
    for entry in tracks:
        source = track_source(entry)
        extra = {"search_type": search_type} if "query" in source else {}
        items.append(compact({**source, **extra, "requested_by": requested_by}))
    return await client().post(f"/playlists/{playlist_id}/tracks/bulk", json={"items": items})


@mcp.tool()
async def save_queue_as_playlist(name: str, description: str | None = None) -> dict[str, Any]:
    """Create a new playlist containing every track in the current queue, in queue order."""
    queue = await client().get("/queue")
    if not queue.get("items"):
        raise ToolError("The queue is empty, so there is nothing to save.")
    playlist = await client().post(
        "/playlists", json=compact({"name": name, "description": description})
    )
    return await client().post(f"/playlists/{playlist['id']}/tracks/queue")


@mcp.tool()
async def add_queue_to_playlist(playlist_id: str) -> dict[str, Any]:
    """Append every track in the current queue to an existing playlist, skipping duplicates."""
    return await client().post(f"/playlists/{playlist_id}/tracks/queue")


@mcp.tool()
async def move_playlist_track(playlist_id: str, track_id: str, position: int) -> dict[str, Any]:
    """Move a track within a playlist. Position is 0-based: 0 moves it to the top,
    and a position past the end moves it to the bottom."""
    return await client().post(
        f"/playlists/{playlist_id}/tracks/{track_id}/move", json={"position": position}
    )


@mcp.tool()
async def add_current_track_to_playlist(playlist_id: str) -> dict[str, Any]:
    """Add the currently loaded track to a playlist."""
    return await client().post(f"/playlists/{playlist_id}/tracks/current")


@mcp.tool()
async def remove_track_from_playlist(playlist_id: str, track_id: str) -> dict[str, Any]:
    """Remove a track from a playlist by track ID."""
    return await client().delete(f"/playlists/{playlist_id}/tracks/{track_id}")


@mcp.tool()
async def clear_playlist(playlist_id: str) -> dict[str, Any]:
    """Remove all tracks from a playlist."""
    return await client().delete(f"/playlists/{playlist_id}/tracks")


@mcp.tool()
async def play_playlist(
    playlist_id: str,
    shuffle: bool = False,
    start_index: int = 0,
) -> dict[str, Any]:
    """Replace the queue with a playlist and start playback."""
    return await client().post(
        f"/playlists/{playlist_id}/play",
        json={"shuffle": shuffle, "start_index": start_index},
    )


@mcp.tool()
async def export_playlists(playlist_id: str | None = None) -> dict[str, Any]:
    """Export all playlists or one playlist in portable JSON format."""
    if playlist_id:
        return await client().get(f"/playlists/{playlist_id}/export")
    return await client().get("/playlists/export")


@mcp.tool()
async def import_playlists(payload: dict[str, Any]) -> dict[str, Any]:
    """Import playlists from the portable JSON format."""
    return await client().post("/playlists/import", json=payload)


def track_source(entry: str) -> dict[str, str]:
    """Treat an entry as a URL if it looks like one, otherwise as a search query."""
    entry = entry.strip()
    lowered = entry.lower()
    if lowered.startswith(("http://", "https://")) or lowered.startswith(
        ("youtu.be/", "youtube.com/", "www.youtube.com/", "music.youtube.com/")
    ):
        return {"url": entry if "://" in entry else f"https://{entry}"}
    return {"query": entry}


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def run(
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    selected = (transport or MCP_TRANSPORT).strip().lower()
    if selected not in SUPPORTED_TRANSPORTS:
        expected = ", ".join(sorted(SUPPORTED_TRANSPORTS))
        raise ConfigError(
            f"Unsupported MCP_TRANSPORT={selected!r}; expected {expected}"
        )

    transport = cast(McpTransport, selected)
    if transport == "stdio":
        mcp.run(transport=transport)
        return

    transport_config: dict[str, Any] = {
        "host": host or MCP_HOST,
        "port": port or MCP_PORT,
    }
    if transport == "streamable-http":
        transport_config["streamable_http_path"] = MCP_STREAMABLE_HTTP_PATH
    if transport == "sse":
        transport_config["sse_path"] = MCP_SSE_PATH
        transport_config["message_path"] = MCP_MESSAGE_PATH

    mcp.run(transport=transport, **transport_config)


if __name__ == "__main__":
    run()
