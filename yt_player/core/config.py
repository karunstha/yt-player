import os

from dotenv import find_dotenv, load_dotenv

from yt_player.env import env_float, env_int, env_str

# Look for .env from the working directory, not from where the package is installed.
load_dotenv(find_dotenv(usecwd=True))


def default_data_dir() -> str:
    data_home = env_str("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data_home, "yt-player")


SERVICE_HOST = env_str("SERVICE_HOST", "127.0.0.1")
SERVICE_PORT = env_int("SERVICE_PORT", 5454, minimum=1, maximum=65535)
DATA_DIR = os.path.expanduser(env_str("DATA_DIR") or default_data_dir())
DATABASE_PATH = os.path.expanduser(
    env_str("DATABASE_PATH") or os.path.join(DATA_DIR, "media_service.sqlite3")
)
COOKIES = os.path.expanduser(env_str("COOKIES_FILE") or os.path.join(DATA_DIR, "cookies.txt"))
FFPLAY_PATH = os.path.expanduser(env_str("FFPLAY_PATH", "ffplay"))
JS_RUNTIME = env_str("JS_RUNTIME", "deno")
DEFAULT_SEARCH_TYPE = env_str("DEFAULT_SEARCH_TYPE", "songs").lower()
# Unlike the other settings, an explicitly empty value means "allow none".
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github")
AUDIO_DRIVER = env_str("AUDIO_DRIVER")
AUDIO_DEVICE = env_str("AUDIO_DEVICE")
DEFAULT_BLUETOOTH_DEVICE_ID = env_str("DEFAULT_BLUETOOTH_DEVICE_ID")
DEFAULT_VOLUME = env_float("DEFAULT_VOLUME", 0.8, minimum=0.0, maximum=1.0)
PLAYBACK_COMPLETION_GRACE_SECONDS = env_float("PLAYBACK_COMPLETION_GRACE_SECONDS", 5.0, minimum=0.0)
PLAYBACK_RECOVERY_RETRIES = env_int("PLAYBACK_RECOVERY_RETRIES", 2, minimum=0)
