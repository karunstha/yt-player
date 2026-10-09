# yt-player

An MCP server and media playback service that lets an AI assistant play music on a real speaker. Ask your assistant to "play some lo-fi", "queue the next three tracks from that album", or "add this song to my workout playlist", and the audio plays from the machine running the service, including over Bluetooth.

It has two parts, both started by the `yt-player` command:

- **Playback service** (`yt-player serve`): a FastAPI app that streams audio with yt-dlp and ffplay. It manages a queue, playlists, and playback history, and stores them in SQLite. It also serves a REST API and a WebSocket event stream, so other clients can use it as well.
- **MCP server** (`yt-player mcp`, or `yt-player-mcp`): a thin MCP layer over the service's HTTP API. It supports stdio, streamable HTTP, and SSE transports.

The MCP server is a lightweight install that only depends on `mcp` and `httpx`. It can run on a different machine from the playback service.

YouTube is the first supported source. The API models are source-neutral: clients work with tracks, playback state, queues, playlists, and playback commands.

## Requirements

The playback service plays audio on the machine it runs on.

| Platform | How to run the playback service | Audio |
|---|---|---|
| Linux | Docker Compose (recommended) or `yt-player serve` | PulseAudio, PipeWire, ALSA, or JACK |
| macOS | `yt-player serve` (not Docker) | CoreAudio |
| Windows | Not supported; use WSL or a Linux machine | |

To run it without Docker you need Python 3.10+, `ffmpeg` (which provides `ffplay`), and a JavaScript runtime. They can be on the PATH, or you can give their locations with `FFPLAY_PATH` and `JS_RUNTIME`. YouTube requires yt-dlp to solve a JavaScript challenge. Any of these runtimes works:

| Runtime | Minimum version | `JS_RUNTIME` |
|---|---|---|
| Deno (default, recommended) | 2.3.0 | `deno` |
| Node.js | 22.0.0 | `node` |
| Bun | 1.2.11 | `bun` |
| QuickJS | 2023-12-09 | `quickjs` |

Optional tools:

- `pactl` changes the volume of a playing track without interrupting it. Without it, a volume change restarts the stream at the current position, which causes a short gap.
- `bluetoothctl` (BlueZ, Linux only) lets the service connect a Bluetooth speaker itself.

Docker Desktop on macOS and Windows can't pass host audio through to containers, so use Docker only on Linux. Any machine can run the MCP server and control a playback service running elsewhere (see [Controlling a remote player](#controlling-a-remote-player)).

The MCP server needs Python 3.10+ and network access to the playback service.

## Quick start

1. Clone the repository and create your settings file:

   ```bash
   git clone https://github.com/karunstha/yt-player.git
   cd yt-player
   cp .env.example .env
   mkdir -p data
   ```

   Create `data/` yourself so you own it. If Docker creates it, it belongs to root.

