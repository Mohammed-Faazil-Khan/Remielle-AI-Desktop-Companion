from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import webview
import json
import threading
import time
import os
import asyncio
import re
import traceback
from pathlib import Path
import minecraft_agent
import minecraft_planner
import mc_mode
import device_access
from clients import Hub
from remielle_config import APP_HOST, APP_PORT, env_bool, env_float, cert_paths, https_is_on

# Import the TTS engine (GPT-SoVITS) & Agent Brain
from voice_tts import play_anime_voice
from agent_brain import chat_with_agent
from ears_stt import PushToTalkRecorder, transcribe_pcm16

recorder = PushToTalkRecorder()

# Reply text starts with a tag like "[happy] ..." (see agent_brain.py).
# Split it into (emotion, spoken_text) so the tag is never sent to TTS or shown on screen.
_EMOTION_RE = re.compile(r"^\s*\[(happy|sad|angry|surprised|relaxed)\]\s*", re.IGNORECASE)

def extract_emotion(reply: str) -> tuple[str, str]:
    m = _EMOTION_RE.match(reply or "")
    if not m:
        return "relaxed", (reply or "").strip()
    return m.group(1).lower(), _EMOTION_RE.sub("", reply, count=1).strip()

app = FastAPI()

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

hub = Hub()
PAGE_FILE = STATIC_DIR / "index.html"

# The one secret that lets a phone (or a tunnel visitor) reach her. Made once and remembered in .env,
# because a page like this can make the PC open programs and run commands.
ACCESS_TOKEN = device_access.ensure_token()
MAX_SPEECH_CHARS = 400


def _cookie_value(header: str, name: str) -> str:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def _visitor_token(query, headers, cookies, client_ip: str) -> bool:
    """Local traffic is this computer talking to itself; everything else has to prove it is you."""
    if device_access.is_local(client_ip):
        return True
    given = (query.get("token") or headers.get("x-remielle-token")
             or cookies.get(device_access.COOKIE) or "")
    return device_access.token_matches(given, ACCESS_TOKEN)


@app.middleware("http")
async def _only_your_devices(request: Request, call_next):
    ip = request.client.host if request.client else ""
    if _visitor_token(request.query_params, request.headers, request.cookies, ip):
        response = await call_next(request)
        # Remember the token in a cookie so the page's other files and its websocket get in too.
        if not device_access.is_local(ip) and request.query_params.get("token"):
            response.set_cookie(device_access.COOKIE, request.query_params["token"],
                                httponly=True, samesite="strict", secure=https_is_on())
        return response
    return JSONResponse({"error": "This device has to open the link from Launch_Remielle.bat, with its token."},
                        status_code=403)


@app.get("/")
async def index():
    return FileResponse(str(PAGE_FILE), media_type="text/html")


@app.get("/sw.js")
def service_worker():
    # A service worker can only look after pages at its own depth or above, so this file has to come
    # from the root - served from /static/, where it lives next to everything else it caches.
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")


@app.get("/speak")
def speak(text: str, key: str = ""):
    # Every screen gets its own key when it connects, so a stranger on the Wi-Fi cannot use her voice
    # to make words on this computer. play_anime_voice returns the NEW wav, so we never replay an old reply.
    if hub.find_by_key(key) is None:
        return JSONResponse({"status": "error", "message": "This screen has not said hello yet."}, status_code=403)
    path = play_anime_voice((text or "")[:MAX_SPEECH_CHARS])
    if path and os.path.exists(path):
        return JSONResponse({"status": "success", "audio_url": "/static/" + os.path.basename(path)})
    return JSONResponse({"status": "error", "message": "Failed to generate speech - is the GPT-SoVITS server running?"},
                        status_code=500)


BLANK_SPEECH = "Hmm... I lost my words for a second. Could you say that again?"
PHONE_MIC_HINT = ("Hold the button down and speak toward the phone. If it keeps failing, allow the microphone "
                  "for this site in the browser's settings (the lock or globe icon by the address).")


async def _announce(text: str, emotion: str = "relaxed") -> int:
    """Tells every open screen what she is saying. Exactly one of them makes the sound, and that one is busy."""
    spoken = {"state": "speaking", "text": text, "emotion": emotion}
    voice = hub.voice()
    sent = await hub.send(spoken)
    if voice is not None:
        hub.hold_voice(voice)
    return sent


