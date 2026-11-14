import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from remielle_config import spotify_oauth, spotify_credentials_missing


def _optional(module_name: str):
    """Imports a package, or returns None (with a console note) so one broken package can't stop the whole assistant."""
    try:
        return __import__(module_name)
    except Exception as e:
        print(f"⚠️ Optional package '{module_name}' is unavailable: {e}")
        return None


pyautogui = _optional("pyautogui")
cv2 = _optional("cv2")
wikipedia = _optional("wikipedia")
spotipy = _optional("spotipy")

if wikipedia is not None:
    try:
        wikipedia.set_user_agent("AnimeAssistant/1.0 (local-dev)")
    except Exception:
        pass

try:
    from spotipy.exceptions import SpotifyException
except Exception:  # spotipy missing: keep the name defined so the except clauses below still work
    class SpotifyException(Exception):
        http_status = None
        msg = ""


def _desktop_dir() -> Path:
    """The real Desktop folder (OneDrive can move it), created if it doesn't exist."""
    home = Path.home()
    for candidate in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if candidate.is_dir():
            return candidate
    fallback = home / "Desktop"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except OSError:
        return home


def create_project_file(filename: str, content: str) -> str:
    """Writes code or text to a file in the workspace."""
    try:
        target_path = Path(filename)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully created {filename}"
    except Exception as e:
        return f"Failed to create file: {e}"


WEB_SHORTCUTS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "spotify web": "https://open.spotify.com",
}

APP_ALIASES = {
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "chrome": "chrome.exe",
    "browser": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "vscode": "code.cmd",
    "vs code": "code.cmd",
    "visual studio code": "code.cmd",
    "code": "code.cmd",
    "spotify": "spotify.exe",
    "pycharm": "pycharm64.exe",
}

APP_PROTOCOLS = {
    "roblox": "roblox://",
    "steam": "steam://",
    "discord": "discord:",
    "notion": "notion:",
    "zoom": "zoommtg://",
}

APP_FALLBACKS = {"spotify": "spotify:"}

_URL_RE = re.compile(
    r"^(?:https?://|www\.)\S+$|^[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|co|in|edu|ai|dev|app)(?:/\S*)?$",
    re.IGNORECASE,
)


def _normalize_target(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").lower()).strip().strip(".,!?;:\"'`")
    return re.sub(r"\bgoogle chrome\b", "chrome", t)


def _find_known(target: str):
    best = None
    for kind, table in (("app", APP_ALIASES), ("protocol", APP_PROTOCOLS), ("web", WEB_SHORTCUTS)):
        for key, value in table.items():
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", target):
                if best is None or len(key) > len(best[1]):
                    best = (kind, key, value)
    return best


def can_open(target: str) -> bool:
    t = _normalize_target(target)
    if not t:
        return False
    return bool(_URL_RE.match(t) or _find_known(t) or os.path.exists(target.strip()))


def _launch(command: str) -> bool:
    return os.system(f"start {command}") == 0


def open_target(target: str) -> str:
    original = (target or "").strip().strip(".,!?;:\"'`")
    t = _normalize_target(original)
    if not t:
        return "Could not find a website, app, or path named: (nothing)"

    if _URL_RE.match(t):
        url = original if original.lower().startswith(("http://", "https://")) else "https://" + original.lstrip("/")
        webbrowser.open(url)
        return f"Opened website: {url}"

    known = _find_known(t)
    if known:
        kind, key, value = known
        if kind == "web":
            webbrowser.open(value)
            return f"Opened website: {value}"
        ok = _launch(value)
        if not ok and key in APP_FALLBACKS:
            ok = _launch(APP_FALLBACKS[key])
        if ok:
            what = "Windows application" if kind == "app" else "app via protocol"
            return f"Launched {what}: {key.title()}"
        return f"Could not launch {key.title()}. Is it installed?"

    if original and os.path.exists(original):
        try:
            os.startfile(original)
            return f"Opened local path: {original}"
        except Exception as e:
            return f"Could not open {original}: {e}"

    if re.search(r'[&|<>^"%\r\n]', original):
        return f"Could not find a website, app, or path named: {original}"
    if os.system(f'start "" "{original}"') == 0:
        return f"Asked Windows to open: {original}"
    return f"Could not find a website, app, or path named: {original}"


