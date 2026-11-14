"""Tests for the door, the speech key and two screens talking to one brain.

These run the real FastAPI app with fake ears, voice and brain, over a pretend network:
nothing here needs the GPU, GPT-SoVITS, Minecraft or a browser.
"""
import json
import os
import unittest

os.environ["REMIELLE_ACCESS_TOKEN"] = "test-door-token"

from starlette.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
import clients  # noqa: E402
from clients import Hub  # noqa: E402

LOCAL = ("127.0.0.1", 53211)
PHONE = ("192.168.1.55", 41122)


class FakeRecorder:
    def __init__(self, hears="open youtube"):
        self.hears = hears
        self.error = None
        self.starts = 0

    def start(self):
        self.starts += 1
        if self.error:
            raise RuntimeError(self.error)

    def stop_and_transcribe(self):
        return self.hears


class Talking(unittest.TestCase):
    def setUp(self):
        app_module.hub = Hub()
        app_module.recorder = FakeRecorder()
        self.calls = []
        self._chat = app_module.chat_with_agent
        self._voice = app_module.play_anime_voice
        self._transcribe = app_module.transcribe_pcm16
        app_module.chat_with_agent = lambda text: (self.calls.append(text) or "[happy] doing it now")
        app_module.play_anime_voice = lambda text: os.path.join("static", "index.html")
        # Starlette fixes the pretend source address per client, so each device gets its own.
        self.nets = {}

    def tearDown(self):
        app_module.chat_with_agent = self._chat
        app_module.play_anime_voice = self._voice
        app_module.transcribe_pcm16 = self._transcribe

    def net(self, client):
        return self.nets.setdefault(client, TestClient(app_module.app, client=client))

    def get(self, path, client=LOCAL, **kwargs):
        return self.net(client).get(path, **kwargs)


class TheDoor(Talking):
    def test_this_computer_is_always_let_in(self):
        self.assertEqual(self.get("/").status_code, 200)

    def test_a_stranger_on_the_wifi_is_not(self):
        answer = self.get("/", client=PHONE)
        self.assertEqual(answer.status_code, 403)
        self.assertIn("token", answer.json()["error"].lower())

    def test_the_link_from_the_launcher_gets_in_and_remembers_the_token(self):
        answer = self.get(f"/?token={app_module.ACCESS_TOKEN}", client=PHONE)
        self.assertEqual(answer.status_code, 200)
        self.assertIn(app_module.device_access.COOKIE, answer.cookies)

    def test_the_cookie_alone_is_enough_afterwards(self):
        self.net(PHONE).cookies.clear()
        self.get(f"/?token={app_module.ACCESS_TOKEN}", client=PHONE)
        self.assertEqual(self.get("/speak", client=PHONE,
                                  params={"text": "hi", "key": "x"}).status_code, 403,
                         "the door is open, but a bad speech key is still refused")
        self.assertEqual(self.get("/", client=PHONE).status_code, 200, "the cookie carries the page itself")

    def test_a_wrong_or_empty_token_does_not_work(self):
        for path in ("/?token=nope", "/?token=", "/?token=test-door-tokenx"):
            self.assertEqual(self.get(path, client=PHONE).status_code, 403, path)

    def test_a_header_carries_it_for_scripts(self):
        answer = self.get("/", client=PHONE, headers={"x-remielle-token": app_module.ACCESS_TOKEN})
        self.assertEqual(answer.status_code, 200)

    def test_the_page_she_serves_is_the_real_one(self):
        body = self.get("/").text
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("manifest.webmanifest", body)


class TheSpeechKey(Talking):
    def speak(self, key=""):
        return self.get("/speak", client=PHONE, params={"text": "hello", "key": key})

    def test_a_screen_has_to_say_hello_before_it_can_use_her_voice(self):
        self.assertEqual(self.speak().status_code, 403)
        self.assertEqual(self.speak("made-up-key").status_code, 403)

    def test_the_key_from_the_hello_works_and_only_once_for_that_screen(self):
        with self.net(LOCAL).websocket_connect("/ws") as ws:
            ws.send_json({"action": "hello", "device": "pc"})
            key = ws.receive_json()["speak_key"]
            answer = self.get("/speak", client=LOCAL, params={"text": "hello", "key": key})
            self.assertEqual(answer.status_code, 200)
            self.assertTrue(answer.json()["audio_url"].startswith("/static/"))
        self.assertEqual(self.get("/speak", client=LOCAL, params={"text": "hello", "key": key}
                                  ).status_code, 403, "a closed screen loses its voice")

    def test_a_long_sentence_is_trimmed_before_it_reaches_the_voice_server(self):
        seen = {}

        def fake_voice(text):
            seen["text"] = text
            return os.path.join("static", "index.html")

        app_module.play_anime_voice = fake_voice
        with self.net(LOCAL).websocket_connect("/ws") as ws:
            ws.send_json({"action": "hello", "device": "pc"})
            key = ws.receive_json()["speak_key"]
            self.get("/speak", client=LOCAL, params={"text": "word " * 300, "key": key})
        self.assertLessEqual(len(seen["text"]), app_module.MAX_SPEECH_CHARS)

    def test_a_broken_voice_server_is_an_honest_error(self):
        app_module.play_anime_voice = lambda text: None
        with self.net(LOCAL).websocket_connect("/ws") as ws:
            ws.send_json({"action": "hello", "device": "pc"})
            key = ws.receive_json()["speak_key"]
            answer = self.get("/speak", client=LOCAL, params={"text": "hello", "key": key})
        self.assertEqual(answer.status_code, 500)


