import os
import re
import signal
import subprocess
import sys
import threading
import time
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


# ffplay's -stats line, e.g. "  12.34 M-A:  0.000 fd=   0 aq=   9KB ...". The
# first number is the master (audio) clock: what is actually being played.
_STATS_CLOCK = re.compile(rb"^\s*(-?\d+(?:\.\d+)?)\s+M-A:")


def parse_stats_clock(line: bytes) -> float | None:
    match = _STATS_CLOCK.match(line)
    return float(match.group(1)) if match else None


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
        self._clock_lock = threading.Lock()
        self._clock: tuple[float, float] | None = None  # (stream seconds, monotonic time read)
        self._clock_offset = 0.0
        self._stream_start = 0.0

    def audio_clock(self) -> tuple[float, float] | None:
        """Track position ffplay is actually playing, and how many seconds ago it said so.

        None until audio has started, so callers can tell startup apart from playback.
        """
        with self._clock_lock:
            if self._clock is None:
                return None
            seconds, read_at = self._clock
            return seconds + self._clock_offset, time.monotonic() - read_at

    def _read_player_stderr(self, process: subprocess.Popen) -> None:
        stream = getattr(process, "stderr", None)
        if stream is None:
            return
        buffer = b""
        first = True
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = re.split(rb"[\r\n]", buffer)
            for line in lines:
                clock = parse_stats_clock(line.replace(b"\x1b[2K", b""))
                if clock is None:
                    text = line.replace(b"\x1b[2K", b"").strip()
                    # Keep ffplay's own error output visible in the service log.
                    if text and b"M-A:" not in text and not text.startswith(b"nan"):
                        sys.stderr.write(text.decode(errors="replace") + "\n")
                    continue
                with self._clock_lock:
                    if process is not self.player_process:
                        return
                    if first:
                        # A seek makes yt-dlp cut a section, and its timestamps
                        # usually restart at 0; otherwise they are absolute.
                        start = self._stream_start
                        self._clock_offset = start if start > 0 and clock < start - 1 else 0.0
                        first = False
                    self._clock = (clock, time.monotonic())

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
        with self._clock_lock:
            self._clock = None
            self._clock_offset = 0.0
            self._stream_start = position_seconds
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
            # Google throttles a long sequential HTTP transfer from this host.
            # Native yt-dlp chunk requests use byte ranges and keep the audio
            # stream ahead of real time instead of starving ffplay.
            "--http-chunk-size",
            "10M",
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
                # Report the real audio clock so position follows what is heard,
                # not when the process was launched.
                "-stats",
                "-volume",
                str(int(volume * 100)),
                "-",
            ],
            stdin=self.yt_process.stdout,
            stderr=subprocess.PIPE,
            env=player_env(),
            preexec_fn=os.setsid,
        )
        if self.yt_process.stdout is not None:
            self.yt_process.stdout.close()
        threading.Thread(
            target=self._read_player_stderr,
            args=(self.player_process,),
            name="ffplay-stats",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._terminate_process(self.yt_process)
        self._terminate_process(self.player_process)
        self.yt_process = None
        self.player_process = None
        with self._clock_lock:
            self._clock = None

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
