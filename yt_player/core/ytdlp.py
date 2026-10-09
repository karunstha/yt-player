"""yt-dlp options shared by the playback backend (CLI) and source lookups (Python API)."""

import os

from yt_player.core.config import COOKIES, JS_RUNTIME, YTDLP_REMOTE_COMPONENTS
from yt_player.env import ConfigError

SUPPORTED_JS_RUNTIMES = ("deno", "node", "bun", "quickjs")
SUPPORTED_REMOTE_COMPONENTS = ("ejs:github", "ejs:npm")


def parse_js_runtimes(value: str) -> dict[str, str | None]:
    """Parse "deno", "node:/usr/bin/node" or "deno,node" into {runtime: path}."""
    runtimes: dict[str, str | None] = {}
    for item in value.split(","):
        name, _, path = item.strip().partition(":")
        name = name.strip().lower()
        if not name:
            continue
        if name not in SUPPORTED_JS_RUNTIMES:
            expected = ", ".join(SUPPORTED_JS_RUNTIMES)
            raise ConfigError(f"Unsupported JS_RUNTIME {name!r}; expected {expected}")
        runtimes[name] = os.path.expanduser(path.strip()) if path.strip() else None
    return runtimes or {"deno": None}


def parse_remote_components(value: str) -> list[str]:
    components = [item.strip().lower() for item in value.split(",") if item.strip()]
    if components == ["none"]:
        return []
    for component in components:
        if component not in SUPPORTED_REMOTE_COMPONENTS:
            expected = ", ".join(SUPPORTED_REMOTE_COMPONENTS)
            raise ConfigError(
                f"Unsupported YTDLP_REMOTE_COMPONENTS {component!r}; expected {expected} or none"
            )
    return components


JS_RUNTIMES = parse_js_runtimes(JS_RUNTIME)
REMOTE_COMPONENTS = parse_remote_components(YTDLP_REMOTE_COMPONENTS)


def ytdlp_cli_args() -> list[str]:
    # --no-js-runtimes first: yt-dlp otherwise keeps deno enabled, and deno
    # outranks every other runtime whenever it is installed.
    args = ["--no-js-runtimes"]
    for name, path in JS_RUNTIMES.items():
        args.extend(["--js-runtimes", f"{name}:{path}" if path else name])
    for component in REMOTE_COMPONENTS:
        args.extend(["--remote-components", component])
    if os.path.exists(COOKIES):
        args.extend(["--cookies", COOKIES])
    return args


def ytdlp_api_options() -> dict:
    options: dict = {
        "js_runtimes": {name: {"path": path} if path else {} for name, path in JS_RUNTIMES.items()},
        "remote_components": list(REMOTE_COMPONENTS),
    }
    if os.path.exists(COOKIES):
        options["cookiefile"] = COOKIES
    return options
