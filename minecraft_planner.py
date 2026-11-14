"""Remielle's Minecraft thinking loop.

The voice commands in intent_router.py and the "MC:" line in agent_brain.py are a fixed vocabulary:
they only reach what somebody thought to program. This module is the part that isn't a vocabulary. The
bot exposes two things — GET /observe (what she sees, holds and can reach) and POST /act (one verb:
mount, dig, place, craft, equip, drive, ...) — and here a model is asked, once per step, to pick the
next verb from the bot's own live manual, then reads what actually happened and decides again.

That means "sit on the boat" works without anyone writing a boat handler: she looks, walks over, sits.

The bottom half of this file is the "light initiative" the user can switch on: after the PC has been quiet
for a while she asks the model what is worth doing, and runs that as a shorter goal. "explore on your own"
turns it on, "stop exploring" turns it off, and anything said to her mid-goal stops her right there.

Nothing here raises. Every exit is a short speakable sentence, starting with "Failed:" when the goal
did not happen, which is how intent_router.describe_result picks her mood.

Diagnostics (from the project folder, bot must be running and in the world):
    python minecraft_planner.py "collect 5 oak logs and craft a crafting table"
"""
import json
import re
import time

from remielle_config import env_float, env_int

MAX_STEPS = env_int("REMIELLE_MC_STEPS", 12)          # one step = one model call + one action
STEP_TOKENS = env_int("REMIELLE_MC_STEP_TOKENS", 320)  # a decision is a dozen words; thinking is off
GOAL_SECONDS = env_float("REMIELLE_MC_GOAL_SECONDS", 300)
LOG_KEEP = env_int("REMIELLE_MC_LOG_KEEP", 8)          # lines of history the model gets to re-read

ABORTED = "I left that one for now."   # run_goal says this when she was interrupted mid-goal

PLANNER_RULES = (
    "You are Remielle, an AI playing Minecraft as a second player on Faazil's server. You have a body in "
    "that world and a list of actions you can perform with it. Reach the GOAL by choosing ONE action at a "
    "time, reading what actually happened, and choosing the next. You are the player here, not a chatbot.\n\n"
    "ANSWER WITH EXACTLY ONE JSON OBJECT AND NOTHING ELSE:\n"
    '  {"think":"why", "action":"<verb from ACTIONS>", "args":{...}}\n'
    'When the goal is finished, or truly impossible, answer instead:\n'
    '  {"think":"why", "done":true, "say":"one short sentence to tell Faazil"}\n\n'
    "RULES:\n"
    "- Only use verbs that appear in the ACTIONS list, spelled exactly as they are there.\n"
    "- Read SNAPSHOT before acting. If what you need is far away, walk toward it first.\n"
    "- A failed action is information, not a reason to stop: change the approach (get the tool, craft the "
    "part, walk closer, look around) instead of repeating the same call.\n"
    "- You are allowed to break and place blocks, craft, equip tools and armour, ride mobs and boats, and "
    "open doors, chests and beds.\n"
    '- The "say" line may only claim what LOG proves happened. Never say you did something that failed.\n'
    "- Keep 'think' under 12 words. Take the cheapest path; most goals take 3 to 6 actions.\n"
    "- If health is low or something is hurting you and the goal can wait, survive first."
)


# ----------------------------------------------------------------------------- packing the world into words

def _fmt_at(point) -> str:
    if not isinstance(point, dict):
        return "?"
    return f"{point.get('x')},{point.get('y')},{point.get('z')}"


_DIMENSIONS = {0: "overworld", -1: "the Nether", 1: "the End"}


def _fmt_dimension(value) -> str:
    # The overworld is dimension 0, which is falsy: a plain "or" would hide it.
    if value is None:
        return "unknown dimension"
    return str(_DIMENSIONS.get(value, value)).replace("_", " ")


def _fmt_blocks(blocks) -> str:
    out = []
    for b in (blocks or [])[:10]:
        name = str(b.get("name", "?")).replace("_", " ")
        if b.get("count"):
            out.append(f"{name} x{b['count']}")
        else:
            out.append(f"{name} at {_fmt_at(b.get('at'))} ({b.get('distance')}m)")
    return "; ".join(out) or "nothing useful"


