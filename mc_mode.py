"""The one switch that puts Remielle into Minecraft and lets her play by herself.

Turning it on brings the server and her bot up if they are not already running, waits until she is
standing in the world, switches her self-chosen goals on, and answers with the sentence she should
say. Turning it off only stops those goals: a running world is left alone, because leaving one you
are standing in is a person's decision, not a side effect of a word.

Like minecraft_agent.py, nothing here raises - a Minecraft problem comes back as a sentence.
"""
import os
import re
import subprocess
import time

import minecraft_agent
from remielle_config import PROJECT_DIR, env_float

LAUNCHER = PROJECT_DIR / "Launch_Minecraft.bat"
READY_SECONDS = env_float("REMIELLE_MC_READY_SECONDS", 150.0)
FIRST_LOOK_SECONDS = env_float("REMIELLE_MC_FIRST_LOOK_SECONDS", 8.0)
POLL_SECONDS = env_float("REMIELLE_MC_POLL_SECONDS", 1.0)

_DIMENSIONS = {0: "the overworld", -1: "the Nether", 1: "the End"}
_NO = re.compile(r"\b(?:no|not|never|cannot|don'?t|doesn'?t|isn'?t|aren'?t|won'?t|can'?t|shouldn'?t)\b", re.IGNORECASE)
_QUESTION = re.compile(r"^(?:is|are|does|do|did|what|which|how|when|who)\b|\?", re.IGNORECASE)
_OFF_WORDS = re.compile(r"\b(?:off|stop|leave|quit|exit|sleep|stand\s+down|pause|rest)\b", re.IGNORECASE)
_ON_WORDS = re.compile(r"\b(?:on|start|join|enter|play|come|enable|go|begin|arrive|wake)\b", re.IGNORECASE)

# What she believes about her own switch, so 'status' answers without waking the game.
_mode = {"on": False}


def is_on() -> bool:
    return bool(_mode["on"])


def _start_game() -> bool:
    """Runs Launch_Minecraft.bat in background mode: server window, then her bot."""
    if not LAUNCHER.exists():
        return False
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(['cmd', '/c', str(LAUNCHER), '/auto'], cwd=str(PROJECT_DIR), creationflags=flags)
        return True
    except OSError:
        return False


def _in_world() -> bool:
    return minecraft_agent.is_connected(max_age=0)


def _where(snap=None) -> str:
    """A short spoken 'I'm here': dimension and rounded coordinates, or '' when she is not in the world."""
    snapshot = snap if snap is not None else minecraft_agent.observe()
    me = (snapshot or {}).get("me") or {}
    at = me.get("at") or {}
    if not at:
        return ""
    dimension = _DIMENSIONS.get((snapshot.get("world") or {}).get("dimension"), "an unknown place")
    try:
        return f"{dimension}, at {round(float(at.get('x')), 1)} {round(float(at.get('y')), 1)} {round(float(at.get('z')), 1)}"
    except (TypeError, ValueError):
        return dimension


def _initiative(word: str) -> str:
    try:
        from minecraft_planner import set_mode
        return set_mode(word)
    except Exception as e:   # a broken planner must not break the switch
        return f"Failed: her own goals did not change ({e})"


def _wait_for_world(timeout: float, *, up=None, in_world=None, sleep=None) -> bool:
    up = up or minecraft_agent.bot_running
    in_world = in_world or _in_world
    sleep = sleep or time.sleep
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if up() and in_world():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(POLL_SECONDS)


def switch(on: bool, *, timeout: float = READY_SECONDS, start=None, wait=None,
           place=None, initiative=None) -> str:
    """Flips Minecraft mode and returns one sentence for her to say. Blocks until she is in the world."""
    start = start or _start_game
    wait = wait or _wait_for_world
    place = place or _where
    initiative = initiative or _initiative
    on = bool(on)

    if not on:
        _mode["on"] = False
        initiative("off")
        return "Minecraft mode is off - I'll keep my own goals to myself now. The game stays open if you want me."

    if not wait(FIRST_LOOK_SECONDS):
        # A second or two of "is it already up?", because a cold start has to be started before it can be waited for.
        if not start():
            _mode["on"] = False
            return ("I couldn't start the game - Launch_Minecraft.bat is missing, so Minecraft mode "
                    "has to be set up first.")
        if not wait(timeout):
            _mode["on"] = False
            return ("The game didn't come up in time. Look in the Minecraft windows for the reason, "
                    "then ask me again.")

    _mode["on"] = True
    initiative("on")
    where = place()
    hello = "I'm in the game and I'll think for myself while you're busy."
    return f"{hello} I'm in {where}." if where else hello


def describe() -> str:
    """One line about the switch and where she stands, for the brain's context and for 'status'."""
    if not is_on():
        return "Minecraft mode is off."
    where = _where()
    return f"Minecraft mode is on, and I'm in {where}." if where else "Minecraft mode is on, but I'm not standing in the world."


def set_mode(word: str = "status") -> str:
    """Accepts the words a person actually says and returns the sentence she answers with."""
    raw = (word or "").strip().lower()
    if _says_no(raw):
        # "do not turn it on" contains both 'not' and 'on'; a sentence she is unsure about changes nothing.
        return f"Failed: '{word}' is not a Minecraft mode I understand. Say on, off, or status."
    if _QUESTION.search(raw) or any(k in raw for k in ("status", "state", "whether")):
        return describe()
    if _OFF_WORDS.search(raw):
        return switch(False)
    if _ON_WORDS.search(raw):
        return switch(True)
    return f"Failed: '{word}' is not a Minecraft mode. Say on, off, or status."


def _says_no(raw: str) -> bool:
    """'do not turn it on' must never be read as 'turn it on'."""
    return bool(_NO.search(raw or ""))
