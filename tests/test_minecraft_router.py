"""Run with:  python -m unittest discover -s tests   (no network, no game needed)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_router as router  # noqa: E402
import minecraft_agent  # noqa: E402


class MatchMinecraft(unittest.TestCase):
    CASES = {
        "follow me": "follow", "Remielle, come here": "come", "stop": "stop", "hey remielle guard me please": "guard",
        "go to 10 64 -20": "goto 10 64 -20", "kill the zombies": "attack zombies", "mine some iron": "collect iron",
        "get me 10 oak logs": "collect 10 oak logs", "craft a pickaxe": "craft pickaxe",
        "give me 5 cobblestone": "give 5 cobblestone", "what's in your inventory": "inventory", "go home": "home",
        "go to sleep": "sleep", "tell everyone hello there": "say hello there", "chop wood in minecraft": "collect wood",
        "collect a file from my folder": None, "play some music": None, "open youtube": None, "make a project file": None,
    }

    def test_phrases(self):
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(router.match_minecraft(text), expected)

    def test_not_claimed_when_not_in_the_game(self):
        with mock.patch.object(router, "_minecraft_active", return_value=False):
            self.assertIsNone(router.match_intent("follow me"))
            self.assertEqual(router.match_intent("open youtube")[0], "open_target")

    def test_claimed_when_in_the_game(self):
        with mock.patch.object(router, "_minecraft_active", return_value=True):
            self.assertEqual(router.match_intent("follow me"), ("minecraft_command", {"command": "follow"}))

    def test_replies_are_honest(self):
        ok = router.describe_result("minecraft_command", {"command": "follow"}, "Following you!")
        bad = router.describe_result("minecraft_command", {"command": "follow"}, "Error: I can't see Faazil right now.")
        self.assertTrue(ok.startswith("[happy]"))
        self.assertTrue(bad.startswith("[sad]"))
        self.assertNotIn("Error:", bad)


class MatchMinecraftMode(unittest.TestCase):
    """The one toggle: the game, her bot and her own goals together."""

    CASES = {
        "minecraft mode on": "on", "turn on minecraft": "on", "turn the game on": "on",
        "switch on minecraft mode": "on", "join the game": "on", "come into the world": "on",
        "play minecraft with me": "on", "start minecraft": "on", "bring yourself into the game": "on",
        "get in the game": "on", "minecraft mode off": "off", "turn off the game": "off",
        "stop playing minecraft": "off", "leave the game": "off", "quit minecraft": "off",
        "go back out of the game": "off", "what's the minecraft mode": "status",
        "are you in the game": "status", "minecraft mode status": "status", "minecraft mode": "status",
        "follow me": None, "open youtube": None, "collect wood in minecraft": None,
        "explore on your own": None, "stop exploring": None, "play some music": None,
        "go to sleep": None, "come here": None, "take a screenshot": None,
    }

    def test_phrases(self):
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(router.match_mc_mode(text), expected)

    def test_the_toggle_is_reachable_from_a_plain_sentence(self):
        self.assertEqual(router.match_intent("join me in the game"), ("minecraft_mode", {"mode": "on"}))
        self.assertEqual(router.match_intent("Remielle, minecraft mode off"), ("minecraft_mode", {"mode": "off"}))

    def test_mode_beats_initiative_when_both_fit(self):
        """"play minecraft on your own" is the whole switch, not only the goals half of it."""
        self.assertEqual(router.match_intent("play minecraft on your own"), ("minecraft_mode", {"mode": "on"}))

    def test_replies_are_honest(self):
        ok = router.describe_result("minecraft_mode", {"mode": "on"}, "I'm in the game. I'm in the overworld, at 1 64 1.")
        slow = router.describe_result("minecraft_mode", {"mode": "on"}, "Failed: the game didn't come up in time.")
        off = router.describe_result("minecraft_mode", {"mode": "off"}, "Minecraft mode is off.")
        self.assertTrue(ok.startswith("[happy]"), ok)
        self.assertTrue(slow.startswith("[sad]"), slow)
        self.assertNotIn("Failed:", slow)
        self.assertTrue(off.startswith("[relaxed]"), off)


class AgentClient(unittest.TestCase):
    def test_bot_offline_gives_a_sentence_not_an_exception(self):
        with mock.patch.object(minecraft_agent, "_request", return_value=None):
            self.assertTrue(minecraft_agent.send_command("follow").startswith("Error:"))
            self.assertFalse(minecraft_agent.say("hi"))
            self.assertEqual(minecraft_agent.poll_events(), [])
            minecraft_agent._cache["at"] = 0.0
            self.assertFalse(minecraft_agent.is_connected(max_age=0))
            self.assertEqual(minecraft_agent.status_summary(), "")

    def test_empty_command(self):
        self.assertTrue(minecraft_agent.send_command("  ").startswith("Error:"))

    def test_first_poll_does_not_replay_history(self):
        minecraft_agent.reset_event_marker()
        replies = [{"ok": True, "last": 7, "events": [{"type": "old"}]}, {"ok": True, "last": 8, "events": [{"type": "new"}]}]
        with mock.patch.object(minecraft_agent, "_request", side_effect=replies):
            self.assertEqual(minecraft_agent.poll_events(), [])
            self.assertEqual([e["type"] for e in minecraft_agent.poll_events()], ["new"])


if __name__ == "__main__":
    unittest.main()