def list_directory(directory_path: str = ".") -> str:
    try:
        target_dir = Path(directory_path)
        if not target_dir.exists() or not target_dir.is_dir():
            return f"Directory not found: {directory_path}"
        items = [f"- {i.name} ({'Folder' if i.is_dir() else 'File'})" for i in target_dir.iterdir()]
        return f"Contents of {directory_path}:\n" + "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        return f"Error reading directory: {e}"


def take_screenshot(filename: str = "capture.png") -> str:
    if pyautogui is None:
        return "Failed to take screenshot: the pyautogui package isn't available."
    try:
        safe_name = Path(filename or "capture.png").name or "capture.png"
        desktop_path = _desktop_dir() / safe_name
        screenshot = pyautogui.screenshot()
        screenshot.save(desktop_path)
        return f"Screenshot successfully saved to {desktop_path}"
    except Exception as e:
        return f"Failed to take screenshot: {e}"


def run_terminal_command(command: str) -> str:
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Command executed successfully. Output:\n{output[:1000]}"
        else:
            return f"Command failed with error:\n{result.stderr.strip()[:1000]}"
    except subprocess.TimeoutExpired:
        return "The command took too long to execute and timed out."
    except Exception as e:
        return f"Execution error: {e}"


def search_wikipedia(query: str) -> str:
    if wikipedia is None:
        return "Search failed: the wikipedia package isn't installed."
    try:
        search_results = wikipedia.search(query)
        if not search_results:
            return f"Could not find any Wikipedia results for '{query}'."

        best_match = search_results[0]
        summary = wikipedia.summary(best_match, sentences=3)
        return f"Information on '{best_match}':\n{summary}"
    except wikipedia.exceptions.DisambiguationError as e:
        return f"The search term is too broad. Options include: {', '.join(e.options[:5])}"
    except Exception as e:
        return f"Search failed: {e}"


def control_media(action: str) -> str:
    action = (action or "").lower().strip()
    valid_actions = {
        "play": "playpause", "pause": "playpause",
        "next": "nexttrack", "skip": "nexttrack",
        "previous": "prevtrack", "back": "prevtrack",
        "volume up": "volumeup", "volume down": "volumedown",
        "mute": "volumemute"
    }
    target_key = None
    for key in valid_actions:
        if key in action:
            target_key = valid_actions[key]
            break

    if target_key:
        if pyautogui is None:
            return "Failed to control media: the pyautogui package isn't available."
        try:
            if target_key in ["volumeup", "volumedown"]:
                for _ in range(5):
                    pyautogui.press(target_key)
            else:
                pyautogui.press(target_key)
            return f"Media control executed: {target_key}"
        except Exception as e:
            return f"Failed to control media: {e}"
    else:
        return f"Invalid media action: {action}. Tell the user valid actions are play, pause, skip, back, or volume adjustments."


def analyze_webcam() -> str:
    if cv2 is None:
        return "Error: OpenCV (cv2) isn't available, so I can't use the webcam."
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return "Error: Could not access the webcam."
        time.sleep(1)
        cap.read()
        ret, frame = cap.read()
        cap.release()

        if not ret:
            return "Error: Failed to capture a frame from the webcam."
        vision_path = _desktop_dir() / "vision_capture.png"
        cv2.imwrite(str(vision_path), frame)
        return f"Webcam activated successfully. Image saved to {vision_path}."
    except Exception as e:
        return f"Failed to analyze webcam: {e}"


# ----------------------------------------------------------------------------- Spotify
#
# Play order: Web API (needs Premium) -> open the track in the desktop app -> open Spotify's search page.
# Whatever happens, the returned sentence only says "Playing" if Spotify confirmed it, and it starts with
# "Playing"/"Opening" only when something really started or opened (intent_router uses that to pick her mood).

_spotify_client = None
_spotify_lock = threading.Lock()


def _start_uri(uri: str) -> bool:
    """Opens a URI with whatever app is registered for it (spotify:..., https:...). True if Windows accepted it."""
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        try:
            startfile(uri)
            return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(uri))
    except Exception:
        return False


def _press(key: str) -> bool:
    if pyautogui is None:
        return False
    try:
        pyautogui.press(key)
        return True
    except Exception as e:
        print(f"⚠️ Could not press {key}: {e}")
        return False


