"""Tests for the screens she talks through: who speaks, who stays quiet, whose microphone she uses."""
import unittest

import clients
from clients import Client, Hub


class FakeSocket:
    """A page: remembers what she sent and can be told to die on the next message."""

    def __init__(self, name="sock", broken=False):
        self.name = name
        self.sent = []
        self.broken = broken

    async def send_json(self, payload):
        if self.broken:
            raise RuntimeError("connection is gone")
        self.sent.append(payload)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def hub_with(*specs):
    """specs like ("pc", True) -> a Hub of those clients."""
    hub = Hub(now=Clock())
    made = []
    for device, audio in specs:
        made.append(hub.add(FakeSocket(device), device, audio))
    return hub, made


class WhoSpeaks(unittest.IsolatedAsyncioTestCase):
    async def test_the_screen_that_asked_her_is_the_one_that_talks(self):
        hub, (pc, phone) = hub_with(("pc", True), ("phone", True))
        hub.asked(phone)
        await hub.send({"state": "speaking", "text": "hello"})
        self.assertEqual(phone.websocket.sent[0]["speak"], True)
        self.assertEqual(pc.websocket.sent[0]["speak"], False)
        self.assertEqual(pc.websocket.sent[0]["text"], "hello", "the muted screen still shows the words")

    async def test_the_desktop_speaks_when_nobody_asked(self):
        hub, (pc, phone) = hub_with(("pc", True), ("phone", True))
        await hub.send({"state": "speaking", "text": "danger nearby"})
        self.assertEqual(pc.websocket.sent[0]["speak"], True)
        self.assertEqual(phone.websocket.sent[0]["speak"], False)

    async def test_a_screen_that_asked_for_silence_never_gets_the_voice(self):
        hub, (pc, mute) = hub_with(("pc", True), ("web", False))
        hub.asked(mute)
        await hub.send({"state": "speaking", "text": "hi"})
        self.assertEqual(pc.websocket.sent[0]["speak"], True, "some screen has to say it")
        self.assertEqual(mute.websocket.sent[0]["speak"], False)

    async def test_only_one_screen_ever_gets_speak_true(self):
        hub, made = hub_with(("pc", True), ("phone", True), ("phone", True))
        hub.asked(made[1])
        await hub.send({"state": "speaking", "text": "hi"})
        flags = [c.websocket.sent[0].get("speak") for c in made]
        self.assertEqual(flags.count(True), 1)
        self.assertEqual(flags[1], True)

    async def test_state_changes_are_not_marked_with_a_voice_owner(self):
        hub, (pc, phone) = hub_with(("pc", True), ("phone", True))
        await hub.send({"state": "idle", "text": ""})
        self.assertNotIn("speak", pc.websocket.sent[0])
        self.assertNotIn("speak", phone.websocket.sent[0])

    async def test_one_screen_only(self):
        hub, (pc, phone) = hub_with(("pc", True), ("phone", True))
        await hub.send({"state": "listening", "text": ""}, only=phone)
        self.assertEqual(pc.websocket.sent, [])
        self.assertEqual(len(phone.websocket.sent), 1)
        self.assertNotIn("speak", phone.websocket.sent[0])

    async def test_no_screens_means_nothing_is_sent(self):
        hub = Hub(now=Clock())
        self.assertEqual(await hub.send({"state": "speaking", "text": "hello"}), 0)
        self.assertIsNone(hub.voice())

    def test_voice_moves_when_the_holder_closes(self):
        hub, (pc, phone) = hub_with(("pc", True), ("phone", True))
        hub.asked(phone)
        hub.drop(phone)
        self.assertIs(hub.voice(), pc)


