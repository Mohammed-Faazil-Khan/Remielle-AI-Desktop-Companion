"""The thinking loop, tested without the API and without the game.

Run with:  python -m unittest discover -s tests
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_brain  # noqa: E402
import intent_router  # noqa: E402
import minecraft_agent  # noqa: E402
import minecraft_planner as planner  # noqa: E402
import system_tools  # noqa: E402

SNAPSHOT = {
    "ok": True, "present": True,
    "me": {"name": "Remielle", "health": 20, "food": 19, "dead": False, "at": {"x": 10, "y": 64, "z": -3},
           "facing": "west", "holding": "oak_log", "wearing": {"head": "iron_helmet"}, "riding": None,
           "on_ground": True, "in_water": False, "sneaking": False, "sprinting": False,
           "busy_with": None, "mode": "idle"},
    "world": {"dimension": "overworld", "day": 3, "time_of_day": 6000, "is_day": True, "raining": False, "home": None},
    "owner": {"name": "Faazil", "distance": 4.2, "visible": True},
    "footing": {"below": {"name": "grass_block"}, "feet": {"name": "air"}, "head": {"name": "air"},
                "in_front": {"name": "dirt"}, "in_front_low": {"name": "dirt"}, "in_front_high": {"name": "air"}},
    "entities": [{"name": "oak_boat", "kind": "vehicle", "id": 41, "distance": 9.1, "at": {"x": 4, "y": 64, "z": -3}},
                 {"name": "zombie", "kind": "monster", "id": 55, "distance": 12.0, "at": {"x": 20, "y": 64, "z": 2},
                  "health": 20}],
    "blocks": [{"name": "oak_log", "distance": 12.4, "at": {"x": 22, "y": 63, "z": 5}, "count": 7},
               {"name": "crafting_table", "distance": 5.1, "at": {"x": 6, "y": 64, "z": -1}}],
    "inventory": [{"name": "oak_log", "count": 12}, {"name": "cobblestone", "count": 40}],
    "can_craft": {"now": ["oak_planks", "stick"], "atATable": ["chest"]},
}

MANUAL = "- mount: mount {\"target\":\"oak boat\"}\n- move: move {\"to\":[x,y,z]}"


def snapshot(**changes):
    snap = json.loads(json.dumps(SNAPSHOT))
    snap.update(changes)
    return snap


class ScriptedModel:
    """The fake 'brain': hands back one canned reply per call and records the prompt it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, messages, max_tokens):
        self.prompts.append(messages)
        if not self.replies:
            raise AssertionError("the loop asked more questions than the script has answers")
        return self.replies.pop(0)


class FakeBot:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def act(self, name, args):
        self.calls.append((name, args))
        if not self.results:
            return {"ok": True, "message": "done"}
        reply = self.results.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# ----------------------------------------------------------------- reading the model's answer

class ParseDecision(unittest.TestCase):
    def test_plain(self):
        d = planner.parse_decision('{"think":"walk over","action":"move","args":{"target":"owner"}}')
        self.assertEqual(d, {"think": "walk over", "action": "move", "args": {"target": "owner"}})

    def test_fenced_and_wrapped_in_prose(self):
        d = planner.parse_decision('Sure!\n```json\n{"action":"mount","args":{"target":"oak boat"}}\n```\n')
        self.assertEqual((d["action"], d["args"]["target"]), ("mount", "oak boat"))

    def test_done(self):
        d = planner.parse_decision('{"done":true,"say":"I am sitting in it!"}')
        self.assertTrue(d["done"])
        self.assertEqual(d["say"], "I am sitting in it!")

    def test_action_named_done_is_done(self):
        self.assertTrue(planner.parse_decision('{"action":"done","say":"all set"}')["done"])

    def test_finished_without_a_say_line(self):
        self.assertTrue(planner.parse_decision('{"think":"no boat here","action":"give_up"}')["done"])

    def test_arguments_as_a_json_string(self):
        d = planner.parse_decision('{"action":"dig","args":"{\\"at\\":[1,2,3]}"}')
        self.assertEqual(d["args"], {"at": [1, 2, 3]})

    def test_aliased_keys_and_casing(self):
        d = planner.parse_decision('{"Tool":"Craft","Parameters":{"item":"stone sword"}}')
        self.assertEqual((d["action"], d["args"]), ("craft", {"item": "stone sword"}))

    def test_space_in_verb(self):
        self.assertEqual(planner.parse_decision('{"action":"stone sword"}')["action"], "stone_sword")

    def test_junk_is_not_a_decision(self):
        for text in ("", "I have no idea", "{broken", "[1,2,3]", "{}"):
            with self.subTest(text=text):
                self.assertIsNone(planner.parse_decision(text))

    def test_say_without_action_counts_as_finished(self):
        self.assertTrue(planner.parse_decision('{"say":"the goal is impossible here"}')["done"])