async def _reply_to(client, user_text: str):
    """Runs the agent and tells the pages what to say. chat_with_agent never raises: a failure comes back as a spoken message."""
    hub.asked(client)          # the screen you spoke into is the one she answers out loud through
    reply = await asyncio.to_thread(chat_with_agent, user_text)
    emotion, spoken_reply = extract_emotion(reply)
    if not spoken_reply:
        emotion, spoken_reply = "sad", BLANK_SPEECH
    print(f"Remielle replied [{emotion}]: {spoken_reply}")
    await _announce(spoken_reply, emotion)
    if GAME_ECHO_CHAT and minecraft_agent.is_connected():
        asyncio.create_task(asyncio.to_thread(minecraft_agent.say, spoken_reply))


async def _set_mc_mode(client, on: bool) -> None:
    """The one Minecraft switch: the game, her bot and her own goals. It can take a minute, so it runs on its own."""
    line = (await asyncio.to_thread(mc_mode.switch, on)).strip()
    failed = line.lower().startswith("failed:")
    await hub.send({"mc_mode": mc_mode.is_on()}, only=client)
    await _announce("I couldn't do that. " + line[7:].strip() if failed else line, "sad" if failed else "happy")


async def _handle_message(client, data: dict):
    action = data.get("action")

    if action == "ptt_start":
        # A screen asks to be listened to: either this computer's microphone or the caller's own phone.
        client.mic = "phone" if str(data.get("mic") or "").lower() == "phone" else "pc"
        if client.mic == "phone":
            client.start_remote()
        else:
            try:
                await asyncio.to_thread(recorder.start)
            except Exception as e:
                print(f"Mic start error: {e}")
                await hub.send({"state": "idle", "text": "", "error": f"Mic error: {e}"}, only=client)
                return
        await hub.send({"state": "listening", "text": ""}, only=client)

    elif action == "ptt_stop":
        await hub.send({"state": "thinking", "text": ""}, only=client)
        # Whichever microphone this screen asked for is the one she answers from - even if the phone's
        # audio was cut off at the cap, so a long hold never gets the computer's microphone instead.
        phone_audio = client.take_remote() if client.mic == "phone" else None
        try:
            if phone_audio is not None:
                user_text = await asyncio.to_thread(transcribe_pcm16, phone_audio, 0.4, "your phone", PHONE_MIC_HINT)
            else:
                user_text = await asyncio.to_thread(recorder.stop_and_transcribe)
        except Exception as e:
            print(f"Transcription error: {e}")
            await hub.send({"state": "idle", "text": "", "error": str(e)}, only=client)
            return

        if not user_text:
            await hub.send({"state": "idle", "text": "",
                            "error": "Didn't catch anything - hold the button down while you speak."}, only=client)
            return

        print(f"User said: {user_text}")
        await hub.send({"state": "thinking", "text": "", "heard": user_text})
        await _reply_to(client, user_text)

    elif action == "prompt":
        user_text = (data.get("text") or "").strip()
        if not user_text:
            await hub.send({"state": "idle", "text": ""}, only=client)
            return
        print(f"User said: {user_text}")
        await hub.send({"state": "thinking", "text": ""}, only=client)
        await _reply_to(client, user_text)

    elif action == "mc_mode":
        hub.asked(client)
        await hub.send({"state": "thinking", "text": ""}, only=client)
        asyncio.create_task(_set_mc_mode(client, bool(data.get("on"))))

    elif action == "ui_idle":
        hub.clear_busy(client)
        return

    elif action == "state":
        await hub.send({"state": data.get("state"), "text": ""}, only=client)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """One connection per open screen. Audio from a phone arrives as binary frames between the hello and the stop."""
    ip = websocket.client.host if websocket.client else ""
    if not _visitor_token(websocket.query_params, websocket.headers, websocket.cookies, ip):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    client = None
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            streamed = message.get("bytes")
            if streamed is not None:
                was_recording = client.recording if client is not None else False
                kept = client.feed(streamed) if client is not None else False
                if was_recording and not kept and client is not None and not client.recording:
                    await websocket.send_json({"state": "idle", "text": "",
                                               "error": "That was too long for me - ask me something shorter."})
                continue

            raw = message.get("text")
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue   # a malformed message must not kill the connection
            if not isinstance(data, dict):
                continue

            if client is None or data.get("action") == "hello":
                if client is not None:
                    hub.drop(client)
                client = hub.add(websocket, str(data.get("device") or "web"), data.get("audio") is not False)
                await websocket.send_json({"state": "idle", "text": "", "client_id": client.id,
                                           "speak_key": client.speak_key, "mc_mode": mc_mode.is_on()})
                continue

            try:
                await _handle_message(client, data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # Whatever went wrong, keep the connection alive and put the avatar back to idle.
                print(f"❌ Error while handling '{data.get('action')}': {e}")
                traceback.print_exc()
                try:
                    await hub.send({"state": "idle", "text": "", "error": f"Something went wrong: {e}"}, only=client)
                except Exception:
                    raise WebSocketDisconnect()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if client is not None:
            hub.drop(client)



# --- MINECRAFT / GAME LINK -------------------------------------------------------------------
# While she is in the game, the app window is usually NOT focused, so the SPACE key in the window can't be used.
# A global hold-to-talk key (default F8) works from anywhere. Spoken events from the game (danger, low health,
# finished tasks, player chat) are queued and spoken only when she is idle, so she never talks over herself.
GAME_PTT_KEY = (os.getenv("REMIELLE_GAME_PTT_KEY", "f8") or "f8").strip().lower()
GAME_EVENTS = env_bool("REMIELLE_GAME_EVENTS", True)
GAME_ECHO_CHAT = env_bool("REMIELLE_GAME_ECHO_CHAT", True)      # also write her replies in the in-game chat
GAME_MIN_GAP = env_float("REMIELLE_GAME_MIN_GAP", 6.0)           # seconds between unsolicited sentences
GAME_EVENT_MAX_AGE = 30.0                                        # an old event is no longer worth saying

_main_loop = None
_last_spoken = 0.0
_hotkey_down = False
_pending_events = []


def _ui_busy() -> bool:
    """True while any screen is still listening, thinking or speaking - she never talks over herself."""
    return hub.busy()


async def _say_unprompted(text: str, emotion: str = "relaxed") -> bool:
    """Speaks without being asked. False when there is no screen open to speak through."""
    global _last_spoken
    text = (text or "").strip()
    if not text:
        return False
    sent = await _announce(text, emotion)
    if sent:
        _last_spoken = time.monotonic()
        if GAME_ECHO_CHAT:
            await asyncio.to_thread(minecraft_agent.say, text)
    return bool(sent)


async def _game_hotkey_pressed():
    """F8 anywhere on the PC: this computer's microphone answers, and this computer's window speaks."""
    global _hotkey_down
    if _hotkey_down or _ui_busy() or not len(hub):
        return
    hub.prefer("pc")
    try:
        await asyncio.to_thread(recorder.start)
    except Exception as e:
        print(f"Mic start error: {e}")
        await hub.send({"state": "idle", "text": "", "error": f"Mic error: {e}"})
        return
    _hotkey_down = True
    await hub.send({"state": "listening", "text": ""})


async def _game_hotkey_released():
    global _hotkey_down
    if not _hotkey_down:
        return
    _hotkey_down = False
    await hub.send({"state": "thinking", "text": ""})
    try:
        user_text = await asyncio.to_thread(recorder.stop_and_transcribe)
    except Exception as e:
        print(f"Transcription error: {e}")
        await hub.send({"state": "idle", "text": "", "error": str(e)})
        return
    if not user_text:
        await hub.send({"state": "idle", "text": "",
                        "error": f"Didn't catch anything - hold {GAME_PTT_KEY.upper()} while you speak."})
        return
    print(f"User said (hotkey): {user_text}")
    await hub.send({"state": "thinking", "text": "", "heard": user_text})
    await _reply_to(hub.voice(), user_text)   # also echoes the reply to the in-game chat when she is in the world


def _start_global_hotkey(loop) -> None:
    """Hold GAME_PTT_KEY anywhere on the PC to talk. Silently skipped when pynput is missing."""
    try:
        from pynput import keyboard
    except Exception as e:
        print(f"ℹ️ Global push-to-talk key disabled (pip install pynput to enable it): {e}")
        return

    def _matches(key) -> bool:
        name = getattr(key, "name", None) or getattr(key, "char", None)
        return str(name or "").lower() == GAME_PTT_KEY

    def on_press(key):
        if _matches(key) and not _hotkey_down:
            asyncio.run_coroutine_threadsafe(_game_hotkey_pressed(), loop)

    def on_release(key):
        if _matches(key):
            asyncio.run_coroutine_threadsafe(_game_hotkey_released(), loop)

    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release, daemon=True)
        listener.start()
        print(f"🎙️ Hold {GAME_PTT_KEY.upper()} anywhere to talk to Remielle.")
    except Exception as e:
        print(f"⚠️ Could not start the global hotkey: {e}")