class ScreensComeAndGo(unittest.IsolatedAsyncioTestCase):
    async def test_a_dead_screen_is_dropped_not_raised(self):
        hub = Hub(now=Clock())
        good = hub.add(FakeSocket("good"), "pc", True)
        bad = hub.add(FakeSocket("bad", broken=True), "phone", True)
        self.assertEqual(await hub.send({"state": "speaking", "text": "hi"}), 1)
        self.assertEqual(len(hub), 1)
        self.assertIsNone(hub.find(bad.websocket))
        self.assertIs(hub.find(good.websocket), good)

    def test_dropping_a_screen_empties_its_microphone(self):
        hub, (pc,) = hub_with(("pc", True))
        pc.start_remote()
        pc.feed(b"audio")
        hub.drop(pc)
        self.assertFalse(pc.recording)
        self.assertEqual(pc.frames, [])
        self.assertEqual(pc.take_remote(), b"")

    def test_the_device_name_is_never_nonsense(self):
        client = Client(FakeSocket(), "abc", device="smartfridge", audio=1)
        self.assertEqual(client.device, "web")
        self.assertTrue(client.wants_audio)


class HerOwnMicrophone(unittest.TestCase):
    def test_audio_only_arrives_while_she_is_recording(self):
        client = Client(FakeSocket(), "abc", "phone")
        self.assertFalse(client.feed(b"too early"))
        client.start_remote()
        self.assertTrue(client.feed(b"voice"))
        self.assertEqual(client.take_remote(), b"voice")
        self.assertFalse(client.feed(b"after"))

    def test_a_second_press_starts_a_clean_buffer(self):
        client = Client(FakeSocket(), "abc", "phone")
        client.start_remote()
        client.feed(b"one")
        client.start_remote()
        self.assertEqual(client.take_remote(), b"", "the old words must not leak into the new question")

    def test_junk_frames_are_ignored(self):
        client = Client(FakeSocket(), "abc", "phone")
        client.start_remote()
        for junk in (b"", None, "text", 12):
            self.assertFalse(client.feed(junk), repr(junk))
        self.assertEqual(client.take_remote(), b"")

    def test_fifteen_seconds_is_the_cap(self):
        client = Client(FakeSocket(), "abc", "phone")
        client.start_remote()
        self.assertTrue(client.feed(b"x" * (clients.REMOTE_LIMIT_BYTES - 10)))
        self.assertTrue(client.feed(b"x" * 10), "filling the buffer exactly is still inside the cap")
        self.assertEqual(len(client.take_remote()), clients.REMOTE_LIMIT_BYTES)

    def test_a_full_buffer_stops_instead_of_dropping_silently(self):
        client = Client(FakeSocket(), "abc", "phone")
        client.start_remote()
        client.feed(b"x" * clients.REMOTE_LIMIT_BYTES)
        self.assertTrue(client.recording)
        self.assertFalse(client.feed(b"x"))
        self.assertFalse(client.recording, "recording stops so the screen can tell you")
        self.assertEqual(len(client.take_remote()), clients.REMOTE_LIMIT_BYTES)


class BusyScreens(unittest.TestCase):
    def test_a_screen_holding_a_turn_blocks_the_next_one(self):
        clock = Clock()
        hub = Hub(now=clock)
        pc = hub.add(FakeSocket(), "pc", True)
        phone = hub.add(FakeSocket(), "phone", True)
        self.assertFalse(hub.busy())
        hub.hold_voice(pc, 90)
        self.assertTrue(hub.busy())
        hub.clear_busy(pc)
        self.assertFalse(hub.busy())
        hub.hold_voice(phone, 5)
        self.assertTrue(hub.busy())
        clock.advance(6)
        self.assertFalse(hub.busy(), "a screen that vanished mid-sentence is not busy forever")

    def test_clearing_the_wrong_screen_does_not_free_the_busy_one(self):
        clock = Clock()
        hub = Hub(now=clock)
        pc, phone = hub.add(FakeSocket(), "pc", True), hub.add(FakeSocket(), "phone", True)
        hub.hold_voice(phone, 90)
        hub.clear_busy(pc)
        self.assertTrue(hub.busy())

    def test_an_unknown_screen_changes_nothing(self):
        hub = Hub(now=Clock())
        hub.hold_voice(None, 90)
        hub.clear_busy(None)
        hub.asked(None)
        self.assertFalse(hub.busy())


if __name__ == "__main__":
    unittest.main()