# ----------------------------------------------------------------- the snapshot she is given

class DescribeView(unittest.TestCase):
    def test_covers_the_useful_facts(self):
        text = planner.describe_view(SNAPSHOT)
        for expected in ("10,64,-3", "facing west", "health 20/20", "oak log", "iron helmet",
                         "Faazil", "4.2m away", "oak boat (vehicle", "crafting table", "cobblestone x40",
                         "oak planks", "grass_block", "not set"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_survives_a_sparse_snapshot(self):
        text = planner.describe_view({"present": False})
        self.assertIsInstance(text, str)
        self.assertIn("ME:", text)

    def test_says_she_is_dead_and_airborne(self):
        snap = snapshot(me=dict(SNAPSHOT["me"], dead=True, on_ground=False))
        text = planner.describe_view(snap)
        self.assertIn("DEAD", text)
        self.assertIn("in mid-air", text)

    def test_no_owner_on_the_server(self):
        self.assertIn("OWNER: not on the server", planner.describe_view(snapshot(owner={})))


# ----------------------------------------------------------------- the loop

class RunGoal(unittest.TestCase):
    def test_two_steps_then_done(self):
        model = ScriptedModel(
            '{"think":"too far","action":"move","args":{"target":"oak boat"}}',
            '{"action":"mount","args":{"target":"oak boat"}}',
            '{"done":true,"say":"I am sitting in the boat!"}')
        bot = FakeBot({"ok": True, "message": "I'm next to the oak boat."},
                      {"ok": True, "message": "I'm sitting in the oak boat!"})
        trace = []
        said = []
        out = planner.run_goal("sit on the boat", ask=model, view=lambda: snapshot(), do=bot.act,
                               manual=MANUAL, announce=said.append, trace=trace)
        self.assertEqual(out, "I am sitting in the boat!")
        self.assertEqual([c[0] for c in bot.calls], ["move", "mount"])
        self.assertEqual(model.prompts[1][1]["content"].count("step 1:"), 1)
        self.assertIn("mount", trace[-2])
        self.assertTrue(said and "sit on the boat" in said[0])

    def test_the_second_question_contains_what_just_happened(self):
        model = ScriptedModel('{"action":"dig","args":{"at":[1,2,3]}}', '{"done":true,"say":"ok"}')
        bot = FakeBot({"ok": False, "message": "I need a shovel for that."})
        planner.run_goal("dig down", ask=model, view=lambda: snapshot(), do=bot.act,
                         manual=MANUAL, announce=lambda t: None)
        second = model.prompts[1][1]["content"]
        self.assertIn("FAILED: I need a shovel for that.", second)
        self.assertIn("GOAL: dig down", second)
        self.assertIn("- mount:", second)          # the bot's own manual, not a hardcoded copy
        self.assertIn("Steps left: 1", second)

    def test_the_first_question_has_no_log(self):
        model = ScriptedModel('{"done":true,"say":"easy"}')
        planner.run_goal("look around", ask=model, view=lambda: snapshot(), do=FakeBot().act,
                         manual=MANUAL, announce=lambda t: None)
        self.assertNotIn("LOG (", model.prompts[0][1]["content"])

    def test_not_in_the_world(self):
        model = ScriptedModel()
        out = planner.run_goal("build a house", ask=model,
                               view=lambda: {"ok": True, "present": False, "message": "I am not in the world."},
                               do=FakeBot().act, manual=MANUAL, announce=lambda t: None)
        self.assertTrue(out.startswith("Failed:"))
        self.assertIn("not in the world", out)
        self.assertEqual(model.prompts, [])

    def test_dead_she_cannot_think(self):
        out = planner.run_goal("build a house", ask=ScriptedModel(),
                               view=lambda: snapshot(me=dict(SNAPSHOT["me"], dead=True)),
                               do=FakeBot().act, manual=MANUAL, announce=lambda t: None)
        self.assertTrue(out.startswith("Failed:"))
        self.assertIn("respawn", out)

    def test_nonsense_replies_stop_cleanly(self):
        model = ScriptedModel("I would love to help!", "mount the boat please")
        bot = FakeBot()
        out = planner.run_goal("sit down", ask=model, view=lambda: snapshot(), do=bot.act,
                               manual=MANUAL, announce=lambda t: None)
        self.assertTrue(out.startswith("Failed:"))
        self.assertEqual(bot.calls, [])
        self.assertEqual(len(model.prompts), 2)

    def test_recovers_after_one_bad_reply(self):
        model = ScriptedModel("erm...", '{"done":true,"say":"got there"}')
        out = planner.run_goal("come here", ask=model, view=lambda: snapshot(), do=FakeBot().act,
                               manual=MANUAL, announce=lambda t: None)
        self.assertEqual(out, "got there")
        self.assertIn("not a usable decision", model.prompts[1][1]["content"])

    def test_runs_out_of_steps(self):
        model = ScriptedModel(*['{"action":"jump","args":{}}' for _ in range(3)])
        bot = FakeBot(*[{"ok": True, "message": "Hop!"} for _ in range(3)])
        out = planner.run_goal("climb the cliff", ask=model, view=lambda: snapshot(), do=bot.act,
                               manual=MANUAL, announce=lambda t: None, max_steps=3)
        self.assertIn("all 3 steps", out)
        self.assertIn("Hop!", out)

    def test_done_without_words_is_not_a_success(self):
        out = planner.run_goal("sit down", ask=ScriptedModel('{"done":true}'), view=lambda: snapshot(),
                               do=FakeBot().act, manual=MANUAL, announce=lambda t: None)
        self.assertTrue(out.startswith("Failed:"))

    def test_a_crashing_brain_is_a_sentence_not_an_exception(self):
        def boom(messages, max_tokens):
            raise ConnectionError("nvidia is down")
        out = planner.run_goal("sit down", ask=boom, view=lambda: snapshot(), do=FakeBot().act,
                               manual=MANUAL, announce=lambda t: None)
        self.assertTrue(out.startswith("Failed:"))

    def test_a_crashing_bot_stops_the_run(self):
        model = ScriptedModel('{"action":"mount","args":{}}', '{"done":true,"say":"stopped"}')
        bot = FakeBot(ConnectionResetError("bot died"))
        trace = []
        planner.run_goal("sit down", ask=model, view=lambda: snapshot(), do=bot.act,
                         manual=MANUAL, announce=lambda t: None, trace=trace)
        self.assertIn("FAILED", trace[0])

    def test_an_empty_goal_does_nothing(self):
        model = ScriptedModel()
        self.assertTrue(planner.run_goal("   ", ask=model, view=lambda: snapshot(),
                                         do=FakeBot().act).startswith("Failed:"))
        self.assertEqual(model.prompts, [])

    def test_time_budget_is_respected(self):
        model = ScriptedModel('{"action":"jump","args":{}}')
        with mock.patch.object(planner, "GOAL_SECONDS", -1.0):
            out = planner.run_goal("jump a lot", ask=model, view=lambda: snapshot(), do=FakeBot().act,
                                   manual=MANUAL, announce=lambda t: None)
        self.assertIn("ran out of time", out)


# ----------------------------------------------------------------- the transport and the wiring

class AgentTransport(unittest.TestCase):
    def test_observe_passes_the_snapshot_through(self):
        with mock.patch.object(minecraft_agent, "_request", return_value=SNAPSHOT) as rq:
            self.assertEqual(minecraft_agent.observe()["present"], True)
            self.assertEqual(rq.call_args.args[0], "/observe")

    def test_observe_when_the_bot_is_offline(self):
        with mock.patch.object(minecraft_agent, "_request", return_value=None):
            self.assertEqual(minecraft_agent.observe(),
                             {"ok": False, "present": False, "message": "the Minecraft bot is not answering"})

    def test_act_sends_the_action_and_args(self):
        with mock.patch.object(minecraft_agent, "_request", return_value={"ok": True}) as rq:
            minecraft_agent.act("Mount", {"target": "oak boat"})
            self.assertEqual(rq.call_args.args[1], {"action": "mount", "args": {"target": "oak boat"}})

    def test_act_without_a_bot(self):
        with mock.patch.object(minecraft_agent, "_request", return_value=None):
            out = minecraft_agent.act("mount", {})
            self.assertFalse(out["ok"])
            self.assertTrue(out["message"].startswith("Error:"))

    def test_act_needs_a_name(self):
        with mock.patch.object(minecraft_agent, "_request") as rq:
            out = minecraft_agent.act("  ")
            self.assertFalse(out["ok"])
            rq.assert_not_called()

    def test_help_and_note(self):
        with mock.patch.object(minecraft_agent, "_request", return_value={"ok": True, "help": "- mount: x"}):
            self.assertEqual(minecraft_agent.action_help(), "- mount: x")
        with mock.patch.object(minecraft_agent, "_request", return_value={"ok": True}) as rq:
            self.assertTrue(minecraft_agent.note("working on it"))
            self.assertEqual(rq.call_args.args[0], "/note")

    def test_note_never_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no socket")):
            self.assertFalse(minecraft_agent.note("hello"))

    def test_observe_and_act_survive_a_dead_socket(self):
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
            self.assertFalse(minecraft_agent.observe()["present"])
            self.assertFalse(minecraft_agent.act("mount", {})["ok"])
            self.assertEqual(minecraft_agent.action_help(), "")


class Wiring(unittest.TestCase):
    def test_the_goal_tool_is_registered(self):
        from system_tools import AVAILABLE_FUNCTIONS
        self.assertIn("minecraft_goal", AVAILABLE_FUNCTIONS)

    def test_the_goal_tool_reports_failures_as_sentences(self):
        import system_tools
        with mock.patch("minecraft_planner.run_goal", side_effect=RuntimeError("kaboom")):
            out = system_tools.AVAILABLE_FUNCTIONS["minecraft_goal"]("build a hut")
        self.assertTrue(out.startswith("Failed:"))
        self.assertIn("kaboom", out)

    def test_moods_follow_what_happened(self):
        ok = intent_router.describe_result("minecraft_goal", {"goal": "sit"}, "I am sitting in the boat!")
        bad = intent_router.describe_result("minecraft_goal", {"goal": "sit"}, "Failed: I could not find a boat.")
        self.assertTrue(ok.startswith("[happy]"))
        self.assertTrue(bad.startswith("[sad]"))
        self.assertNotIn("Failed:", bad)

    def test_the_brain_knows_the_goal_verb(self):
        import agent_brain
        names = [t["function"]["name"] for t in agent_brain.tool_schemas]
        self.assertIn("minecraft_goal", names)
        self.assertIn(("GOAL", "minecraft_goal", "goal"), agent_brain._TEXT_ACTIONS)
        self.assertIn("GOAL:", agent_brain.SYSTEM_RULES)

    def test_a_goal_line_from_the_model_runs_the_loop(self):
        import agent_brain
        with mock.patch("minecraft_planner.run_goal", return_value="I am in the boat") as rg:
            found = agent_brain._find_text_action("[happy] Watch me! GOAL: sit on the boat")
            self.assertEqual(found, ("minecraft_goal", {"goal": "sit on the boat"}))
            out = agent_brain._execute_from_reply("[happy] GOAL: sit on the boat", None)
        rg.assert_called_once_with("sit on the boat")
        self.assertTrue(out.startswith("[happy]"))

    def test_a_mc_line_still_uses_the_fast_path(self):
        import agent_brain
        self.assertEqual(agent_brain._find_text_action("[happy] MC: follow"),
                         ("minecraft_command", {"command": "follow"}))


# ----------------------------------------------------------------- thinking on her own

class Initiative(unittest.TestCase):
    def setUp(self):
        import time as _t
        planner._state.update(on=False, running=False, quiet_since=_t.monotonic(), last_run=0.0, last_goal="")

    def quiet(self):
        """Pretend the user has not said anything for a while."""
        import time as _t
        planner._state["quiet_since"] = _t.monotonic() - planner.IDLE_SECONDS - 1

    def test_the_switch_has_three_answers(self):
        self.assertTrue(planner.set_mode("on").startswith("I'll think for myself"))
        self.assertTrue(planner.is_on())
        self.assertIn("on", planner.set_mode("status"))
        self.assertTrue(planner.set_mode("off").startswith("Stopped."))
        self.assertFalse(planner.is_on())
        self.assertTrue(planner.set_mode("maybe").startswith("Failed:"))

    def test_switching_on_twice_does_not_repeat_itself(self):
        planner.set_mode("on")
        self.assertIn("what I was doing", planner.set_mode("start"))

    def test_loose_wordings_still_land_on_the_right_side(self):
        planner.set_mode("off")
        planner.set_mode("turn it on, dear")
        self.assertTrue(planner.is_on())
        self.assertTrue(planner.set_mode("sideways").startswith("Failed:"))
        self.assertTrue(planner.is_on(), "an unrecognisable mode word changes nothing")
        planner.set_mode("do not do that any more")
        self.assertFalse(planner.is_on())
        planner.set_mode("on")
        answer = planner.set_mode("do not turn it on")
        self.assertFalse(answer.startswith("I'll think for myself"),
                         'a sentence that says "not" must never switch her on')
        self.assertFalse(planner.is_on())

    def test_speaking_resets_the_quiet_clock(self):
        planner.set_mode("on")
        self.quiet()
        planner.user_spoke()
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=ScriptedModel(), busy=lambda: False), "")

    def test_off_means_she_never_asks_anything(self):
        model = ScriptedModel()
        self.quiet()
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=model, busy=lambda: False), "")
        self.assertEqual(model.prompts, [])

    def test_a_busy_game_blocks_a_self_chosen_goal(self):
        planner.set_mode("on")
        self.quiet()
        model = ScriptedModel()
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=model, busy=lambda: True), "")
        self.assertEqual(model.prompts, [])

    def test_she_waits_for_the_cooldown_between_projects(self):
        planner.set_mode("on")
        self.quiet()
        planner.tick(view=lambda: snapshot(), ask=ScriptedModel('{"goal":null,"why":"all fine"}'),
                     busy=lambda: False, manual=MANUAL)
        self.quiet()                      # still quiet, but she just thought about it
        model = ScriptedModel()
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=model, busy=lambda: False), "")
        self.assertEqual(model.prompts, [])

    def test_she_can_decide_to_leave_it_alone(self):
        planner.set_mode("on")
        self.quiet()
        model = ScriptedModel('{"goal":null,"why":"everything is tidy"}')
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=model, busy=lambda: False,
                                      manual=MANUAL), "")
        self.assertIn("ACTIONS:", model.prompts[0][1]["content"])
        self.assertIn("oak boat", model.prompts[0][1]["content"])

    def test_a_self_chosen_goal_is_run_to_the_end(self):
        planner.set_mode("on")
        self.quiet()
        model = ScriptedModel('{"goal":"collect 5 oak logs"}',
                              '{"action":"collect","args":{"block":"oak log","count":5}}',
                              '{"done":true,"say":"five logs, all mine"}')
        bot = FakeBot({"ok": True, "message": "Collected 5 oak log."})
        out = planner.tick(view=lambda: snapshot(), ask=model, do=bot.act, busy=lambda: False,
                           announce=lambda t: None, manual=MANUAL)
        self.assertEqual(out, "On my own, I did this: five logs, all mine")
        self.assertEqual(planner._state["last_goal"], "collect 5 oak logs")
        self.assertFalse(planner._state["running"])

    def test_she_stops_the_moment_he_speaks(self):
        planner.set_mode("on")
        self.quiet()
        model = ScriptedModel('{"goal":"build a hut"}', '{"action":"place","args":{"block":"oak planks"}}',
                              '{"action":"place","args":{"block":"oak planks"}}')
        bot = FakeBot({"ok": True, "message": "Placed oak planks."})

        def ask(messages, max_tokens):
            if len(model.prompts) == 1:      # she is halfway through when he says something
                planner.user_spoke()
            return model(messages, max_tokens)

        out = planner.tick(view=lambda: snapshot(), ask=ask, do=bot.act, busy=lambda: False,
                           announce=lambda t: None, manual=MANUAL)
        self.assertEqual(out, "I stopped that one and came straight back to you.")
        self.assertEqual(len(bot.calls), 1)

    def test_a_crash_in_the_picker_is_silence_not_a_stack_trace(self):
        planner.set_mode("on")
        self.quiet()

        def broken(messages, max_tokens):
            raise ConnectionError("nvidia down")
        self.assertEqual(planner.tick(view=lambda: snapshot(), ask=broken, busy=lambda: False,
                                      manual=MANUAL), "")
        self.assertFalse(planner._state["running"])

    def test_parse_goal(self):
        self.assertEqual(planner.parse_goal('{"goal":"chop some wood","why":"we are short"}'), "chop some wood")
        self.assertEqual(planner.parse_goal('```json\n{"plan":"cook"}\n```'), "cook")
        for junk in ('{"goal":null}', '{"goal":"none"}', '{"goal":""}', '{"why":"all tidy"}', 'not json', '{}',
                     '{"goal":true}'):
            with self.subTest(junk=junk):
                self.assertIsNone(planner.parse_goal(junk))


