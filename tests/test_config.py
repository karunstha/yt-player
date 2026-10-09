import sys

import pytest

from yt_player import cli
from yt_player.core import config, ytdlp
from yt_player.env import ConfigError, env_float, env_int, env_str


def test_empty_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("TEST_PORT", "")
    monkeypatch.setenv("TEST_VOLUME", "  ")
    monkeypatch.setenv("TEST_HOST", "")

    assert env_int("TEST_PORT", 5454) == 5454
    assert env_float("TEST_VOLUME", 0.8) == 0.8
    assert env_str("TEST_HOST", "127.0.0.1") == "127.0.0.1"


def test_invalid_values_name_the_setting(monkeypatch):
    monkeypatch.setenv("SERVICE_PORT", "abc")
    with pytest.raises(ConfigError, match="SERVICE_PORT must be a whole number, got 'abc'"):
        env_int("SERVICE_PORT", 5454)

    monkeypatch.setenv("DEFAULT_VOLUME", "1.5")
    with pytest.raises(ConfigError, match="DEFAULT_VOLUME must be at most 1.0"):
        env_float("DEFAULT_VOLUME", 0.8, minimum=0.0, maximum=1.0)


def test_cli_reports_config_errors_without_traceback(monkeypatch):
    def bad_config(**kwargs):
        raise ConfigError("MCP_PORT must be a whole number, got 'x'")

    monkeypatch.setattr("yt_player.mcp.server.run", bad_config)

    with pytest.raises(SystemExit, match="Configuration error: MCP_PORT must be a whole number"):
        cli.main(["mcp"])


def test_yt_player_mcp_entry_point_forwards_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr("yt_player.mcp.server.run", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["yt-player-mcp", "--transport", "sse"])

    cli.mcp_main()

    assert calls == [{"transport": "sse", "host": None, "port": None}]


def test_service_warnings_flag_missing_tools(monkeypatch):
    monkeypatch.setattr(config, "FFPLAY_PATH", "/nonexistent/ffplay")
    monkeypatch.setattr(config, "DEFAULT_BLUETOOTH_DEVICE_ID", "AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(ytdlp, "JS_RUNTIMES", {"node": "/nonexistent/node"})
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    warnings = cli.service_warnings()

    assert any("ffplay not found" in w for w in warnings)
    assert any("No JavaScript runtime found" in w for w in warnings)
    assert any("bluetoothctl is not installed" in w for w in warnings)


def test_service_warnings_empty_when_tools_exist(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_BLUETOOTH_DEVICE_ID", None)
    monkeypatch.setattr(ytdlp, "JS_RUNTIMES", {"deno": None})
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert cli.service_warnings() == []
