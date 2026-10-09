import argparse
import os
import shutil
import sys

from yt_player.env import ConfigError

# Executable that yt-dlp looks for when a runtime is given without a path.
JS_RUNTIME_EXECUTABLES = {"deno": "deno", "node": "node", "bun": "bun", "quickjs": "qjs"}


def service_warnings() -> list[str]:
    """Problems that will not stop the service from starting but will break playback."""
    from yt_player.core import config
    from yt_player.core.ytdlp import JS_RUNTIMES

    warnings = []
    if not shutil.which(config.FFPLAY_PATH):
        warnings.append(
            f"ffplay not found ({config.FFPLAY_PATH}). Install ffmpeg or set FFPLAY_PATH; "
            "playback will fail until then."
        )
    if not any(js_runtime_available(name, path) for name, path in JS_RUNTIMES.items()):
        warnings.append(
            f"No JavaScript runtime found for JS_RUNTIME={config.JS_RUNTIME}. "
            "YouTube playback will fail; install Deno or point JS_RUNTIME at an installed runtime."
        )
    if config.DEFAULT_BLUETOOTH_DEVICE_ID and not shutil.which("bluetoothctl"):
        warnings.append(
            "DEFAULT_BLUETOOTH_DEVICE_ID is set but bluetoothctl is not installed; "
            "the speaker will not be connected automatically."
        )
    return warnings


def js_runtime_available(name: str, path: str | None) -> bool:
    if path:
        return os.path.exists(path)
    return shutil.which(JS_RUNTIME_EXECUTABLES[name]) is not None


def serve(host: str | None, port: int | None) -> None:
    try:
        import uvicorn

        from yt_player.core import config
        from yt_player.factory import create_app
    except ImportError as exc:
        sys.exit(
            f"The playback service needs extra dependencies ({exc.name} is missing). "
            "Install them with: pip install 'yt-player[server]'"
        )

    for warning in service_warnings():
        print(f"WARNING: {warning}", file=sys.stderr)
    print(
        f"yt-player: data in {config.DATA_DIR}, "
        f"audio driver {config.AUDIO_DRIVER or 'auto'}, "
        f"device {config.AUDIO_DEVICE or 'system default'}, "
        f"JS runtime {config.JS_RUNTIME}",
        file=sys.stderr,
    )
    uvicorn.run(create_app(), host=host or config.SERVICE_HOST, port=port or config.SERVICE_PORT)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="yt-player",
        description="Media playback service and MCP server.",
    )
    modes = parser.add_subparsers(dest="mode", required=True)

    serve_parser = modes.add_parser("serve", help="Run the playback service (HTTP API).")
    serve_parser.add_argument("--host", help="Bind host. Defaults to SERVICE_HOST or 127.0.0.1.")
    serve_parser.add_argument("--port", type=int, help="Bind port. Defaults to SERVICE_PORT or 5454.")

    mcp_parser = modes.add_parser("mcp", help="Run the MCP server.")
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        help="Defaults to MCP_TRANSPORT or stdio.",
    )
    mcp_parser.add_argument("--host", help="Bind host for HTTP transports. Defaults to MCP_HOST.")
    mcp_parser.add_argument("--port", type=int, help="Bind port for HTTP transports. Defaults to MCP_PORT.")

    args = parser.parse_args(argv)
    try:
        if args.mode == "serve":
            serve(args.host, args.port)
        else:
            from yt_player.mcp.server import run

            run(transport=args.transport, host=args.host, port=args.port)
    except ConfigError as exc:
        sys.exit(f"Configuration error: {exc}")


def mcp_main() -> None:
    """Entry point for `yt-player-mcp`, the same as `yt-player mcp`."""
    main(["mcp", *sys.argv[1:]])


if __name__ == "__main__":
    main()
