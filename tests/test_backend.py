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
    assert ffplay_command[0] == "/opt/ffmpeg/bin/ffplay"