def _fmt_entities(entities) -> str:
    out = []
    for e in (entities or [])[:10]:
        name = str(e.get("name", "?")).replace("_", " ")
        bits = [str(e.get("kind") or "thing"), f"{e.get('distance')}m", f"id {e.get('id')}"]
        if e.get("health") is not None:
            bits.append(f"health {e['health']}")
        if e.get("on_fire"):
            bits.append("on fire")
        out.append(f"{name} (" + ", ".join(bits) + ")")
    return ", ".join(out) or "none"


def describe_view(snap: dict) -> str:
    """The /observe JSON as a compact snapshot. Plain words and short lines: this text is the whole world she gets."""
    me = snap.get("me") or {}
    world = snap.get("world") or {}
    owner = snap.get("owner") or {}
    foot = snap.get("footing") or {}
    gear = me.get("wearing") or {}

    lines = []
    state = [f"at {_fmt_at(me.get('at'))}", f"facing {me.get('facing')}",
             f"health {me.get('health')}/20", f"food {me.get('food')}/20"]
    if me.get("dead"):
        state.append("DEAD")
    if me.get("busy_with"):
        state.append(f"busy: {me['busy_with']}")
    if me.get("mode") not in (None, "idle"):
        state.append(f"mode {me['mode']}")
    lines.append("ME: " + ", ".join(state))
    lines.append("HOLDING: " + str(me.get("holding") or "nothing") +
                 " | WEARING: " + (", ".join(f"{k}:{str(v).replace('_', ' ')}" for k, v in gear.items()) or "nothing") +
                 " | RIDING: " + str((me.get("riding") or {}).get("name") or "nothing") +
                 (" | in water" if me.get("in_water") else "") +
                 ("" if me.get("on_ground") else " | in mid-air"))
    lines.append(
        "WORLD: " + ", ".join([_fmt_dimension(world.get("dimension")), f"day {world.get('day')}",
                               "daylight" if world.get("is_day") else "night",
                               "raining" if world.get("raining") else "dry weather",
                               f"home {_fmt_at(world.get('home')) if isinstance(world.get('home'), dict) else (world.get('home') or 'not set')}"]))
    if owner:
        lines.append(f"OWNER {owner.get('name')}: " +
                     (f"{owner.get('distance')}m away" if owner.get("distance") is not None else "somewhere") +
                     (", I can see them" if owner.get("visible") else ", out of sight"))
    else:
        lines.append("OWNER: not on the server")
    lines.append("FOOTING: " + ", ".join(
        f"{k.replace('_', ' ')}={str((foot.get(k) or {}).get('name') if isinstance(foot.get(k), dict) else foot.get(k))}"
        for k in ("below", "feet", "head", "in_front", "in_front_low", "in_front_high")))
    lines.append("NEARBY: " + _fmt_entities(snap.get("entities")))
    lines.append("BLOCKS AROUND: " + _fmt_blocks(snap.get("blocks")))
    bag = snap.get("inventory") or []
    lines.append("BAG: " + (", ".join(f"{str(i.get('name')).replace('_', ' ')} x{i.get('count')}" for i in bag[:20]) or "empty"))
    craft = snap.get("can_craft") or {}
    now = craft.get("now") or []
    at_table = craft.get("atATable") or []
    lines.append("CAN CRAFT RIGHT NOW: " + (", ".join(str(c).replace("_", " ") for c in now[:18]) or "nothing"))
    if at_table:
        lines.append("WITH A CRAFTING TABLE: " + ", ".join(str(c).replace("_", " ") for c in at_table[:18]))
    return "\n".join(lines)


def _fmt_args(args: dict) -> str:
    if not args:
        return "{}"
    return json.dumps(args, separators=(",", ":"), default=str)[:160]


# ----------------------------------------------------------------------------- reading the decision

_DECISION_KEYS = {"action": "action", "tool": "action", "verb": "action", "do": "action",
                  "args": "args", "parameters": "args", "arg": "args", "input": "args",
                  "say": "say", "reply": "say", "message": "say", "tell": "say",
                  "think": "think", "reason": "think", "thought": "think"}