def _spotify():
    """A logged-in Spotify client. Raises RuntimeError with a short reason if it can't be set up."""
    global _spotify_client
    with _spotify_lock:
        if _spotify_client is not None:
            return _spotify_client
        if spotipy is None:
            raise RuntimeError("the spotipy package isn't installed")
        missing = spotify_credentials_missing()
        if missing:
            raise RuntimeError("missing " + ", ".join(missing) + " - add them to the .env file")
        auth = spotify_oauth(open_browser=False)   # never pop up a login page from inside the assistant
        if auth.get_cached_token() is None:
            raise RuntimeError("no saved login - run spotify_login.py once")
        _spotify_client = spotipy.Spotify(auth_manager=auth, requests_timeout=8, retries=2)
        return _spotify_client


def _reset_spotify() -> None:
    global _spotify_client
    with _spotify_lock:
        _spotify_client = None


def _open_search(query: str) -> bool:
    """Shows the search results for the query, so the user at least sees something on screen."""
    q = quote(query)
    return _start_uri(f"spotify:search:{q}") or bool(webbrowser.open(f"https://open.spotify.com/search/{q}"))


def _search_track(sp, query: str):
    queries = []
    m = re.match(r"^(.+?)\s+by\s+(.+)$", query, re.IGNORECASE)   # "blinding lights by the weeknd"
    if m:
        queries.append(f"track:{m.group(1).strip()} artist:{m.group(2).strip()}")
    queries.append(query)
    for q in queries:
        items = sp.search(q=q, limit=5, type="track").get("tracks", {}).get("items", [])
        if items:
            return items[0]
    return None


def _pick_device(sp):
    devices = sp.devices().get("devices", []) or []
    for d in devices:
        if d.get("is_active"):
            return d["id"]
    for d in devices:   # nothing active: prefer this PC's app over a phone or a speaker
        if str(d.get("type", "")).lower() == "computer" and not d.get("is_restricted"):
            return d["id"]
    return None


def _wake_spotify(sp, seconds: int = 15):
    """A device id that can play, starting the Spotify desktop app first if it isn't running."""
    device = _pick_device(sp)
    if device:
        return device
    print("🎵 Starting the Spotify app...")
    if not _start_uri("spotify:"):       # works for both the Store and the classic installer
        _launch("spotify.exe")
    for _ in range(seconds):
        time.sleep(1)
        try:
            device = _pick_device(sp)
        except Exception:
            device = None
        if device:
            return device
    return None


def _playback_state(sp, uri: str) -> str:
    """'playing_it', 'playing_other', 'idle' or 'unknown' (Spotify wouldn't tell us)."""
    try:
        state = sp.current_playback()
    except Exception:
        return "unknown"
    if not state:
        return "idle"
    if not state.get("is_playing"):
        return "idle"
    return "playing_it" if (state.get("item") or {}).get("uri") == uri else "playing_other"


def _start_playback(sp, device: str, uri: str) -> None:
    try:
        sp.start_playback(device_id=device, uris=[uri])
    except SpotifyException as e:
        if e.http_status != 404:
            raise
        # The device is known but asleep: wake it up, then try once more.
        try:
            sp.transfer_playback(device_id=device, force_play=False)
        except Exception:
            pass
        time.sleep(1.5)
        sp.start_playback(device_id=device, uris=[uri])


def _open_in_app(sp, uri: str):
    """Opens the track in the Spotify app and makes sure it plays. True = confirmed playing, None = can't tell, False = not playing."""
    if not _start_uri(uri):
        return False
    time.sleep(3.0)   # let the app come up and load the track
    state = _playback_state(sp, uri) if sp else "unknown"
    if state == "playing_it":
        return True
    if state == "playing_other":
        return False   # something else is playing; pressing play/pause would only stop it
    _press("playpause")
    time.sleep(1.5)
    state = _playback_state(sp, uri) if sp else "unknown"
    if state == "playing_it":
        return True
    return None if state == "unknown" else False


