"""Shared settings for Remielle.

Importing this module loads a local ".env" file (next to this file) into the environment, so API keys
and Spotify credentials never have to live inside source files. Real environment variables (for example
the ones in a PyCharm run configuration) always win over .env.
"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    path = PROJECT_DIR / ".env"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


_load_env_file()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ----------------------------------------------------------------------------- Where she listens
# She binds every interface by default, because your phone has to reach her. Nothing that is not this
# computer itself gets through without the token in device_access.py, so this is not an open door.
APP_HOST = (os.environ.get("REMIELLE_HOST") or "0.0.0.0").strip() or "0.0.0.0"
APP_PORT = env_int("REMIELLE_PORT", 8000)

CERT_DIR = PROJECT_DIR / "certs"


def cert_paths() -> tuple:
    """(keyfile, certfile) when Setup_App.bat has made the local certificate, else (None, None)."""
    key, certificate = CERT_DIR / "remielle-key.pem", CERT_DIR / "remielle.pem"
    if key.is_file() and certificate.is_file():
        return str(key), str(certificate)
    return None, None


def https_is_on() -> bool:
    """REMIELLE_HTTPS=0 forces plain http; otherwise https as soon as the certificate exists."""
    raw = (os.environ.get("REMIELLE_HTTPS") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return cert_paths()[0] is not None


# ----------------------------------------------------------------------------- Spotify
SPOTIFY_SCOPES = "user-modify-playback-state user-read-playback-state user-read-private"
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
# One fixed location, so spotify_login.py and the assistant always use the same saved login,
# no matter which folder PyCharm or the terminal happens to run from.
SPOTIFY_CACHE_PATH = str(PROJECT_DIR / ".spotify_cache")


def spotify_credentials_missing() -> list:
    """Names of the Spotify environment variables that are not set."""
    return [k for k in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET") if not os.environ.get(k, "").strip()]


def spotify_oauth(open_browser: bool = False):
    """Builds the SpotifyOAuth object used by both spotify_login.py and system_tools.py."""
    from spotipy.oauth2 import SpotifyOAuth

    return SpotifyOAuth(
        scope=SPOTIFY_SCOPES,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        cache_path=SPOTIFY_CACHE_PATH,
        open_browser=open_browser,
    )
