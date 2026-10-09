import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from yt_player import cli
from yt_player.mcp.client import MediaServiceClient
from yt_player.mcp import server as mcp_server
from yt_player.mcp.server import mcp


def test_mcp_server_registers_core_tools():
    tools = asyncio.run(mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    assert "get_playback_state" in tool_names
    assert "play_media" in tool_names
    assert "search_media" in tool_names
    assert "get_playback_history" in tool_names
    assert "list_playlists" in tool_names
    assert "add_current_track_to_playlist" in tool_names
    assert "play_playlist" in tool_names


def test_mcp_run_uses_stdio_by_default(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mcp_server.mcp, "run", fake_run)
    monkeypatch.setattr(mcp_server, "MCP_TRANSPORT", "stdio")

    mcp_server.run()

    assert calls == [{"transport": "stdio"}]


def test_mcp_run_passes_http_transport_config(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mcp_server.mcp, "run", fake_run)
    monkeypatch.setattr(mcp_server, "MCP_TRANSPORT", "streamable-http")
    monkeypatch.setattr(mcp_server, "MCP_HOST", "0.0.0.0")
    monkeypatch.setattr(mcp_server, "MCP_PORT", 5455)
    monkeypatch.setattr(mcp_server, "MCP_STREAMABLE_HTTP_PATH", "/mcp")

    mcp_server.run()

    assert calls == [
        {
            "transport": "streamable-http",
            "host": "0.0.0.0",
            "port": 5455,
            "streamable_http_path": "/mcp",
        }
    ]


def test_mcp_run_rejects_unknown_transport(monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_TRANSPORT", "websocket")

    with pytest.raises(ValueError, match="Unsupported MCP_TRANSPORT"):
        mcp_server.run()


def test_mcp_run_arguments_override_environment(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mcp_server.mcp, "run", fake_run)
    monkeypatch.setattr(mcp_server, "MCP_TRANSPORT", "stdio")
    monkeypatch.setattr(mcp_server, "MCP_SSE_PATH", "/sse")
    monkeypatch.setattr(mcp_server, "MCP_MESSAGE_PATH", "/messages/")

    mcp_server.run(transport="sse", host="127.0.0.1", port=6000)

    assert calls == [
        {
            "transport": "sse",
            "host": "127.0.0.1",
            "port": 6000,
            "sse_path": "/sse",
            "message_path": "/messages/",
        }
    ]


def test_cli_mcp_mode_runs_mcp_server(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run", lambda **kwargs: calls.append(kwargs))

    cli.main(["mcp", "--transport", "streamable-http", "--port", "6001"])

    assert calls == [{"transport": "streamable-http", "host": None, "port": 6001}]


def test_mcp_server_does_not_import_service_dependencies():
    # The base package install only ships mcp and httpx (uvicorn comes with mcp),
    # so the MCP server must not pull in the playback service's dependencies.
    code = (
        "import sys, yt_player.mcp.server, yt_player.cli; "
        "heavy = {'fastapi', 'yt_dlp', 'ytmusicapi', 'dotenv'} & set(sys.modules); "
        "assert not heavy, heavy"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).parents[1])


def test_unreachable_media_service_error_reaches_the_model(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "client", lambda: MediaServiceClient(base_url="http://127.0.0.1:9", timeout=2)
    )

    with pytest.raises(ToolError, match="Could not reach the media service at http://127.0.0.1:9"):
        asyncio.run(mcp.call_tool("health", {}))


def test_track_source_detects_urls_and_queries():
    assert mcp_server.track_source("https://youtu.be/abc") == {"url": "https://youtu.be/abc"}
    assert mcp_server.track_source("music.youtube.com/watch?v=abc") == {
        "url": "https://music.youtube.com/watch?v=abc"
    }
    assert mcp_server.track_source(" lofi beats ") == {"query": "lofi beats"}


def test_save_queue_as_playlist_refuses_empty_queue(monkeypatch):
    calls = []

    class FakeClient:
        async def get(self, path, **kwargs):
            calls.append(("GET", path))
            return {"items": []}

        async def post(self, path, **kwargs):
            calls.append(("POST", path))
            return {}

    monkeypatch.setattr(mcp_server, "client", lambda: FakeClient())

    with pytest.raises(ToolError, match="queue is empty"):
        asyncio.run(mcp.call_tool("save_queue_as_playlist", {"name": "Friday"}))
    assert calls == [("GET", "/queue")]


def test_add_tracks_to_playlist_sends_items_in_order(monkeypatch):
    sent = []

    class FakeClient:
        async def post(self, path, **kwargs):
            sent.append((path, kwargs["json"]))
            return {"added": []}

    monkeypatch.setattr(mcp_server, "client", lambda: FakeClient())

    asyncio.run(
        mcp.call_tool(
            "add_tracks_to_playlist",
            {"playlist_id": "Mix", "tracks": ["https://youtu.be/a", "lofi beats"]},
        )
    )

    assert sent == [
        (
            "/playlists/Mix/tracks/bulk",
            {"items": [{"url": "https://youtu.be/a"}, {"query": "lofi beats"}]},
        )
    ]


def test_rename_playlist_can_clear_description_or_rename_alone(monkeypatch):
    sent = []

    class FakeClient:
        async def patch(self, path, **kwargs):
            sent.append(kwargs["json"])
            return {}

    monkeypatch.setattr(mcp_server, "client", lambda: FakeClient())

    asyncio.run(mcp.call_tool("rename_playlist", {"playlist_id": "Mix", "description": ""}))
    asyncio.run(mcp.call_tool("rename_playlist", {"playlist_id": "Mix", "name": "Mix 2"}))
    assert sent == [{"description": None}, {"name": "Mix 2"}]

    with pytest.raises(ToolError, match="Provide a new name"):
        asyncio.run(mcp.call_tool("rename_playlist", {"playlist_id": "Mix"}))