class TwoScreensOneBrain(Talking):
    def connect(self, device, client=LOCAL, audio=True):
        path = "/ws" + (f"?token={app_module.ACCESS_TOKEN}" if client != LOCAL else "")
        ws = self.net(client).websocket_connect(path)
        ws.__enter__()
        ws.send_json({"action": "hello", "device": device, "audio": audio})
        hello = ws.receive_json()
        return ws, hello

    def test_only_the_screen_you_used_shows_thinking(self):
        pc, _ = self.connect("pc")
        phone, _ = self.connect("phone", client=PHONE)
        phone.send_json({"action": "prompt", "text": "what time is it"})
        self.assertEqual(phone.receive_json()["state"], "thinking", "the screen you spoke into shows the work")
        self.assertEqual(pc.receive_json()["state"], "speaking", "the idle screen is only told the answer")

    def test_the_other_screen_hears_the_answer_without_making_a_sound(self):
        pc, _ = self.connect("pc")
        phone, _ = self.connect("phone", client=PHONE)
        phone.send_json({"action": "prompt", "text": "what is my cpu"})
        self.assertEqual(phone.receive_json()["state"], "thinking")
        answered = phone.receive_json()
        self.assertEqual((answered["state"], answered["speak"]), ("speaking", True))
        mirrored = pc.receive_json()
        self.assertEqual((mirrored["state"], mirrored["speak"]), ("speaking", False))
        self.assertEqual(mirrored["text"], "doing it now", "the quiet screen still shows the words")

    def test_a_screen_that_asked_for_silence_never_gets_the_voice(self):
        laptop, _ = self.connect("pc")
        tablet, _ = self.connect("web", client=PHONE, audio=False)
        tablet.send_json({"action": "prompt", "text": "hello there"})
        tablet.receive_json()
        self.assertEqual(tablet.receive_json()["speak"], False)
        self.assertEqual(laptop.receive_json()["speak"], True, "someone has to say it out loud")

    def test_junk_and_empty_frames_do_not_kill_the_connection(self):
        ws, hello = self.connect("pc")
        self.assertIn("speak_key", hello)
        ws.send_text("this is not json")
        ws.send_text("[1,2,3]")
        ws.send_json({"action": "nonsense"})
        ws.send_json({"action": "prompt", "text": "   "})
        self.assertEqual(ws.receive_json()["state"], "idle")
        ws.send_json({"action": "prompt", "text": "hi"})
        ws.receive_json()
        self.assertEqual(ws.receive_json()["state"], "speaking")

    def test_a_state_request_comes_straight_back(self):
        ws, _ = self.connect("pc")
        ws.send_json({"action": "state", "state": "relaxed"})
        self.assertEqual(ws.receive_json()["state"], "relaxed")

    def test_the_phone_connection_needs_the_token_too(self):
        with self.assertRaises(Exception):
            self.net(PHONE).websocket_connect("/ws").__enter__()


