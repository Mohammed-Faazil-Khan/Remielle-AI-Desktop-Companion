"""Who may talk to Remielle, and the one secret a device needs to do it.

Her page can make the PC open programs and run commands, so anything that is not this computer itself has to
prove it belongs to you. The proof is a single token kept in .env, printed by the launcher inside the phone URL
so you never type it: ?token=... sets a cookie, and from then on the page, its files and its websocket all
carry that cookie.

Nothing here touches FastAPI, so the rules can be tested on their own.
"""
import hmac
import ipaddress
import os
import secrets
import socket
from pathlib import Path

from remielle_config import PROJECT_DIR

TOKEN_NAME = "REMIELLE_ACCESS_TOKEN"
TOKEN_FILE = Path(PROJECT_DIR) / ".env"
COOKIE = "remielle_token"
_MAX_TOKEN_LEN = 200


def is_local(client_ip: str) -> bool:
    """True for requests coming from this very computer, which are always allowed."""
    return (client_ip or "").strip() in ("127.0.0.1", "::1", "localhost")


def generate_token() -> str:
    return secrets.token_urlsafe(16)


def _read_from_env_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() != TOKEN_NAME:
            continue
        value = value.strip().strip("\"'")
        if value:
            return value
    return ""


def _append_to_env_file(path: Path, token: str) -> bool:
    """Adds the token to .env. False when the file cannot be written (she still works, just re-rolls every run)."""
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{TOKEN_NAME}={token}\n")
        return True
    except OSError:
        return False


def ensure_token(environ=None, path: Path = TOKEN_FILE) -> str:
    """The token she guards every non-local request with - made once and remembered in .env."""
    environ = os.environ if environ is None else environ
    present = (environ.get(TOKEN_NAME) or "").strip()
    if present:
        return present
    found = _read_from_env_file(path)
    if found:
        environ[TOKEN_NAME] = found
        return found
    token = generate_token()
    environ[TOKEN_NAME] = token
    _append_to_env_file(path, token)
    return token


def token_matches(presented: str, expected: str) -> bool:
    """Constant-time compare of the value exactly as it arrived. An empty expectation never matches anything."""
    if not expected:
        return False
    got = presented or ""
    return bool(got) and hmac.compare_digest(got[:_MAX_TOKEN_LEN], expected[:_MAX_TOKEN_LEN])


def _new_probe():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def local_ip() -> str:
    """The address your phone can actually reach, picked by which interface would route to the internet."""
    try:
        probe = _new_probe()
    except OSError:
        return "127.0.0.1"
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def is_routable(ip: str) -> bool:
    """True only for a home-network address, so a VPN card or a public interface is not offered to your phone."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(address.version == 4 and address.is_private
                and not address.is_loopback and not address.is_link_local)


def phone_url(token: str, ip: str, port: int, https: bool) -> str:
    scheme = "https" if https else "http"
    return f"{scheme}://{ip}:{port}/?token={token}"
