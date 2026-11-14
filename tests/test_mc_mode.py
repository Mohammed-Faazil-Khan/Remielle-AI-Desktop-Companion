"""Minecraft mode: the one switch that brings her into the game and lets her play on her own."""
import unittest

import mc_mode


class Recorder:
    """A fake that remembers its calls and answers from a list, repeating the last answer."""

    def __init__(self, *results):
        self.results = list(results) or [True]
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


SNAP = {"ok": True, "me": {"at": {"x": 12.44, "y": 64.0, "z": -8.5}}, "world": {"dimension": 0}}


class TheSwitch(unittest.TestCase):
    def setUp(self):
        mc_mode._mode["on"] = False

    def test_turning_it_on_starts_the_game_and_thinks_for_herself(self):
        initiative = Recorder("ok")
        answer = mc_mode.switch(True, wait=Recorder(True),
                                place=Recorder("the overworld, at 12.4 64.0 -8.5"), initiative=initiative)
        self.assertTrue(mc_mode.is_on())
        self.assertEqual(initiative.calls, [(("on",), {})])
        self.assertIn("I'm in the game", answer)
        self.assertIn("12.4 64.0 -8.5", answer, "she says where she landed")

    def test_a_world_she_cannot_describe_is_still_a_world_she_is_in(self):
        answer = mc_mode.switch(True, wait=Recorder(True), place=Recorder(""), initiative=Recorder("ok"))
        self.assertTrue(mc_mode.is_on())
        self.assertEqual(answer, "I'm in the game and I'll think for myself while you're busy.",
                         "no place, so no made-up coordinates")

    def test_the_game_is_started_once_and_she_waits_again_for_it(self):
        wait, start = Recorder(False, True), Recorder(True)
        mc_mode.switch(True, wait=wait, start=start, place=Recorder(""), initiative=Recorder("ok"))
        self.assertEqual(len(wait.calls), 2)
        self.assertEqual(len(start.calls), 1)
        self.assertTrue(mc_mode.is_on())

    def test_she_only_looks_briefly_before_starting_the_game(self):
        wait = Recorder(False, True)
        mc_mode.switch(True, wait=wait, start=Recorder(True), place=Recorder(""), initiative=Recorder("ok"))
        self.assertEqual(wait.calls[0], ((mc_mode.FIRST_LOOK_SECONDS,), {}),
                         "a cold start must not sit silent for the whole budget first")
        self.assertEqual(wait.calls[1], ((mc_mode.READY_SECONDS,), {}))

    def test_a_game_that_never_comes_up_leaves_the_switch_off(self):
        initiative = Recorder("ok")
        answer = mc_mode.switch(True, wait=Recorder(False, False), start=Recorder(True),
                                place=Recorder(""), initiative=initiative)
        self.assertFalse(mc_mode.is_on())
        self.assertIn("didn't come up", answer)
        self.assertEqual(initiative.calls, [], "she never promises goals she cannot reach")

    def test_a_missing_launcher_is_reported_instead_of_a_crash(self):
        answer = mc_mode.switch(True, wait=Recorder(False), start=Recorder(False),
                                place=Recorder(""), initiative=Recorder("ok"))
        self.assertFalse(mc_mode.is_on())
        self.assertIn("Launch_Minecraft.bat", answer)

    def test_turning_it_off_only_stops_the_goals(self):
        initiative, start = Recorder("ok"), Recorder(True)
        answer = mc_mode.switch(False, start=start, wait=Recorder(True),
                                place=Recorder(""), initiative=initiative)
        self.assertFalse(mc_mode.is_on())
        self.assertEqual(initiative.calls, [(("off",), {})])
        self.assertEqual(start.calls, [], "a running world is not shut down by a word")
        self.assertIn("off", answer)


class TheWords(unittest.TestCase):
    """People say the switch in sentences, not in booleans."""

    def setUp(self):
        mc_mode._mode["on"] = False
        self.calls = []
        self.real_switch = mc_mode.switch
        mc_mode.switch = self._switch
        self.addCleanup(setattr, mc_mode, "switch", self.real_switch)

    def _switch(self, on):
        self.calls.append(on)
        mc_mode._mode["on"] = bool(on)
        return "off" if not on else "on"

    def test_the_usual_words(self):
        for word in ("on", "turn it on", "join the game", "play minecraft by yourself", "come into the world"):
            self.calls.clear()
            mc_mode.set_mode(word)
            self.assertEqual(self.calls, [True], word)

        for word in ("off", "stop that", "leave the game", "quit minecraft", "go back to sleep"):
            self.calls.clear()
            mc_mode.set_mode(word)
            self.assertEqual(self.calls, [False], word)

    def test_a_no_never_turns_her_on(self):
        for word in ("do not turn it on", "don't join the game", "no minecraft mode"):
            self.calls.clear()
            answer = mc_mode.set_mode(word)
            self.assertEqual(self.calls, [], word)
            self.assertTrue(answer.startswith("Failed:"), word)

    def test_a_word_she_does_not_know_changes_nothing(self):
        self.calls.clear()
        self.assertTrue(mc_mode.set_mode("sideways").startswith("Failed:"))
        self.assertEqual(self.calls, [])

    def test_status_asks_without_touching_the_switch(self):
        self.calls.clear()
        mc_mode.set_mode("minecraft mode status")
        self.assertEqual(self.calls, [])

    def test_status_says_where_she_is_when_the_mode_is_on(self):
        real_where = mc_mode._where
        mc_mode._where = lambda snap=None: "the Nether, at 1 2 3"
        self.addCleanup(setattr, mc_mode, "_where", real_where)
        mc_mode._mode["on"] = True
        self.assertEqual(mc_mode.describe(), "Minecraft mode is on, and I'm in the Nether, at 1 2 3.")
        mc_mode._mode["on"] = False
        self.assertEqual(mc_mode.describe(), "Minecraft mode is off.")


class HerEyes(unittest.TestCase):
    def test_a_snapshot_becomes_one_short_spoken_place(self):
        self.assertEqual(mc_mode._where(SNAP), "the overworld, at 12.4 64.0 -8.5")

    def test_dimensions_have_names(self):
        for dimension, name in ((-1, "the Nether"), (1, "the End")):
            self.assertIn(name, mc_mode._where({"me": {"at": {"x": 0, "y": 0, "z": 0}},
                                                "world": {"dimension": dimension}}))

    def test_no_position_is_no_sentence(self):
        self.assertEqual(mc_mode._where({"ok": False, "message": "the bot is not answering"}), "")
        self.assertEqual(mc_mode._where({"me": {"at": None}, "world": {}}), "")

    def test_a_junk_coordinate_still_gives_her_the_dimension(self):
        self.assertEqual(mc_mode._where({"me": {"at": {"x": "somewhere"}}, "world": {"dimension": 0}}),
                         "the overworld")


if __name__ == "__main__":
    unittest.main()
