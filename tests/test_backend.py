import subprocess
import sys

from yt_player.player import backend
from yt_player.player.backend import FfplayBackend, player_env


def test_player_env_leaves_audio_selection_to_sdl_by_default(monkeypatch):
    monkeypatch.setattr(backend, "AUDIO_DRIVER", None)
    monkeypatch.setattr(backend, "AUDIO_DEVICE", None)
    monkeypatch.delenv("SDL_AUDIODRIVER", raising=False)
    monkeypatch.delenv("PULSE_SINK", raising=False)
    monkeypatch.delenv("AUDIODEV", raising=False)

    env = player_env()

    assert "SDL_AUDIODRIVER" not in env
    assert "PULSE_SINK" not in env
    assert "AUDIODEV" not in env


def test_player_env_applies_audio_driver_and_device(monkeypatch):
    monkeypatch.setattr(backend, "AUDIO_DRIVER", "alsa")
    monkeypatch.setattr(backend, "AUDIO_DEVICE", "hw:1,0")

    env = player_env()

    assert env["SDL_AUDIODRIVER"] == "alsa"
    assert env["PULSE_SINK"] == "hw:1,0"
    assert env["AUDIODEV"] == "hw:1,0"


def test_bluetooth_connect_without_bluetoothctl_does_not_raise(monkeypatch):
    def missing_bluetoothctl(*args, **kwargs):
        raise FileNotFoundError("bluetoothctl")

    monkeypatch.setattr(subprocess, "run", missing_bluetoothctl)

    FfplayBackend().try_connect_bluetooth("AA:BB:CC:DD:EE:FF")


def test_yt_dlp_runs_with_current_interpreter_and_ffplay_path(monkeypatch):
    launched = []

    class FakeProcess:
        stdout = None

        def __init__(self, command, **kwargs):
            launched.append(command)

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(backend, "FFPLAY_PATH", "/opt/ffmpeg/bin/ffplay")
    monkeypatch.setattr(backend, "DEFAULT_BLUETOOTH_DEVICE_ID", None)

    FfplayBackend().start_url("https://www.youtube.com/watch?v=abc")

    ytdlp_command, ffplay_command = launched
    assert ytdlp_command[:3] == [sys.executable, "-m", "yt_dlp"]
    assert "--http-chunk-size" in ytdlp_command
    assert ytdlp_command[ytdlp_command.index("--http-chunk-size") + 1] == "10M"
    assert "--downloader" not in ytdlp_command
    assert ffplay_command[0] == "/opt/ffmpeg/bin/ffplay"


def test_parse_stats_clock_reads_ffplay_master_clock():
    assert backend.parse_stats_clock(b"   12.34 M-A:  0.000 fd=   0 aq=    9KB") == 12.34
    assert backend.parse_stats_clock(b"  -0.01 M-A:  0.000 fd=   0") == -0.01
    assert backend.parse_stats_clock(b"    nan M-A:    nan fd=   0") is None
    assert backend.parse_stats_clock(b"Invalid data found when processing input") is None


def _feed_stderr(player, data, *, stream_start=0.0):
    import io

    class Process:
        stderr = io.BufferedReader(io.BytesIO(data))

    process = Process()
    player.player_process = process
    player._stream_start = stream_start
    player._read_player_stderr(process)


def test_audio_clock_is_none_until_audio_starts():
    player = FfplayBackend()
    _feed_stderr(player, b"    nan    :  0.000 fd=   0\r")
    assert player.audio_clock() is None


def test_audio_clock_follows_latest_stats_line():
    player = FfplayBackend()
    _feed_stderr(player, b"    nan    :  0.000\r\x1b[2K   0.02 M-A:  0.000\r   4.50 M-A:  0.000\r")
    seconds, age = player.audio_clock()
    assert seconds == 4.5
    assert 0 <= age < 1


def test_audio_clock_adds_seek_start_when_stream_restarts_at_zero():
    player = FfplayBackend()
    _feed_stderr(player, b"   0.10 M-A: 0\r   2.00 M-A: 0\r", stream_start=60)
    assert player.audio_clock()[0] == 62.0


def test_audio_clock_keeps_absolute_timestamps_after_seek():
    player = FfplayBackend()
    _feed_stderr(player, b"  60.10 M-A: 0\r  62.00 M-A: 0\r", stream_start=60)
    assert player.audio_clock()[0] == 62.0


def test_ffplay_errors_still_reach_the_log(capsys):
    player = FfplayBackend()
    _feed_stderr(player, b"pipe:: Invalid data found when processing input\n")
    assert "Invalid data found" in capsys.readouterr().err
