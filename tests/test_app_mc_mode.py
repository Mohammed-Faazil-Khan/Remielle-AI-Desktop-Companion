"""The Minecraft switch as the screens see it: one click, one honest answer, and the truth on a new screen."""
import os
import unittest

os.environ["REMIELLE_ACCESS_TOKEN"] = "test-door-token"

from starlette.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from clients import Hub  # noqa: E402

LOCAL = ("127.0.0.1", 53211)


class FakeMcMode:
    def __init__(self, answer, reaches=True):
        self.answer = answer
        self.reaches = reaches
        self.on = False
        self.calls = []

    def switch(self, on):
        self.calls.append(on)
        self.on = bool(on) and self.reaches
        return self.answer

    def is_on(self):
        return self.on


class TheSwitch(unittest.TestCase):
    def setUp(self):
        app_module.hub = Hub()
        self._mc = app_module.mc_mode
        self.net = TestClient(app_module.app, client=LOCAL)

    def tearDown(self):
        app_module.mc_mode = self._mc

    def ws(self, fake):
        """A screen that has said hello - the answer is already read, so what comes next is real news."""
        app_module.mc_mode = fake
        ws = self.net.websocket_connect("/ws")
        ws.__enter__()
        ws.send_json({"action": "hello", "device": "pc"})
        self.hello = ws.receive_json()
        self.addCleanup(ws.__exit__, None, None, None)
        return ws

    def test_hello_tells_a_new_screen_how_the_switch_really_is(self):
        self.ws(FakeMcMode("off"))
        self.assertIn("speak_key", self.hello)
        self.assertEqual(self.hello["mc_mode"], False)

    def test_one_click_brings_her_into_the_game_and_tells_the_screen_the_truth(self):
        fake = FakeMcMode("I'm in the game and I'll think for myself while you're busy. I'm in the overworld, at 1 64 1.")
        ws = self.ws(fake)
        ws.send_json({"action": "mc_mode", "on": True})
        self.assertEqual(ws.receive_json()["state"], "thinking")
        self.assertEqual(ws.receive_json(), {"mc_mode": True})
        spoken = ws.receive_json()
        self.assertEqual(spoken["state"], "speaking")
        self.assertIn("I'm in the game", spoken["text"])
        self.assertEqual(fake.calls, [True])

    def test_a_switch_that_fails_is_said_out_loud_and_the_button_goes_back(self):
        fake = FakeMcMode("Failed: the game didn't come up in time.", reaches=False)
        ws = self.ws(fake)
        ws.send_json({"action": "mc_mode", "on": True})
        self.assertEqual(ws.receive_json()["state"], "thinking")
        self.assertEqual(ws.receive_json(), {"mc_mode": False})
        self.assertEqual(ws.receive_json()["text"], "I couldn't do that. the game didn't come up in time.")

    def test_the_next_screen_sees_the_game_she_started(self):
        fake = FakeMcMode("I'm in the game.")
        ws = self.ws(fake)
        ws.send_json({"action": "mc_mode", "on": True})
        for _ in range(3):
            ws.receive_json()
        second = self.net.websocket_connect("/ws")
        second.__enter__()
        self.addCleanup(second.__exit__, None, None, None)
        second.send_json({"action": "hello", "device": "phone"})
        self.assertEqual(second.receive_json()["mc_mode"], True, "she kept the game she started")

    def test_turning_it_off_needs_no_game_at_all(self):
        fake = FakeMcMode("Minecraft mode is off - I'll keep my own goals to myself now.")
        ws = self.ws(fake)
        ws.send_json({"action": "mc_mode", "on": False})
        self.assertEqual(ws.receive_json()["state"], "thinking")
        self.assertEqual(ws.receive_json(), {"mc_mode": False})
        self.assertIn("off", ws.receive_json()["text"])
        self.assertEqual(fake.calls, [False])


if __name__ == "__main__":
    unittest.main()