2. Edit `.env`. By default the container plays through your PulseAudio or PipeWire socket. `PULSE_RUNTIME_DIR` must point at `/run/user/<UID>/pulse`; run `id -u` to find your UID. To choose a speaker or use ALSA instead, see [Audio output](#audio-output).

3. Optional but recommended: add YouTube cookies, which make it less likely that YouTube blocks requests as a bot. Export your cookies in Netscape format (for example with a "cookies.txt" browser extension) and save them as `data/cookies.txt`. The service runs without them.

4. Start the playback service:

   ```bash
   docker compose up -d
   ```

5. Check that it responds:

   ```bash
   curl http://127.0.0.1:5454/player/state
   ```

6. Connect an MCP client using one of the options below.

### Running without Docker

On Linux or macOS, with the system tools listed under [Requirements](#requirements):

```bash
pip install "yt-player[server] @ git+https://github.com/karunstha/yt-player"
yt-player serve
```

Without Docker, the service binds to `127.0.0.1:5454`, stores data in `~/.local/share/yt-player`, and plays through the system's default audio output. Use `--host`/`--port` or the [configuration](#configuration) variables to change that, either in the environment or in a `.env` file in the directory you start it from. On macOS, install the system tools first with `brew install ffmpeg deno`. If you already have Node.js 22+, you can skip Deno and set `JS_RUNTIME=node`.

At startup the service prints the settings it is using and warns about anything that would stop playback, such as a missing `ffplay` or JavaScript runtime. Services started by launchd or systemd get a minimal PATH that usually excludes Homebrew and nvm. In that case, set `FFPLAY_PATH` and `JS_RUNTIME` to full paths, for example `FFPLAY_PATH=/opt/homebrew/bin/ffplay`.

## Audio output

By default, audio goes to the system's default output device. Two settings change that, in `.env` or the environment:

- `AUDIO_DRIVER` chooses the sound system: `pulseaudio`, `pipewire`, `alsa`, `jack`, or `coreaudio`. If unset, the most suitable one is chosen automatically. Docker Compose sets `pulseaudio`.
- `AUDIO_DEVICE` chooses the output device on that sound system.

| Sound system | `AUDIO_DRIVER` | `AUDIO_DEVICE` | List devices with |
|---|---|---|---|
| PulseAudio | `pulseaudio` | Sink name, e.g. `alsa_output.usb-DAC-00.analog-stereo` | `pactl list short sinks` |
| PipeWire | `pulseaudio` (through `pipewire-pulse`) | Sink name, as above | `pactl list short sinks` |
| ALSA | `alsa` | Device, e.g. `plughw:1,0` | `aplay -l` |
| JACK, CoreAudio, native PipeWire | as named | Not supported; uses the system default | |

**Bluetooth speakers.** `DEFAULT_BLUETOOTH_DEVICE_ID` only connects the speaker, before startup and before each track. To make sure audio actually goes to it, also set `AUDIO_DEVICE` to its sink, for example `bluez_output.AA_BB_CC_DD_EE_FF.1` on PipeWire or `bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink` on PulseAudio. The exact name appears in `pactl list short sinks` while the speaker is connected.

**ALSA in Docker.** On hosts without PulseAudio or PipeWire, set these in `.env`:

```bash
AUDIO_DRIVER=alsa
AUDIO_DEVICE=plughw:1,0
```

ALSA needs exclusive access to the sound card, so playback fails with "device busy" if a sound server on the host already uses that card. Compose still mounts `PULSE_RUNTIME_DIR`, so on a host without PulseAudio, Docker creates an empty `/run/user/<UID>/pulse` directory. It's safe to ignore.

Audio errors from ffplay appear in the service log (`docker compose logs yt-player`). `Audio target '<name>' not available` means ffplay was built without that sound system; pick another `AUDIO_DRIVER` or leave it empty.

## Connecting an MCP client

### Option A: stdio (recommended for desktop clients)

Your MCP client launches the server as a local process. The simplest way is [uv](https://docs.astral.sh/uv/), which installs it on first run without a separate setup step.

**Claude Code:**

```bash
claude mcp add yt-player \
  -e MEDIA_SERVICE_URL=http://127.0.0.1:5454 \
  -- uvx --from git+https://github.com/karunstha/yt-player yt-player-mcp
```

**Claude Desktop, Cursor, and other JSON-configured clients:**

```json
{
  "mcpServers": {
    "yt-player": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/karunstha/yt-player", "yt-player-mcp"],
      "env": {
        "MEDIA_SERVICE_URL": "http://127.0.0.1:5454"
      }
    }
  }
}
```

Without uv, install the package and point your client at the `yt-player-mcp` command:

```bash
pipx install git+https://github.com/karunstha/yt-player
```

If your client can run Docker commands, you can use the image you already built instead:

```bash
docker run --rm -i --network host \
  -e MEDIA_SERVICE_URL=http://127.0.0.1:5454 \
  yt-player yt-player mcp
```

### Option B: streamable HTTP (long-running, Dockerized)

Start the MCP server alongside the playback service with the optional Compose profile:

```bash
docker compose --profile mcp up -d
```

The server then listens at:

```text
http://127.0.0.1:5455/mcp
```

For Claude Code:

```bash
claude mcp add --transport http yt-player http://127.0.0.1:5455/mcp
```

Outside Docker, the same server runs with `yt-player mcp --transport streamable-http`.

### Controlling a remote player

The MCP server only makes HTTP calls to the playback service, so it can run on a different machine from the speaker. For example, the playback service can run on a Linux mini PC connected to your speakers while your assistant runs on a laptop. Point `MEDIA_SERVICE_URL` at the player:

```bash
MEDIA_SERVICE_URL=http://media-box.local:5454
```

Read [Security](#security) before you expose either port on a network.

## MCP tools

| Area | Tools |
|---|---|
| Status | `health`, `get_playback_state`, `get_current_track` |
| Search | `search_media` |
| Playback | `play_media`, `pause_playback`, `resume_playback`, `stop_playback`, `seek_playback`, `next_track`, `previous_track`, `set_volume`, `set_shuffle`, `set_repeat` |
| History | `get_playback_history` (paginated with `next_before`) |
| Queue | `get_queue`, `append_queue_track`, `insert_queue_track_next`, `queue_playlist`, `remove_queue_item`, `clear_queue`, `replace_queue_with_playlist` |
| Playlists | `list_playlists`, `get_playlist`, `create_playlist`, `rename_playlist`, `delete_playlist`, `add_track_to_playlist`, `add_tracks_to_playlist`, `add_current_track_to_playlist`, `move_playlist_track`, `remove_track_from_playlist`, `clear_playlist`, `play_playlist` |
| Saving the queue | `save_queue_as_playlist`, `add_queue_to_playlist` |
| Import/export | `export_playlists`, `import_playlists` |

Tools that take a track (`play_media`, `append_queue_track`, `insert_queue_track_next`, `add_track_to_playlist`) accept either a search `query` or a `url`.

Searches and queries take an optional `search_type` that sets what to look for:

| `search_type` | Searches | Example |
|---|---|---|
| `songs` (default) | YouTube Music songs | "play Bohemian Rhapsody" |
| `music_videos` | YouTube Music videos | "play the official video for…" |
| `podcasts` | YouTube Music podcast episodes | "play the Lex Fridman episode with Sam Altman" |
| `videos` | All of YouTube | tutorials, talks, live streams |

Change the default with `DEFAULT_SEARCH_TYPE`. `add_tracks_to_playlist` takes a list where each entry is a URL or a search query.

A playlist holds each track once. Adding a track that is already there fails with a message saying so. Batch adds (`add_tracks_to_playlist`, `save_queue_as_playlist`, `add_queue_to_playlist`) skip duplicates and list them in the result as `skipped`. Tracks that couldn't be found are listed as `failed`. Playlists can be referred to by ID, name, or a slug of the name.

## Troubleshooting

- **The service starts with warnings.** Fix what they name: install the missing tool, or point `FFPLAY_PATH` or `JS_RUNTIME` at it.
- **Playback state shows `error`, or nothing plays.** Check the service log (`docker compose logs yt-player`, or the terminal running `yt-player serve`). Errors from yt-dlp and ffplay appear there. For audio problems, see [Audio output](#audio-output).
- **YouTube says "Sign in to confirm you're not a bot".** Add cookies as `cookies.txt` in the data directory (see [Quick start](#quick-start)).
- **The assistant reports "Could not reach the media service".** The playback service isn't running, or `MEDIA_SERVICE_URL` in your MCP client config points at the wrong host or port.

## Security

Neither the playback service nor the MCP server has authentication.

- Under Docker Compose the playback service binds to `0.0.0.0:5454` with host networking, so **anyone on your network can control playback**. Set `SERVICE_HOST=127.0.0.1` in `.env` if only this machine needs access. Otherwise, run it only on a trusted network, or put it behind a firewall or an authenticating reverse proxy. Outside Docker, `yt-player serve` binds to `127.0.0.1` by default.
- The MCP HTTP transport binds to `127.0.0.1` by default. Keep `MCP_HOST=127.0.0.1` unless you intentionally want other machines to control playback. If you bind it to `0.0.0.0`, protect the port the same way.
- The playback container runs with `privileged: true` and mounts the host's D-Bus and Bluetooth state so it can manage Bluetooth devices. Remove those settings if you don't need Bluetooth.
- `cookies.txt` holds your YouTube session. The `data/` directory and `.env` are listed in `.gitignore`; never commit them.

## Configuration

Both parts read environment variables. `yt-player serve` also loads a `.env` file from the directory it is started in. The MCP server does not read `.env`; set its variables in your MCP client's config, as in the examples above.

Empty values (such as `SERVICE_PORT=`) use the default. Invalid values stop startup with a message naming the setting.

Docker Compose (set in `.env`, see [.env.example](.env.example)):

- `PULSE_RUNTIME_DIR`: host PulseAudio directory mounted into the container. Defaults to `/run/user/1000/pulse`.
- `DATA_PATH`: host directory mounted at `/data`. Defaults to `./data`.
- `SERVICE_HOST`, `SERVICE_PORT`, `AUDIO_DRIVER`, `AUDIO_DEVICE`, `DEFAULT_BLUETOOTH_DEVICE_ID`, `DEFAULT_VOLUME`, `DEFAULT_SEARCH_TYPE`, `MCP_HOST`, `MCP_PORT`: passed through to the containers (see below). Under Compose, `SERVICE_HOST` defaults to `0.0.0.0` and `AUDIO_DRIVER` to `pulseaudio`.

Playback service:

- `SERVICE_HOST`: bind host. Defaults to `127.0.0.1`.
- `SERVICE_PORT`: bind port. Defaults to `5454`.
- `DATA_DIR`: directory for persistent service data. Defaults to `$XDG_DATA_HOME/yt-player` (usually `~/.local/share/yt-player`); the Docker image uses `/data`.
- `DATABASE_PATH`: SQLite database path. Defaults to `$DATA_DIR/media_service.sqlite3`.
- `COOKIES_FILE`: Netscape-format cookies file passed to yt-dlp, used only if it exists. Defaults to `$DATA_DIR/cookies.txt`.
- `AUDIO_DRIVER`: sound system used for playback. Empty picks one automatically. See [Audio output](#audio-output).
- `AUDIO_DEVICE`: output device on that sound system. Empty uses the system default.
- `DEFAULT_BLUETOOTH_DEVICE_ID`: optional Bluetooth device MAC address to connect on startup and before playback.
- `DEFAULT_VOLUME`: startup volume from `0.0` to `1.0`. Defaults to `0.8`.
- `FFPLAY_PATH`: the `ffplay` executable. Defaults to `ffplay` on the PATH.
- `DEFAULT_SEARCH_TYPE`: what searches look for when a request doesn't say: `songs`, `music_videos`, `podcasts`, or `videos`. Defaults to `songs`.
- `JS_RUNTIME`: JavaScript runtime yt-dlp uses for YouTube's challenge: `deno`, `node`, `bun`, or `quickjs`. Add a path as `node:/usr/local/bin/node` if it isn't on the PATH. Give several, comma-separated, to fall back in order of yt-dlp's priority (deno, node, quickjs, bun). Defaults to `deno`. The Docker image ships Deno only.
- `YTDLP_REMOTE_COMPONENTS`: what yt-dlp may download if the bundled challenge solver (`yt-dlp-ejs`) is missing or doesn't match the installed yt-dlp version: `ejs:github`, `ejs:npm`, or `none`. Defaults to `ejs:github`. Normally nothing is downloaded, because the `server` extra installs the matching solver.
- `PLAYBACK_COMPLETION_GRACE_SECONDS`: failed backend exits within this many seconds of track end are treated as completed. Defaults to `5`.
- `PLAYBACK_RECOVERY_RETRIES`: automatic stream restarts per track after a non-clean backend exit. Defaults to `2`.

MCP server:

- `MEDIA_SERVICE_URL`: base URL of the playback service. Defaults to `http://127.0.0.1:5454`.
- `MEDIA_SERVICE_TIMEOUT_SECONDS`: HTTP timeout for calls to the playback service. Defaults to `30`.
- `MCP_TRANSPORT`: `stdio`, `streamable-http`, or `sse`. Defaults to `stdio`.
- `MCP_HOST`: bind host for HTTP transports. Defaults to `127.0.0.1`.
- `MCP_PORT`: bind port for HTTP transports. Defaults to `5455`.
- `MCP_STREAMABLE_HTTP_PATH`: streamable HTTP path. Defaults to `/mcp`.
- `MCP_SSE_PATH`: SSE event path. Defaults to `/sse`.
- `MCP_MESSAGE_PATH`: SSE message path. Defaults to `/messages/`.

Docker Compose bind-mounts `DATA_PATH` (default `./data`) at `/data`, so playlists, saved track metadata, playback history, and cookies survive container replacement.

## HTTP API

The MCP server is one client of this API. You can also call it directly, or build your own UI on top of it.

Playback:

- `POST /player/play` with one of `url`, `query`, or `track`
- `GET /player/state`
- `GET /player/current`
- `POST /player/pause`
- `POST /player/resume`
- `POST /player/stop`
- `POST /player/seek`
- `POST /player/next`
- `POST /player/previous`
- `POST /player/volume`
- `POST /player/shuffle`
- `POST /player/repeat`
- `GET /player/history?days=7&before=<iso-date-time>&limit=100`

Search:

- `GET /search?q=...&count=10&type=songs`: `type` is `songs`, `music_videos`, `podcasts`, or `videos`
- Requests that take a `query` (`/player/play`, `/queue`, `/queue/tracks`, `/playlists/{id}/tracks`, and `bulk` items) also accept `search_type`

Queue:

- `GET /queue`
- `POST /queue`
- `DELETE /queue`
- `POST /queue/tracks`
- `POST /queue/tracks/next`
- `POST /queue/playlists/{id}` with optional `play_next` and `shuffle`: adds a playlist's tracks without replacing the queue
- `DELETE /queue/items/{item_id}`

Playlists:

- `POST /playlists`
- `GET /playlists`
- `GET /playlists/{id}`
- `PATCH /playlists/{id}`
- `DELETE /playlists/{id}`
- `POST /playlists/{id}/tracks`
- `POST /playlists/{id}/tracks/bulk` with `items` (up to 50 tracks): returns `added`, `skipped`, and `failed`
- `POST /playlists/{id}/tracks/queue`: appends the current queue, returns the same shape as `bulk`
- `POST /playlists/{id}/tracks/current`
- `DELETE /playlists/{id}/tracks/{track_id}`
- `POST /playlists/{id}/tracks/{track_id}/move` with a 0-based `position`
- `POST /playlists/{id}/tracks/reorder`
- `DELETE /playlists/{id}/tracks`
- `POST /playlists/{id}/play`
- `POST /playlists/{id}/shuffle`
- `GET /playlists/export`
- `GET /playlists/{id}/export`
- `POST /playlists/import`

Realtime events:

- `WS /events`

The WebSocket sends `playback.sync` on connect and then broadcasts meaningful events such as `track.changed`, `playback.state_changed`, `playback.seeked`, `queue.changed`, `playlist.changed`, and `track.completed`. While playing, it emits low-frequency `playback.sync` updates approximately every 3 seconds.

### Legacy API

The original endpoints are still available:

- `POST /play`
- `POST /stop`
- `GET /status`
- `POST /search`
- `POST /play-search`
- `POST /shuffle`

## Project layout

- `yt_player/cli.py`: the `yt-player` command and its `serve` and `mcp` modes.
- `yt_player/factory.py`: FastAPI app creation, lifespan handling, service wiring, and error handlers.
- `yt_player/env.py`: environment variable parsing shared by the service and the MCP server.
- `yt_player/core/`: service configuration, yt-dlp options (`ytdlp.py`), structured service errors, and shared utilities.
- `yt_player/player/`: playback routes, queue routes, legacy playback routes, playback controller, queue service, ffplay/yt-dlp backend, and player/queue models.
- `yt_player/playlists/`: playlist routes, persistent playlist service, SQLite repository, and playlist models.
- `yt_player/sources/`: media source integrations. YouTube is currently implemented in `yt_player/sources/youtube.py`.
- `yt_player/events/`: WebSocket route, event broadcaster, and event models.
- `yt_player/mcp/`: MCP server and HTTP client wrapper for the media service API.
- `main.py`: ASGI entrypoint for running the app directly with Uvicorn (`uvicorn main:app`).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## License

[MIT](LICENSE)