def play_spotify_music(query: str) -> str:
    """Finds a track on Spotify and plays it, falling back step by step so the user always sees or hears something."""
    query = re.sub(r"\s+on spotify$", "", (query or "").strip().strip("\"'`.,!?"), flags=re.IGNORECASE).strip()
    if not query:
        return "Could not find that song: nothing to search for."

    try:
        sp = _spotify()
    except Exception as e:
        print(f"🎵 Spotify isn't ready: {e}")
        _open_search(query)
        return f"Spotify isn't linked yet, so I opened its search page for '{query}'. Run the Spotify login script once to let me play songs."

    try:
        track = _search_track(sp, query)
    except Exception as e:
        print(f"🎵 Spotify search error: {e}")
        if getattr(e, "http_status", None) == 401:
            _reset_spotify()   # expired token: rebuild the client next time
        _open_search(query)
        return f"Spotify search failed, so I opened its search page for '{query}' instead."
    if track is None:
        _open_search(query)
        return f"Could not find '{query}' on Spotify, so I opened its search page."

    uri = track["uri"]
    artists = track.get("artists") or [{}]
    label = f"'{track.get('name', query)}' by {artists[0].get('name', 'unknown artist')}"

    # 1) Web API: needs Premium, and a device that is awake.
    try:
        device = _wake_spotify(sp)
    except Exception as e:
        print(f"🎵 Could not find a Spotify device: {e}")
        device = None
    if device:
        try:
            _start_playback(sp, device, uri)
            state = "idle"
            for _ in range(2):
                time.sleep(1.5)
                state = _playback_state(sp, uri)
                if state in ("playing_it", "unknown"):
                    return f"Playing {label} on Spotify."
            print(f"🎵 The API accepted the request but Spotify reports '{state}'. Trying the desktop app instead.")
        except SpotifyException as e:
            print(f"🎵 Spotify playback error {e.http_status}: {e.msg}")   # 403 usually means a free account
            if e.http_status == 401:
                _reset_spotify()
        except Exception as e:
            print(f"🎵 Spotify playback error: {e}")

    # 2) Open the track in the desktop app and confirm that it plays.
    result = _open_in_app(sp, uri)
    if result is True:
        return f"Playing {label} on Spotify."
    if result is None:
        return f"Opening {label} in the Spotify app."
    if result is False and _playback_state(sp, uri) == "playing_other":
        return f"Opening {label} in the Spotify app, but something else is still playing - press play on it."

    # 3) Last resort: at least show the search results.
    _open_search(f"{track.get('name', query)} {artists[0].get('name', '')}".strip())
    return f"Opening Spotify's search for {label}, because I couldn't start it automatically. Press play on the first result."


def minecraft_command(command: str) -> str:
    """Sends a game command to the Minecraft companion bot (follow, collect, attack, ...)."""
    try:
        from minecraft_agent import send_command
        return send_command(command)
    except Exception as e:  # never let a Minecraft problem break the assistant
        return f"Error: Minecraft control failed ({e})"


def minecraft_goal(goal: str) -> str:
    """Works out the steps herself and runs them: she looks, decides, acts, and reacts to what happened."""
    try:
        from minecraft_planner import run_goal
        return run_goal(goal)
    except Exception as e:  # never let a Minecraft problem break the assistant
        return f"Failed: Minecraft thinking stopped ({e})"


def minecraft_initiative(mode: str) -> str:
    """Lets her pick her own Minecraft goals while Faazil is quiet, or stops her doing that."""
    try:
        from minecraft_planner import set_mode
        return set_mode(mode)
    except Exception as e:
        return f"Failed: Minecraft initiative mode did not change ({e})"


def minecraft_mode(mode: str) -> str:
    """The one switch: brings her into Minecraft and lets her play on her own, or takes her out of it."""
    try:
        from mc_mode import set_mode
        return set_mode(mode)
    except Exception as e:  # never let a Minecraft problem break the assistant
        return f"Failed: Minecraft mode did not change ({e})"


AVAILABLE_FUNCTIONS = {
    "create_project_file": create_project_file,
    "open_target": open_target,
    "list_directory": list_directory,
    "take_screenshot": take_screenshot,
    "run_terminal_command": run_terminal_command,
    "search_wikipedia": search_wikipedia,
    "control_media": control_media,
    "analyze_webcam": analyze_webcam,
    "play_spotify_music": play_spotify_music,
    "minecraft_command": minecraft_command,
    "minecraft_goal": minecraft_goal,
    "minecraft_initiative": minecraft_initiative,
    "minecraft_mode": minecraft_mode
}
