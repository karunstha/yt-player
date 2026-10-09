import os

import pytest

from yt_player.core import ytdlp
from yt_player.core.ytdlp import parse_js_runtimes, parse_remote_components


def test_parse_js_runtimes_accepts_names_paths_and_lists():
    assert parse_js_runtimes("deno") == {"deno": None}
    assert parse_js_runtimes("node:/usr/bin/node") == {"node": "/usr/bin/node"}
    assert parse_js_runtimes(" Deno , bun:/opt/bun ") == {"deno": None, "bun": "/opt/bun"}
    assert parse_js_runtimes("") == {"deno": None}
    assert parse_js_runtimes("node:~/bin/node") == {"node": os.path.expanduser("~/bin/node")}


def test_parse_js_runtimes_rejects_unknown_runtime():
    with pytest.raises(ValueError, match="Unsupported JS_RUNTIME 'python'"):
        parse_js_runtimes("python")


def test_parse_remote_components():
    assert parse_remote_components("ejs:github") == ["ejs:github"]
    assert parse_remote_components("none") == []
    assert parse_remote_components("") == []
    with pytest.raises(ValueError, match="Unsupported YTDLP_REMOTE_COMPONENTS"):
        parse_remote_components("ejs:somewhere")


def test_cli_args_replace_default_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp, "JS_RUNTIMES", {"node": "/usr/bin/node", "deno": None})
    monkeypatch.setattr(ytdlp, "REMOTE_COMPONENTS", [])
    monkeypatch.setattr(ytdlp, "COOKIES", str(tmp_path / "missing.txt"))

    assert ytdlp.ytdlp_cli_args() == [
        "--no-js-runtimes",
        "--js-runtimes",
        "node:/usr/bin/node",
        "--js-runtimes",
        "deno",
    ]


def test_api_options_match_cli_settings(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(ytdlp, "JS_RUNTIMES", {"node": "/usr/bin/node", "deno": None})
    monkeypatch.setattr(ytdlp, "REMOTE_COMPONENTS", ["ejs:github"])
    monkeypatch.setattr(ytdlp, "COOKIES", str(cookies))

    assert ytdlp.ytdlp_api_options() == {
        "js_runtimes": {"node": {"path": "/usr/bin/node"}, "deno": {}},
        "remote_components": ["ejs:github"],
        "cookiefile": str(cookies),
    }
