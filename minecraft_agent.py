"""Remielle's link to the Minecraft bot (the Node program in ./minecraft_bot).

The bot runs separately and exposes a tiny HTTP API on 127.0.0.1. Everything here is
"never raise": if the bot is off, the world is closed or the API is slow, the caller gets a short,
speakable sentence instead of an exception, so a Minecraft problem can never break the assistant.
Only the standard library is used.
"""
import json
import time
import urllib.error
import urllib.request

from remielle_config import env_float, env_int

API_HOST = "127.0.0.1"
API_PORT = env_int("MC_API_PORT", 3100)
BASE = f"http://{API_HOST}:{API_PORT}"

_CONNECTED_TTL = 2.0
_cache = {"at": 0.0, "connected": False, "mode": "idle"}
_last_event_id = {"value": None}


def _request(path: str, payload=None, timeout: float = 3.0):
    """Returns the parsed JSON reply, or None when the bot can't be reached."""
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return None
    except Exception:
        return None


def bot_running() -> bool:
    """Is the bot program itself up (even if it isn't in the world right now)?"""
    h = _request("/health", timeout=1.0)
    return bool(h and h.get("ok"))


def is_connected(max_age: float = _CONNECTED_TTL) -> bool:
    """Is she standing in the Minecraft world right now? Cached for a moment so chatting stays fast."""
    now = time.monotonic()
    if now - _cache["at"] < max_age:
        return bool(_cache["connected"])
    h = _request("/health", timeout=1.0)
    _cache["at"] = now
    _cache["connected"] = bool(h and h.get("ok") and h.get("connected"))
    return _cache["connected"]


def is_busy() -> bool:
    """True while she is doing something in the game (so a bare 'stop' should mean the game)."""
    if not is_connected():
        return False
    st = _request("/status", timeout=1.0)
    return bool(st and st.get("connected") and (st.get("task") or st.get("mode") not in (None, "idle")))


def send_command(command: str) -> str:
    """Runs one game command (see minecraft_bot/src/parser.js). Always returns a sentence."""
    command = (command or "").strip()
    if not command:
        return "Error: no Minecraft command was given."
    reply = _request("/command", {"command": command}, timeout=8.0)
    _cache["at"] = 0.0
    if reply is None:
        return ("Error: the Minecraft bot isn't running. Start it with Launch Minecraft Companion, "
                "then join the world.")
    message = str(reply.get("message") or "").strip()
    if reply.get("ok"):
        return message or "Done."
    return "Error: " + (message or "that didn't work.")


ACT_TIMEOUT = env_float("MC_ACT_TIMEOUT", 90.0)


def observe() -> dict:
    """What she can see, hold and reach right now. Never raises: a broken bot is just 'not present'."""
    reply = _request("/observe", timeout=3.0)
    if not isinstance(reply, dict):
        return {"ok": False, "present": False, "message": "the Minecraft bot is not answering"}
    return reply


def act(action: str, args: dict | None = None) -> dict:
    """Performs one primitive (mount, dig, place, craft, ...). Always returns a dict with ok/message."""
    action = (action or "").strip().lower()
    if not action:
        return {"ok": False, "message": "Error: no action was given."}
    reply = _request("/act", {"action": action, "args": args or {}}, timeout=ACT_TIMEOUT)
    _cache["at"] = 0.0
    if not isinstance(reply, dict):
        return {"ok": False, "did": action,
                "message": "Error: the Minecraft bot isn't running. Start it with Launch Minecraft Companion."}
    return reply


def action_help() -> str:
    """The bot's own list of verbs, so the planner never has to keep a duplicate of it in Python."""
    reply = _request("/actions", timeout=2.0)
    if not isinstance(reply, dict):
        return ""
    return str(reply.get("help") or "")


def say(text: str) -> bool:
    """Writes a line in the in-game chat as her."""
    text = (text or "").strip()
    if not text:
        return False
    reply = _request("/say", {"text": text[:400]}, timeout=3.0)
    return bool(reply and reply.get("ok"))


def note(text: str, mood: str = "relaxed", priority: int = 1) -> bool:
    """Queues a line for her to say out loud (nothing is written in the game chat). Used for long thinking jobs."""
    text = (text or "").strip()
    if not text:
        return False
    reply = _request("/note", {"text": text[:300], "mood": mood, "priority": priority}, timeout=2.0)
    return bool(reply and reply.get("ok"))


def status_summary() -> str:
    """One short line for the brain's context ('' when she isn't in the world)."""
    if not is_connected():
        return ""
    st = _request("/status", timeout=1.5)
    if not st or not st.get("connected"):
        return ""
    bits = [f"health {st.get('health')}/20", f"food {st.get('food')}/20"]
    if st.get("mode") and st["mode"] != "idle":
        bits.append(f"mode {st['mode']}")
    task = st.get("task")
    if isinstance(task, dict) and task.get("label"):
        bits.append(f"busy: {task['label']}")
    hostiles = st.get("hostiles") or []
    if hostiles:
        bits.append("monsters near: " + ", ".join(str(h.get("name")) for h in hostiles[:3]))
    return "In Minecraft (" + ", ".join(bits) + ")"


def poll_events() -> list:
    """New in-game events since the last call (a list of dicts). The first call only sets the marker."""
    since = _last_event_id["value"]
    reply = _request(f"/events?since={since if since is not None else 0}", timeout=1.5)
    if not reply or "events" not in reply:
        return []
    last = reply.get("last")
    first_call = since is None
    if isinstance(last, int):
        _last_event_id["value"] = last
    elif first_call:
        _last_event_id["value"] = 0
    if first_call:
        # don't replay old history as if it just happened
        return []
    return list(reply.get("events") or [])


def reset_event_marker() -> None:
    _last_event_id["value"] = None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(send_command(" ".join(sys.argv[1:])))
    else:
        print("bot running:", bot_running(), "| in world:", is_connected())
        print(status_summary() or "(not in the world)")