class RunGoalAbort(unittest.TestCase):
    def test_abort_is_checked_before_every_step(self):
        model = ScriptedModel('{"action":"dig","args":{"at":[0,63,0]}}')
        bot = FakeBot({"ok": True, "message": "dug"})
        out = planner.run_goal("dig", ask=model, view=lambda: snapshot(), do=bot.act, manual=MANUAL,
                               announce=lambda t: None, abort=lambda: True)
        self.assertEqual(out, planner.ABORTED)
        self.assertEqual(model.prompts, [])
        self.assertEqual(bot.calls, [])


class InitiativeVoice(unittest.TestCase):
    PHRASES = {
        "explore on your own": "on", "go explore": "on", "be proactive": "on", "use your initiative": "on",
        "think for yourself": "on", "do your own thing": "on", "hey remielle surprise me": "on",
        "can you keep yourself busy": "on", "go on your own": "on",
        "stop exploring": "off", "don't be proactive": "off", "turn off your initiative": "off",
        "quit being on your own": "off", "do not think for yourself": "off",
        "are you on your own": "status", "initiative status": "status",
        "follow me": None, "stop": None, "jump": None, "collect wood": None, "where are you": None,
        "craft a chest": None, "guard me": None, "open youtube": None, "take a screenshot": None,
        "come here": None, "what's in your inventory": None, "explore wikipedia": None,
    }

    def test_phrases(self):
        for text, expected in self.PHRASES.items():
            with self.subTest(text=text):
                self.assertEqual(intent_router.match_initiative(text), expected)

    def test_the_router_reaches_the_tool_even_offline(self):
        with mock.patch.object(intent_router, "_minecraft_active", return_value=False):
            self.assertEqual(intent_router.match_intent("explore on your own"),
                             ("minecraft_initiative", {"mode": "on"}))

    def test_a_normal_game_command_still_goes_to_the_game(self):
        with mock.patch.object(intent_router, "_minecraft_active", return_value=True):
            self.assertEqual(intent_router.match_intent("collect 10 oak logs")[0], "minecraft_command")

    def test_moods_follow_what_happened(self):
        ok = intent_router.describe_result("minecraft_initiative", {"mode": "on"}, "I'll think for myself now.")
        bad = intent_router.describe_result("minecraft_initiative", {"mode": "sideways"}, "Failed: on or off.")
        self.assertTrue(ok.startswith("[happy]"))
        self.assertTrue(bad.startswith("[sad]"))

    def test_the_tool_is_registered_and_never_raises(self):
        self.assertIn("minecraft_initiative", system_tools.AVAILABLE_FUNCTIONS)
        with mock.patch("minecraft_planner.set_mode", side_effect=RuntimeError("boom")):
            self.assertTrue(system_tools.minecraft_initiative("on").startswith("Failed:"))

    def test_the_brain_knows_the_verb(self):
        self.assertIn(("INITIATIVE", "minecraft_initiative", "mode"), agent_brain._TEXT_ACTIONS)
        self.assertIn("INITIATIVE:", agent_brain.SYSTEM_RULES)
        self.assertIn("minecraft_initiative", [t["function"]["name"] for t in agent_brain.tool_schemas])
        with mock.patch("minecraft_planner.set_mode", return_value="I'll think for myself now.") as sm:
            out = agent_brain._execute_from_reply("[happy] INITIATIVE: on", None)
        sm.assert_called_once_with("on")
        self.assertTrue(out.startswith("[happy]"))


if __name__ == "__main__":
    unittest.main()