class HerOwnMicrophone(Talking):
    def test_audio_from_the_phone_is_what_she_listens_to(self):
        heard = {}

        def fake_transcribe(audio, min_seconds=0.4, label="microphone", hint=""):
            heard["bytes"] = audio
            heard["label"] = label
            return "sit on the boat"

        app_module.transcribe_pcm16 = fake_transcribe
        ws = self.net(PHONE).websocket_connect(f"/ws?token={app_module.ACCESS_TOKEN}")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "phone"})
        ws.receive_json()
        ws.send_json({"action": "ptt_start", "mic": "phone"})
        self.assertEqual(ws.receive_json()["state"], "listening")
        ws.send_bytes(b"\x01\x02" * 800)
        ws.send_json({"action": "ptt_stop"})
        self.assertEqual(ws.receive_json()["state"], "thinking")
        # The words she heard only arrive once the bytes are transcribed, and her answer only once the brain has run.
        self.assertEqual(ws.receive_json().get("heard"), "sit on the boat")
        self.assertEqual(heard["bytes"], b"\x01\x02" * 800)
        self.assertEqual(heard["label"], "your phone")
        self.assertEqual(ws.receive_json()["state"], "speaking")
        self.assertEqual(self.calls, ["sit on the boat"])

    def test_the_computer_mic_is_still_the_default(self):
        ws = self.net(LOCAL).websocket_connect("/ws")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "pc"})
        ws.receive_json()
        ws.send_json({"action": "ptt_start"})
        self.assertEqual(ws.receive_json()["state"], "listening")
        ws.send_json({"action": "ptt_stop"})
        ws.receive_json()
        self.assertEqual(ws.receive_json()["heard"], "open youtube")

    def test_a_microphone_that_cannot_open_is_said_out_loud(self):
        app_module.recorder = FakeRecorder()
        app_module.recorder.error = "no such device"
        ws = self.net(LOCAL).websocket_connect("/ws")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "pc"})
        ws.receive_json()
        ws.send_json({"action": "ptt_start"})
        answer = ws.receive_json()
        self.assertEqual(answer["state"], "idle")
        self.assertIn("no such device", answer["error"])

    def test_silence_from_the_phone_is_not_a_crash(self):
        def shout(audio, min_seconds=0.4, label="microphone", hint=""):
            raise RuntimeError(f"{label} captured nothing but silence. {hint}")

        app_module.transcribe_pcm16 = shout
        ws = self.net(LOCAL).websocket_connect("/ws?token=x")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "phone"})
        ws.receive_json()
        ws.send_json({"action": "ptt_start", "mic": "phone"})
        ws.receive_json()
        ws.send_bytes(b"\x00\x00" * 500)
        ws.send_json({"action": "ptt_stop"})
        self.assertEqual(ws.receive_json()["state"], "thinking")
        answer = ws.receive_json()
        self.assertEqual(answer["state"], "idle")
        self.assertIn("silence", answer["error"])

    def test_a_button_held_forever_stops_at_fifteen_seconds(self):
        app_module.transcribe_pcm16 = lambda *a, **k: "keep going"
        ws = self.net(LOCAL).websocket_connect("/ws")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "phone"})
        ws.receive_json()
        ws.send_json({"action": "ptt_start", "mic": "phone"})
        ws.receive_json()
        frame = b"\x07\x00" * 1600          # 0.2 s of 16 kHz mono PCM16
        for _ in range(clients.REMOTE_LIMIT_BYTES // len(frame) + 3):
            ws.send_bytes(frame)
        cut = ws.receive_json()
        self.assertEqual((cut["state"], "long" in cut.get("error", "").lower()), ("idle", True))
        ws.send_json({"action": "ptt_stop"})
        self.assertEqual(ws.receive_json()["state"], "thinking", "what she caught is still answered")


class BusyUntilSheFinishes(Talking):
    def test_a_screen_talking_blocks_unprompted_speech_then_clears(self):
        ws = self.net(LOCAL).websocket_connect("/ws")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "pc"})
        ws.receive_json()
        ws.send_json({"action": "prompt", "text": "hi"})
        ws.receive_json()
        ws.receive_json()
        self.assertTrue(app_module._ui_busy())
        ws.send_json({"action": "ui_idle"})
        self.assertFalse(app_module._ui_busy())


class TheHomeScreenApp(Talking):
    """What the phone needs to behave like an app: a worker at the right depth and icons that exist."""

    def test_the_worker_comes_from_the_root(self):
        answer = self.get("/sw.js")
        self.assertEqual(answer.status_code, 200)
        self.assertIn("javascript", answer.headers["content-type"])
        self.assertIn("remielle-v", answer.text)

    def test_the_page_registers_that_root_worker(self):
        # A worker served from /static/ can only look after /static/, so the home-screen app would never cache her.
        body = self.get("/").text
        self.assertIn("serviceWorker.register('/sw.js')", body)

    def test_every_icon_the_manifest_promises_is_a_real_file(self):
        manifest = json.loads(self.get("/static/manifest.webmanifest").text)
        self.assertEqual(manifest["name"], "Remielle")
        self.assertEqual(manifest["display"], "standalone")
        for icon in manifest["icons"]:
            path = app_module.STATIC_DIR / icon["src"].lstrip("/").split("/", 1)[1]   # "/static/icons/x" -> "icons/x"
            self.assertTrue(path.is_file(), icon["src"])
            self.assertEqual(self.get(icon["src"]).status_code, 200, icon["src"])

    def test_the_window_icon_is_a_real_ico(self):
        icon = app_module.STATIC_DIR / "icons" / "remielle.ico"
        self.assertTrue(icon.is_file(), "Setup_App.bat and the Start Menu shortcut point at this")
        self.assertEqual(icon.read_bytes()[:4], b"\x00\x00\x01\x00")

    def test_a_stranger_on_the_wifi_cannot_even_read_the_worker(self):
        self.assertEqual(self.get("/sw.js", client=PHONE).status_code, 403)


if __name__ == "__main__":
    unittest.main()