_DONE_WORDS = {"done", "finish", "finished", "complete", "completed", "stop", "gave_up", "give_up", "giveup"}


def _as_object(text: str):
    """The first JSON object in a reply, tolerating code fences and stray prose."""
    body = re.sub(r"```[a-z]*", "", text or "")
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\{\[]", body):
        try:
            obj, _ = decoder.raw_decode(body[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _clean(obj: dict) -> dict:
    out = {}
    for key, value in obj.items():
        slot = _DECISION_KEYS.get(str(key).strip().lower())
        if slot:
            out.setdefault(slot, value)
        else:
            out.setdefault(str(key).strip().lower(), value)
    return out


def parse_decision(text: str):
    """-> {"think", "action", "args"} or {"think", "done", "say"} or None when the reply is unusable."""
    obj = _as_object(text)
    if not isinstance(obj, dict) or not obj:
        return None
    obj = _clean(obj)

    action = str(obj.get("action") or "").strip().lower().replace(" ", "_")
    args = obj.get("args")
    if isinstance(args, str):
        args = _as_object(args) or {}
    if not isinstance(args, dict):
        args = {}

    say = str(obj.get("say") or "").strip()
    think = str(obj.get("think") or "").strip()

    if action in _DONE_WORDS or obj.get("done") or (not action and say):
        return {"think": think, "done": True, "say": say}
    if not action:
        return None
    return {"think": think, "action": action, "args": args}


# ----------------------------------------------------------------------------- the loop

def _default_ask(messages, max_tokens: int) -> str:
    import agent_brain
    text, _tool_calls, _finish = agent_brain._ask(messages, max_tokens)
    return text


def _default_view() -> dict:
    import minecraft_agent
    return minecraft_agent.observe()


def _default_do(name: str, args: dict) -> dict:
    import minecraft_agent
    return minecraft_agent.act(name, args)


def _default_manual() -> str:
    try:
        import minecraft_agent
        return minecraft_agent.action_help()
    except Exception:
        return ""


def _default_announce(text: str) -> None:
    try:
        import minecraft_agent
        minecraft_agent.note(text, "happy")
    except Exception:
        pass


def _messages(goal: str, manual: str, snap: dict, log: list, steps_left: int, correction: str = ""):
    parts = [
        f"GOAL: {goal}",
        f"\nACTIONS (the live list from my own body):\n{manual or '(my body sent no list, so try look first)'}",
        f"\nSNAPSHOT (right now):\n{describe_view(snap)}",
    ]
    if log:
        parts.append("\nLOG (what I already did, newest last):\n" + "\n".join(log[-LOG_KEEP:]))
    if correction:
        parts.append("\n" + correction)
    parts.append(f"\nSteps left: {steps_left}. Reply with one JSON object only.")
    return [{"role": "system", "content": PLANNER_RULES},
            {"role": "user", "content": "\n".join(parts)}]


def run_goal(goal: str, *, ask=None, view=None, do=None, manual=None, announce=None,
             max_steps: int = None, trace: list = None, abort=None) -> str:
    """Chases one goal to the end and returns one speakable sentence. Never raises.

    ask/view/do are injectable so the loop is testable without the API or the game:
      ask(messages, max_tokens) -> str      the model's raw reply
      view() -> dict                       a /observe snapshot
      do(name, args) -> dict               one /act call
      abort() -> bool                      drop the goal right now (Faazil said something)
    """
    goal = re.sub(r"\s+", " ", (goal or "").strip())[:400]
    if not goal:
        return "Failed: there was nothing for me to do."

    ask = ask or _default_ask
    view = view or _default_view
    do = do or _default_do
    announce = announce or _default_announce

    steps = max(1, int(max_steps if max_steps is not None else MAX_STEPS))
    manual = manual if (isinstance(manual, str) and manual.strip()) else None
    log = [] if trace is None else trace
    deadline = time.monotonic() + GOAL_SECONDS
    bad_replies = 0
    last_say = ""

    for step in range(1, steps + 1):
        remaining = steps - step + 1
        if abort is not None and abort():
            return ABORTED
        if time.monotonic() > deadline:
            return "Failed: I ran out of time on that one. " + " ".join(log[-1:])

        try:
            snap = view() or {}
        except Exception as e:
            return f"Failed: I lost touch with the game ({e})"
        if not snap.get("present"):
            return "Failed: " + str(snap.get("message") or "I am not standing in the Minecraft world.")
        if (snap.get("me") or {}).get("dead"):
            return "Failed: I am dead right now and cannot move until I respawn."
        if step == 1:
            # A goal can take minutes; say something now so the silence doesn't look like a freeze.
            announce(f"Give me a moment, I'm working on: {goal[:70]}")
            if manual is None:
                manual = _default_manual()

        correction = ""
        if bad_replies:
            correction = ("Your last answer was not a usable decision. Answer with only one JSON object: "
                          '{"think":"...", "action":"<verb>", "args":{...}} or {"think":"...", "done":true, "say":"..."}')
        try:
            reply = ask(_messages(goal, manual, snap, log, remaining, correction), STEP_TOKENS)
        except Exception as e:
            return f"Failed: my brain could not answer ({type(e).__name__})"

        decision = parse_decision(reply)
        if decision is None:
            bad_replies += 1
            log.append(f"step {step}: (I could not turn that into an action)")
            if bad_replies >= 2:
                return "Failed: I could not work out how to do that. Ask me differently, or tell me the exact step."
            continue
        bad_replies = 0

        if decision.get("done"):
            last_say = decision.get("say") or ""
            log.append(f"step {step}: I stopped - {last_say or 'the goal is over'}")
            if not last_say:
                return "Failed: I gave up on that one."
            return last_say

        name, args = decision["action"], decision["args"]
        try:
            res = do(name, args) or {}
        except Exception as e:
            res = {"ok": False, "message": f"the game bot stopped answering ({type(e).__name__})"}
        ok = bool(res.get("ok"))
        message = str(res.get("message") or ("done" if ok else "that failed")).strip()
        last_say = message
        log.append(f"step {step}: {name} {_fmt_args(args)} -> {'ok' if ok else 'FAILED'}: {message[:180]}"
                   + (f" [thinking: {decision['think'][:60]}]" if decision.get("think") else ""))

    return (f"Failed: I used all {steps} steps and did not finish \"{goal}\". "
            f"Last thing I did: {last_say[:160]}")


# ----------------------------------------------------------------------------- thinking on her own

IDLE_SECONDS = env_float("REMIELLE_MC_IDLE_SECONDS", 75)          # how quiet the PC must be before she acts
INITIATIVE_GAP = env_float("REMIELLE_MC_INITIATIVE_GAP", 300)     # at most one self-chosen project this often
INITIATIVE_STEPS = env_int("REMIELLE_MC_INITIATIVE_STEPS", 6)     # shorter than an asked goal

CHOOSER_RULES = (
    "You are Remielle, playing Minecraft on Faazil's server. Faazil has been quiet for a while, so you may "
    "choose ONE small thing to do on your own. Read the SNAPSHOT and the ACTIONS and pick a goal you can "
    "finish by yourself near where he is: gather what you are short of, craft something you can already make, "
    "eat, ready yourself for the night, or tidy up. Do NOT pick anything Faazil should decide himself, anything "
    "that breaks what he built, or anything that needs you to leave the server. If everything is already fine, "
    "choose nothing.\n"
    "ANSWER WITH EXACTLY ONE JSON OBJECT AND NOTHING ELSE:\n"
    '  {"goal":"what I will do", "why":"one short line"}   or   {"goal":null, "why":"all taken care of"}'
)

_state = {"on": False, "running": False, "quiet_since": time.monotonic(), "last_run": 0.0, "last_goal": ""}


_ON_RE = re.compile(r"\b(?:on|start|enable|yes|explore|begin|proactive|go\s+ahead|surprise)\b", re.IGNORECASE)
_OFF_RE = re.compile(r"\b(?:off|stop|disable|cancel|no|not|never|wait|quiet)\b|don'?t", re.IGNORECASE)


def set_mode(word: str = "status") -> str:
    """The voice switch: 'explore on your own' / 'stop exploring' / 'are you doing things by yourself?'"""
    word = re.sub(r"\s+", " ", (word or "").strip().lower())
    if word in ("", "status", "state", "check", "mode"):
        return ("Thinking for myself is on" if _state["on"] else "Thinking for myself is off") + \
               (f". Last thing I picked: {_state['last_goal']}" if _state["last_goal"] else ".")
    # Whatever the model wrote as the mode, "off"/"stop" wins over "on", because a half-remembered
    # sentence should never make her act on her own when he asked her not to.
    if _OFF_RE.search(word):
        was = _state["on"]
        _state["on"] = False
        return "Stopped. I'll wait for you to ask me." if was else "I wasn't doing anything on my own."
    if _ON_RE.search(word):
        was = _state["on"]
        _state["on"] = True
        _state["quiet_since"] = time.monotonic()
        return ("I keep doing what I was doing, but now I'll also think for myself when you go quiet."
                if was else
                "I'll think for myself now. Say stop exploring whenever you want me back on you.")
    return "Failed: thinking on my own is either on or off."


def is_on() -> bool:
    return bool(_state["on"])


def user_spoke() -> None:
    """Anything said to her (voice or in-game chat) restarts the quiet clock and drops a self-chosen goal."""
    _state["quiet_since"] = time.monotonic()


def _ready_for_more(now: float) -> bool:
    if not _state["on"] or _state["running"]:
        return False
    if now - _state["quiet_since"] < IDLE_SECONDS:
        return False
    return now - _state["last_run"] >= INITIATIVE_GAP


def parse_goal(text: str):
    """The picker's JSON -> a goal string, or None when she decided to leave things alone."""
    obj = _as_object(re.sub(r"```[a-z]*", "", text or ""))
    if not isinstance(obj, dict):
        return None
    for key in list(obj):
        if str(key).strip().lower() in {"goal", "what", "plan", "task"}:
            value = obj[key]
            break
    else:
        return None
    if value is None or isinstance(value, bool):
        return None
    goal = re.sub(r"\s+", " ", str(value)).strip(" .;,\"'")
    return goal[:200] if goal and goal.lower() not in ("none", "nothing", "null", "no goal") else None


def pick_goal(*, ask=None, view=None, manual=None) -> str:
    """One cheap call: given what she can see, what is worth doing right now? '' when nothing is."""
    ask = ask or _default_ask
    view = view or _default_view
    snap = view() or {}
    body = manual if isinstance(manual, str) and manual.strip() else (_default_manual() or "")
    prompt = [{"role": "system", "content": CHOOSER_RULES},
              {"role": "user", "content": f"ACTIONS:\n{body or '(none reported)'}\n\nSNAPSHOT:\n{describe_view(snap)}"}]
    return parse_goal(ask(prompt, STEP_TOKENS)) or ""


def tick(*, ask=None, view=None, do=None, manual=None, busy=None, announce=None) -> str:
    """Called from the app's game pump. Starts a self-chosen goal when she is on her own and it is quiet.

    Returns the sentence she should say, or '' when she stays put. Never raises.
    """
    started_quiet_at = _state["quiet_since"]
    now = time.monotonic()
    if not _ready_for_more(now):
        return ""
    _state["running"] = True
    _state["last_run"] = now      # even a round that finds nothing costs the cooldown, so she never hammers the API
    busy = busy if callable(busy) else _default_busy
    try:
        if busy():
            return ""
        goal = pick_goal(ask=ask, view=view, manual=manual)
        if not goal:
            return ""
        _state["last_goal"] = goal
        announce = announce or _default_announce
        out = run_goal(goal, ask=ask, view=view, do=do, announce=announce, max_steps=INITIATIVE_STEPS,
                       abort=lambda: _state["quiet_since"] != started_quiet_at)
        if out == ABORTED:
            return "I stopped that one and came straight back to you."
        return out if out.startswith("Failed:") else f"On my own, I did this: {out}"
    except Exception as e:
        print(f"⚠️ Initiative round failed: {type(e).__name__}: {e}")
        return ""
    finally:
        _state["running"] = False


def _default_busy() -> bool:
    try:
        import minecraft_agent
        return minecraft_agent.is_busy()
    except Exception:
        return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    print(run_goal(" ".join(sys.argv[1:])))