async def _answer_game_chat(event: dict) -> None:
    """Someone said her name in the in-game chat: answer in character, in chat and out loud."""
    who = str(event.get("player") or "someone")
    text = str(event.get("text") or "").strip()
    if not text:
        return
    prompt = text if event.get("isOwner") else f"(Another player named {who} says in the Minecraft chat) {text}"
    reply = await asyncio.to_thread(chat_with_agent, prompt)
    emotion, spoken = extract_emotion(reply)
    if not spoken:
        return
    print(f"Remielle (game chat) [{emotion}]: {spoken}")
    for _ in range(40):                 # wait (up to ~20 s) for her to finish whatever she is saying
        if not _ui_busy():
            break
        await asyncio.sleep(0.5)
    await _say_unprompted(spoken, emotion)


_initiative_task = None


async def _initiative_round() -> None:
    """One self-chosen Minecraft goal, run off the event loop so polling never stalls while she works."""
    global _initiative_task
    try:
        spoken = await asyncio.to_thread(minecraft_planner.tick)
        if spoken and len(hub) and not _ui_busy():
            await _say_unprompted(spoken, "relaxed")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ Initiative round error: {e}")
    finally:
        _initiative_task = None


async def _game_event_pump() -> None:
    """Polls the bot for new events. Never lets an exception end the loop."""
    global _pending_events, _initiative_task
    while True:
        try:
            await asyncio.sleep(1.5)
            if not GAME_EVENTS:
                continue
            new = await asyncio.to_thread(minecraft_agent.poll_events)
            now = time.monotonic()
            for e in new:
                if not isinstance(e, dict):
                    continue
                if e.get("type") == "chat":
                    asyncio.create_task(_answer_game_chat(e))
                elif e.get("speak") and e.get("message"):
                    _pending_events.append((now, e))
            _pending_events = [(t, e) for (t, e) in _pending_events if now - t < GAME_EVENT_MAX_AGE][-8:]
            # Truly nothing happening: she may pick one small goal of her own, if that mode is switched on.
            if (_initiative_task is None and minecraft_planner.is_on() and not _pending_events
                    and not _ui_busy() and not _hotkey_down and len(hub)):
                _initiative_task = asyncio.create_task(_initiative_round())
            if not _pending_events or _ui_busy() or _hotkey_down or not len(hub):
                continue
            _pending_events.sort(key=lambda te: (-int(te[1].get("priority") or 1), te[0]))
            urgent = int(_pending_events[0][1].get("priority") or 1) >= 3
            if not urgent and now - _last_spoken < GAME_MIN_GAP:
                continue
            _, event = _pending_events.pop(0)
            await _say_unprompted(str(event.get("message")), str(event.get("mood") or "relaxed"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ Game event pump error: {e}")
            await asyncio.sleep(3)


@app.on_event("startup")
async def _on_startup():
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    _start_global_hotkey(_main_loop)
    asyncio.create_task(_game_event_pump())


# --- FRONTEND -----------------------------------------------------------------------------
# The page is static/index.html: a real file now, so your phone can cache it for the home-screen
# app and so nobody has to edit six hundred lines of HTML buried inside a Python string.

def _tls() -> tuple:
    """(keyfile, certfile) when she can speak https, else (None, None) and she stays on plain http."""
    key, certificate = cert_paths()
    return (key, certificate) if (key and certificate and https_is_on()) else (None, None)


def _startup_notes() -> None:
    """Prints the one link your phone needs, in plain words, when she comes up."""
    https = bool(_tls()[0])
    ip = device_access.local_ip()
    print("\n" + "=" * 64)
    print(f"  This computer : {'https' if https else 'http'}://127.0.0.1:{APP_PORT}  (the app window)")
    if device_access.is_routable(ip):
        print(f"  Your phone    : {device_access.phone_url(ACCESS_TOKEN, ip, APP_PORT, https)}")
        print("                Open that link on the phone, then menu > Add to Home screen.")
        print("                It needs the same Wi-Fi as this PC.")
        if https:
            print("                For her voice from the phone, either install certs\\rootCA.pem on the phone")
            print("                (Android steps are in PHONE.md), or skip the certificate and use the")
            print("                Launch_Tunnel.bat link below - that one is trusted already.")
        else:
            print("                Voice from the phone needs https - run Setup_App.bat once, then restart her.")
    else:
        print("  Your phone    : no home-network address found, so she is on this computer only.")
    print(f"  Away from home: Launch_Tunnel.bat prints an https link that works anywhere.")
    print("=" * 64 + "\n")


def run_server():
    kwargs = dict(host=APP_HOST, port=APP_PORT, log_level="warning")
    key, certificate = _tls()
    if key:
        kwargs.update(ssl_keyfile=key, ssl_certfile=certificate)
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    _startup_notes()
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(1)
    window_icon = STATIC_DIR / "icons" / "remielle.ico"
    window_kwargs = dict(width=600, height=800, transparent=True)
    if window_icon.is_file():
        window_kwargs["icon"] = str(window_icon)
    scheme = "https" if _tls()[0] else "http"
    webview.create_window("Remielle", f"{scheme}://127.0.0.1:{APP_PORT}", **window_kwargs)
    # Set AVATAR_DEBUG=1 to enable right-click > Inspect (console shows JS errors)
    webview.start(debug=os.environ.get("AVATAR_DEBUG") == "1")