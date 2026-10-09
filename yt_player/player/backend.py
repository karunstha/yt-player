import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from yt_player.core.config import (
    AUDIO_DEVICE,
    AUDIO_DRIVER,
    DEFAULT_BLUETOOTH_DEVICE_ID,
    DEFAULT_VOLUME,
    FFPLAY_PATH,
)
from yt_player.core.ytdlp import ytdlp_cli_args


@dataclass(frozen=True)
class BackendExitStatus:
    yt_returncode: int | None
    player_returncode: int | None

    @property
    def clean(self) -> bool:
        return self.yt_returncode == 0 and self.player_returncode == 0

    @property
    def failed(self) -> bool:
        return not self.clean

    @property
    def message(self) -> str | None:
        if self.clean:
            return None
        parts = []
        if self.yt_returncode not in (None, 0):
            parts.append(f"yt-dlp exited with status {self.yt_returncode}")
        if self.player_returncode not in (None, 0):
            parts.append(f"ffplay exited with status {self.player_returncode}")
        return "; ".join(parts) or "Playback backend exited unexpectedly"


def player_env() -> dict[str, str]:
    """Environment for ffplay, which picks its audio output through SDL."""
    env = os.environ.copy()
    if AUDIO_DRIVER:
        env["SDL_AUDIODRIVER"] = AUDIO_DRIVER
    if AUDIO_DEVICE:
        # Each SDL audio driver reads its own variable, so set both:
        # PULSE_SINK for PulseAudio/pipewire-pulse, AUDIODEV for ALSA.
        env["PULSE_SINK"] = AUDIO_DEVICE
        env["AUDIODEV"] = AUDIO_DEVICE
    return env


class FfplayBackend:
    def __init__(self) -> None:
        self.yt_process: subprocess.Popen | None = None
        self.player_process: subprocess.Popen | None = None
        self.current_url: str | None = None

    def try_connect_bluetooth(self, device_id: str) -> None:
        cmd = """
        power on
        agent on
        default-agent
        connect {device_id}
        quit
        """.format(device_id=device_id)

        try:
            result = subprocess.run(
                ["bluetoothctl"],
                input=cmd,
                text=True,
                capture_output=True,
                timeout=10,
            )
        except FileNotFoundError:
            print("Bluetooth connect skipped: bluetoothctl is not installed")
            return
        except subprocess.TimeoutExpired:
            print(f"Bluetooth connect to {device_id} timed out")
            return

        if "Connection successful" in result.stdout:
            print("Connected")
        else:
            print("Failed")
            print(result.stdout)
            print(result.stderr)

    def start_url(
        self,
        url: str,
        *,
        position_seconds: float = 0,
        volume: float = DEFAULT_VOLUME,
        bluetooth_device_id: str | None = None,
    ) -> None:
        self.stop()

        device_id = bluetooth_device_id or DEFAULT_BLUETOOTH_DEVICE_ID
        if device_id:
            self.try_connect_bluetooth(device_id)

        self.current_url = url
        # Run yt-dlp with this interpreter so it is found even when the
        # environment's bin directory is not on PATH (pipx, uvx, services).
        ytdlp_command = [
            sys.executable,
            "-m",
            "yt_dlp",
            *ytdlp_cli_args(),
            "-f",
            "bestaudio",
            "--no-part",
            "--no-mtime",
            "--downloader",
            "ffmpeg",
            "--downloader-args",
            (
                "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 "
                "-reconnect_on_network_error 1 -reconnect_delay_max 5 "
                "-reconnect_delay_total_max 30 -rw_timeout 15000000"
            ),
            "--socket-timeout",
            "15",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "-o",
            "-",
        ]
        if position_seconds > 0:
            ytdlp_command.extend(["--download-sections", f"*{position_seconds}-inf"])
        ytdlp_command.append(url)

        self.yt_process = subprocess.Popen(
            ytdlp_command,
            stdout=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        self.player_process = subprocess.Popen(
            [
                FFPLAY_PATH,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                "-volume",
                str(int(volume * 100)),
                "-",
            ],
            stdin=self.yt_process.stdout,
            env=player_env(),
            preexec_fn=os.setsid,
        )
        if self.yt_process.stdout is not None:
            self.yt_process.stdout.close()

    def stop(self) -> None:
        self._terminate_process(self.yt_process)
        self._terminate_process(self.player_process)
        self.yt_process = None
        self.player_process = None

    def pause(self) -> None:
        self._signal_process(self.yt_process, signal.SIGSTOP)
        self._signal_process(self.player_process, signal.SIGSTOP)

    def resume(self) -> None:
        self._signal_process(self.yt_process, signal.SIGCONT)
        self._signal_process(self.player_process, signal.SIGCONT)

    def set_volume(self, volume: float) -> bool:
        sink_input_id = self._pulse_sink_input_id()
        if sink_input_id is None:
            return False

        try:
            result = subprocess.run(
                [
                    "pactl",
                    "set-sink-input-volume",
                    sink_input_id,
                    f"{int(volume * 100)}%",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def is_running(self) -> bool:
        return self.player_process is not None and self.player_process.poll() is None

    def exit_status(self) -> BackendExitStatus | None:
        if self.player_process is None:
            return None

        player_returncode = self.player_process.poll()
        if player_returncode is None:
            return None

        yt_returncode = self.yt_process.poll() if self.yt_process is not None else None
        if yt_returncode is None and self.yt_process is not None:
            self._terminate_process(self.yt_process)
            yt_returncode = self.yt_process.poll()

        return BackendExitStatus(
            yt_returncode=yt_returncode,
            player_returncode=player_returncode,
        )

    def _signal_process(self, process: subprocess.Popen | None, sig: signal.Signals) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except ProcessLookupError:
            pass

    def _terminate_process(self, process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    def _pulse_sink_input_id(self) -> str | None:
        if self.player_process is None or self.player_process.poll() is not None:
            return None

        try:
            result = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None

        expected_pid = f'application.process.id = "{self.player_process.pid}"'
        current_id: str | None = None
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if line.startswith("Sink Input #"):
                current_id = line.rsplit("#", 1)[-1]
            elif current_id is not None and line == expected_pid:
                return current_id
        return None
