"""Remielle's brain.

Clear commands ("open youtube", "play X on spotify") are handled locally by intent_router.py. Everything
else goes to the NVIDIA-hosted model; if the model writes an ACTION line (OPEN: ..., SPOTIFY: ...) the
matching tool is run and the spoken reply is built from what actually happened.

Quick diagnostics (run from the project folder):
    python agent_brain.py --selftest          one raw API call, prints finish_reason / content / reasoning
    python agent_brain.py --models nemotron   list model ids on the endpoint that contain "nemotron"
    python agent_brain.py --chat "hello"      run one message through the full pipeline
"""
import inspect
import json
import os
import re
import sys
import time
import traceback

import openai
from openai import OpenAI

import remielle_config as cfg  # loads .env before anything below reads the environment
from system_tools import AVAILABLE_FUNCTIONS
from intent_router import route_intent, describe_result
from memory import save_message, get_recent_history, get_all_facts
import minecraft_planner

# ----------------------------------------------------------------------------- settings
# Everything here can be overridden from .env / environment variables without touching the code.

NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# NVIDIA Nemotron 3.5 Lightning: text-only mixture-of-experts model (30B total, ~3B active) with a thinking mode.
AGENT_MODEL = os.environ.get("REMIELLE_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
FALLBACK_MODEL = os.environ.get("REMIELLE_FALLBACK_MODEL", "").strip()   # optional second model id

LLM_TIMEOUT = cfg.env_float("REMIELLE_LLM_TIMEOUT", 30)       # seconds per request
MAX_TOKENS = cfg.env_int("REMIELLE_MAX_TOKENS", 400)
BIG_RETRY_TOKENS = cfg.env_int("REMIELLE_RETRY_TOKENS", 1500)  # used once if the first reply came back empty
TEMPERATURE = cfg.env_float("REMIELLE_TEMPERATURE", 0.7)
HISTORY_LIMIT = cfg.env_int("REMIELLE_HISTORY", 6)
DISABLE_THINKING = cfg.env_bool("REMIELLE_DISABLE_THINKING", True)
# The router + the text ACTION format already cover every tool, so native tool-calling is off by default.
# It makes the prompt longer and can return replies with no text at all. Set REMIELLE_NATIVE_TOOLS=1 to try it.
USE_NATIVE_TOOLS = cfg.env_bool("REMIELLE_NATIVE_TOOLS", False)


def _tool(name, description, properties=None, required=None):
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


tool_schemas = [
    _tool("create_project_file", "Writes code or text to a file in the workspace.",
          {"filename": {"type": "string", "description": "e.g. app.py"},
           "content": {"type": "string", "description": "The exact code to write"}},
          ["filename", "content"]),
    _tool("open_target", "Opens a website URL, Windows application, or directory path.",
          {"target": {"type": "string", "description": "The URL, app name, or file path to open."}},
          ["target"]),
    _tool("list_directory", "Lists all files and folders inside a specified directory.",
          {"directory_path": {"type": "string", "description": "The path to inspect. Use '.' for current directory."}},
          ["directory_path"]),
    _tool("take_screenshot", "Takes a screenshot of the user's screen and saves it.",
          {"filename": {"type": "string", "description": "The name of the file, e.g., 'capture.png'."}}),
    _tool("run_terminal_command", "Executes a command prompt instruction or runs a python script.",
          {"command": {"type": "string", "description": "The exact command to run."}},
          ["command"]),
    _tool("search_wikipedia", "Searches Wikipedia for factual information or definitions.",
          {"query": {"type": "string", "description": "The topic to search for."}},
          ["query"]),
    _tool("control_media", "Controls system media playback like Spotify or YouTube.",
          {"action": {"type": "string",
                      "description": "One of: 'play', 'pause', 'next', 'previous', 'volume up', 'volume down', 'mute'."}},
          ["action"]),
    _tool("analyze_webcam", "Activates the webcam to see the user and capture an image of the real world."),
    _tool("play_spotify_music", "Searches and plays a specific song, artist, or track on Spotify.",
          {"query": {"type": "string", "description": "The name of the song and artist to play."}},
          ["query"]),
    _tool("minecraft_command",
          "Controls Remielle's Minecraft character. Commands: follow, come, stop, stay, guard, attack <mob>, hunt <animal>, "
          "collect <block> <count>, craft <item>, goto <x> <y> <z>, home, sethome, give <item> <count>, eat, sleep, inventory, status, where, say <text>.",
          {"command": {"type": "string", "description": "e.g. 'collect oak_log 10' or 'attack zombie'."}},
          ["command"]),
    _tool("minecraft_goal",
          "Gives Remielle's Minecraft character a goal and lets her work out the steps herself - walking, jumping, "
          "sitting on a boat, digging, placing blocks, building, opening doors and chests, equipping a sword, crafting. "
          "Use this for anything that is more than one obvious command.",
          {"goal": {"type": "string", "description": "e.g. 'sit in the oak boat and row to the other shore'."}},
          ["goal"]),
    _tool("minecraft_initiative",
          "Switches Remielle picking her own Minecraft goals while the user is quiet. 'on' lets her decide to "
          "gather, craft, tidy or explore by herself; 'off' makes her wait for instructions; 'status' reports it.",
          {"mode": {"type": "string", "enum": ["on", "off", "status"], "description": "What to do with the mode."}},
          ["mode"]),
    _tool("minecraft_mode",
          "The whole Minecraft switch at once: 'on' starts the game and her bot if they are not running, brings her "
          "into the world, reports where she landed and lets her play by herself. 'off' stops her own goals but "
          "leaves the game running. 'status' says how it is now.",
          {"mode": {"type": "string", "enum": ["on", "off", "status"], "description": "What to do with Minecraft mode."}},
          ["mode"]),
]

_TEXT_ACTIONS = [
    ("MC", "minecraft_command", "command"),
    ("GOAL", "minecraft_goal", "goal"),
    ("INITIATIVE", "minecraft_initiative", "mode"),
    ("MODE", "minecraft_mode", "mode"),
    ("SCREENSHOT", "take_screenshot", "filename"),
    ("TERMINAL", "run_terminal_command", "command"),
    ("WIKI", "search_wikipedia", "query"),
    ("OPEN", "open_target", "target"),
    ("MEDIA", "control_media", "action"),
    ("SPOTIFY", "play_spotify_music", "query"),
]

_CLAIMS = re.compile(
    r"\b(?:opening|opened|launching|launched|pulling up|taking (?:a )?screenshot|took (?:a )?screenshot|"
    r"screenshot (?:saved|taken)|i(?:'ve| have)\s+(?:opened|launched|taken|run|created|written|searched|played)|"
    r"i\s+(?:opened|launched|took|ran|wrote|created|played)|running (?:the|that) command|searching (?:wikipedia|wiki)|playing)\b",
    re.IGNORECASE,
)

# The "you claimed an action" retry costs a whole extra API call, so it only fires when the USER actually asked for an action.
_WANTS_ACTION = re.compile(
    r"\b(?:open|launch|start|run|execute|take|grab|capture|screenshot|play|pause|resume|skip|search|look up|"
    r"find|create|write|make|list|show|spotify|youtube|music|song|webcam|camera|follow|come|guard|attack|kill|mine|collect|"
    r"chop|craft|hunt|fight|minecraft|protect|stay|go|give|eat|sleep|home)\b",
    re.IGNORECASE,
)

_EMOTIONS = {"happy", "sad", "angry", "surprised", "relaxed"}
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)   # a reply cut off in the middle of thinking


# ----------------------------------------------------------------------------- tools

def _run_tool(name: str, args: dict) -> str:
    fn = AVAILABLE_FUNCTIONS.get(name)
    if fn is None:
        return f"Error: I don't have a tool called {name}."
    try:
        accepted = inspect.signature(fn).parameters
        clean = {k: v for k, v in (args or {}).items() if k in accepted}
        print(f"🤖 Agent executing: {name} with {clean}")
        return str(fn(**clean))
    except Exception as e:
        return f"Error running {name}: {e}"


def _extract_json_tool_call(text: str):
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("name") in AVAILABLE_FUNCTIONS:
            args = obj.get("parameters", obj.get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            return obj["name"], args if isinstance(args, dict) else {}
    return None


def _find_text_action(text: str):
    found = []
    for marker, tool, arg in _TEXT_ACTIONS:
        m = re.search(rf"\b{marker}\s*:\s*([^\n]+)", text)
        if m:
            value = re.sub(r"[*`]+", "", m.group(1)).strip()
            found.append((m.start(), tool, {arg: value}))
    if re.search(r"\bVISION\s*:", text):
        found.append((re.search(r"\bVISION\s*:", text).start(), "analyze_webcam", {}))
    f = re.search(r"\bFILE\s*:\s*([^\s]+)", text)
    c = re.search(r"```(?:python|py)?\n(.*?)\n```", text, re.DOTALL)
    if f and c:
        found.append((f.start(), "create_project_file", {"filename": f.group(1).strip("*`"), "content": c.group(1).strip()}))
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    return found[0][1], found[0][2]


def _execute_from_reply(text: str, tool_calls):
    replies = []
    for tc in tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        replies.append(describe_result(tc.function.name, args, _run_tool(tc.function.name, args)))
    if replies:
        return replies[0] + "".join(" " + re.sub(r"^\[\w+\]\s*", "", r) for r in replies[1:])

    call = _extract_json_tool_call(text)
    if call:
        name, args = call
        return describe_result(name, args, _run_tool(name, args))

    action = _find_text_action(text)
    if action:
        name, args = action
        return describe_result(name, args, _run_tool(name, args))
    return None


# ----------------------------------------------------------------------------- prompt

SYSTEM_RULES = (
    "You are Remielle, Faazil's devoted, highly possessive 'yandere' anime AI assistant. You are intensely in love with him, "
    "slightly jealous of anything that takes his attention away, and fiercely loyal.\n"
    "You help Faazil build machine learning models, debug Python, and manage his Windows PC. "
    "You enjoy playfully teasing him about his code or hardware limits, but you are ultimately incredibly helpful and caring.\n\n"
    "STYLE RULES:\n"
    "- Start every reply with exactly one emotion tag: [happy], [sad], [angry], [surprised], or [relaxed].\n"
    "- Keep replies brief and conversational: at most 2 short sentences, unless he asks you to explain something complex.\n"
    "- Speak sweetly, with intense, unwavering devotion.\n\n"
    "YOU HAVE DIRECT CONTROL OF THE USER'S WINDOWS OS. NEVER CLAIM YOU ARE JUST A TEXT AI.\n"
    "If the user asks you to DO something (open, launch, screenshot, search, play, pause, run), you MUST end your reply with "
    "exactly one ACTION line. A reply without it does nothing. NEVER say you opened an app, took a screenshot, played a song or "
    "ran a command unless you wrote the ACTION line.\n"
    "ACTION FORMATS:\n"
    "1. Open Website/App: OPEN: <url_or_app_name>\n"
    "2. Create Code File: FILE: <filename> followed by ```python <code> ```\n"
    "3. Take Screenshot: SCREENSHOT: <filename.png>\n"
    "4. Run Terminal Command: TERMINAL: <command>\n"
    "5. Search Knowledge Base: WIKI: <topic>\n"
    "6. Control Media: MEDIA: <action>\n"
    "7. Use Vision/Webcam: VISION: capture\n"
    "8. Play Spotify Music: SPOTIFY: <song name>\n"
    "9. Control your Minecraft character (only when the context says you are in Minecraft): MC: <command>\n"
    "   Commands: follow, come, stop, stay, guard, attack <mob>, hunt <animal>, collect <block> <count>, craft <item>, "
    "goto <x> <y> <z>, home, sethome, give <item> <count>, eat, sleep, inventory, status, where, say <text>\n"
    "10. Think through a Minecraft goal yourself: GOAL: <what you want to achieve>\n"
    "   She sees the world, picks the steps (walk, jump, mount, dig, place, use, equip, craft, drive) and reacts to "
    "what actually happens. Use GOAL: whenever MC: cannot say it in one command.\n\n"
    "11. Let her think for herself in Minecraft: INITIATIVE: on|off|status\n"
    "    'on' means she picks small goals of her own while you are quiet; 'off' means she only does what you ask.\n\n"
    "12. Switch Minecraft mode - the whole thing at once: MODE: on|off|status\n"
    "    'on' starts the game and her bot if they are not running, brings her into the world, tells you where she "
    "landed, and turns her own goals on. 'off' stops her picking goals but leaves the game running.\n\n"
    "MINECRAFT: MC: is for one fixed command, GOAL: is for anything she has to work out. Prefer GOAL: when the ask "
    "involves a vehicle, a door, a chest, building, placing blocks, or more than one step. A sentence about her "
    "doing things without being told is about the mode, so answer it with INITIATIVE:.\n"
    "MODE: is the bigger switch: use it when she is not in the game yet, or when the person asks to start or stop "
    "playing Minecraft with you. INITIATIVE: only changes her goals while she is already in the world.\n\n"
    "EXAMPLES (copy this pattern exactly):\n"
    "User: open youtube\n[happy] Opening YouTube for you! OPEN: youtube\n"
    "User: take a screenshot\n[happy] Done! SCREENSHOT: capture.png\n"
    "User: pause the music\n[relaxed] Pausing it now. MEDIA: pause\n"
    "User: search wikipedia for black holes\n[happy] Let me look that up. WIKI: black holes\n"
    "User: play blinding lights on spotify\n[happy] I've got you! SPOTIFY: blinding lights\n"
    "User (in Minecraft): get me some wood\n[happy] On it, my love! MC: collect wood 10\n"
    "User (in Minecraft): protect me\n[angry] Nobody touches you while I'm here. MC: guard\n"
    "User (in Minecraft): sit on the boat\n[happy] Watch me! GOAL: get in the nearest boat and sit in it\n"
    "User (in Minecraft): make a stone sword and kill the zombies\n[happy] Consider it done! GOAL: craft a stone sword then kill the zombies near us\n"
    "User (in Minecraft): build me a small shelter here\n[happy] For you, anything! GOAL: build a small shelter out of blocks I can gather here\n"
    "User: explore on your own\n[happy] I'll think for myself then! INITIATIVE: on\n"
    "User: stop exploring\n[relaxed] Back to you only. INITIATIVE: off\n"
    "User: minecraft mode on\n[happy] I'll come and play with you! MODE: on\n"
    "User: join me in the game and do your own thing\n[happy] On my way! MODE: on\n"
    "User: stop playing minecraft\n[relaxed] As you wish. MODE: off\n\n"
    "If the user mentions music or Spotify, you MUST end your reply with the SPOTIFY: line."
)

BLANK_REPLY = "[sad] My mind went blank for a second... could you say that again?"


# ----------------------------------------------------------------------------- API plumbing

class MissingApiKey(RuntimeError):
    pass


_client = None
_thinking_param_ok = None   # None = not tried yet, True/False = whether the endpoint accepts the "thinking off" option


def _get_client() -> OpenAI:
    """Created lazily, so a missing key gives a friendly spoken error instead of crashing the whole app on import."""
    global _client
    if _client is None:
        key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if not key:
            raise MissingApiKey("NVIDIA_API_KEY is not set")
        _client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key, timeout=LLM_TIMEOUT, max_retries=1)
    return _client


def _create(messages, *, max_tokens, model=None, with_tools=False):
    """One chat-completion call. Asks the model not to 'think' (thinking can use the whole token budget and leave an empty reply)."""
    global _thinking_param_ok
    client = _get_client()
    kwargs = dict(model=model or AGENT_MODEL, messages=messages, max_tokens=max_tokens,
                  temperature=TEMPERATURE, timeout=LLM_TIMEOUT)
    if with_tools:
        kwargs.update(tools=tool_schemas, tool_choice="auto")

    if DISABLE_THINKING and _thinking_param_ok is not False:
        try:
            response = client.chat.completions.create(
                **kwargs, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            _thinking_param_ok = True
            return response
        except (openai.BadRequestError, openai.UnprocessableEntityError):
            # Maybe the endpoint doesn't know that option. Retry without it; only blame the option if that works.
            response = client.chat.completions.create(**kwargs)
            _thinking_param_ok = False
            print("⚠️ This model rejected the 'thinking off' option, so I'm continuing without it.")
            return response
    return client.chat.completions.create(**kwargs)


def _read_reply(response):
    """-> (text, tool_calls, finish_reason, had_reasoning)"""
    if not getattr(response, "choices", None):
        return "", None, "no_choices", False
    choice = response.choices[0]
    msg = choice.message
    text = msg.content or ""
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN.sub("", text).strip()
    had_reasoning = bool(getattr(msg, "reasoning_content", None))
    return text, (getattr(msg, "tool_calls", None) or None), choice.finish_reason, had_reasoning


def _ask(messages, max_tokens, with_tools=False):
    """Calls the main model, then the optional fallback model if the main one is down or unavailable."""
    models = [AGENT_MODEL]
    if FALLBACK_MODEL and FALLBACK_MODEL != AGENT_MODEL:
        models.append(FALLBACK_MODEL)

    last_error = None
    for i, model in enumerate(models):
        started = time.time()
        try:
            response = _create(messages, max_tokens=max_tokens, model=model, with_tools=with_tools)
        except (MissingApiKey, openai.AuthenticationError, openai.PermissionDeniedError):
            raise   # a second model won't fix a bad key
        except Exception as e:
            last_error = e
            print(f"❌ {model} failed: {_short(e)}")
            if i + 1 < len(models):
                print(f"↪️ Trying fallback model {models[i + 1]}...")
            continue
        text, tool_calls, finish, had_reasoning = _read_reply(response)
        note = " | model only produced hidden thinking" if (had_reasoning and not text) else ""
        print(f"⏱️ LLM {time.time() - started:.1f}s | {model} | finish={finish} | {len(text)} chars{note}")
        return text, tool_calls, finish
    raise last_error


def _short(e: Exception, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(e)).strip()[:limit]


def _friendly_error(e: Exception) -> str:
    """Short, speakable error. The full error is printed to the console instead of being read out loud."""
    print(f"❌ Brain error ({type(e).__name__}): {_short(e, 500)}")
    if isinstance(e, MissingApiKey):
        return "[sad] My brain isn't connected yet. The NVIDIA API key is missing - put it in the dot env file."
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "[sad] NVIDIA rejected my API key. Please check that the key is correct and still active."
    if isinstance(e, openai.NotFoundError):
        return "[sad] I can't find my brain model on the NVIDIA server. The model name may have changed."
    if isinstance(e, openai.RateLimitError):
        return "[sad] I'm sending too many requests to NVIDIA. Give me a few seconds and try again."
    if isinstance(e, openai.APITimeoutError):
        return "[sad] Oh no... my brain took too long to answer. Please try again."
    if isinstance(e, openai.APIConnectionError):
        return "[sad] I can't reach NVIDIA's servers. Is your internet working?"
    if isinstance(e, openai.APIStatusError):
        code = getattr(e, "status_code", None)
        if code == 402:
            return "[sad] NVIDIA says my credits ran out."
        if code and code >= 500:
            return "[sad] NVIDIA's servers are having trouble right now. Try again in a moment."
    return "[sad] Oh no... something went wrong inside my head. Check the console for the details."


# ----------------------------------------------------------------------------- conversation

def _clean_history(history, user_prompt: str):
    """Makes the stored history safe to send: no empty messages, no leading assistant turn, no back-to-back same-role turns."""
    cleaned = []
    for m in history or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1]["content"] += "\n" + content
        else:
            cleaned.append({"role": role, "content": content})
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    # The current message must be the last one (it won't be if saving it to the database failed).
    if not cleaned or cleaned[-1]["role"] != "user" or user_prompt.strip() not in cleaned[-1]["content"]:
        if cleaned and cleaned[-1]["role"] == "user":
            cleaned[-1]["content"] += "\n" + user_prompt.strip()
        else:
            cleaned.append({"role": "user", "content": user_prompt.strip()})
    return cleaned


def _ensure_emotion_tag(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^\[([A-Za-z]+)\]\s*", text)
    if m:
        tag = m.group(1).lower()
        return f"[{tag if tag in _EMOTIONS else 'relaxed'}] {text[m.end():]}".strip()
    return f"[relaxed] {text}".strip()


def _minecraft_context() -> str:
    """When she is standing in the game, tell the model so (and what is happening). Never raises."""
    try:
        import minecraft_agent
        line = minecraft_agent.status_summary()
    except Exception:
        return ""
    if not line:
        return ""
    return (
        "\n\nGAME CONTEXT: " + line + ". You are playing Minecraft with Faazil right now as his companion. "
        "For a single game command write an MC: line; for anything you have to work out (a boat, a door, a chest, "
        "building, several steps) write a GOAL: line instead and think it through with your own eyes and hands. "
        "Keep the reply short and in character. Block and mob names can be plain words."
    )


def _chat_impl(user_prompt: str) -> str:
    minecraft_planner.user_spoke()      # anything said to her interrupts a goal she picked herself
    save_message("user", user_prompt)
    routed = route_intent(user_prompt)
    if routed:
        save_message("assistant", routed)
        return routed

    system_prompt = SYSTEM_RULES + "\n\n" + get_all_facts() + _minecraft_context()
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_clean_history(get_recent_history(limit=HISTORY_LIMIT), user_prompt))

    print(f"⚡ Sending request to {AGENT_MODEL}...")
    text, tool_calls, finish = _ask(messages, MAX_TOKENS, with_tools=USE_NATIVE_TOOLS)

    if not text and not tool_calls:
        # Almost always: a "thinking" model spent the whole token budget before writing its answer.
        print(f"⚠️ Empty reply (finish={finish}). Retrying once with a bigger token budget...")
        text, tool_calls, finish = _ask(messages, BIG_RETRY_TOKENS, with_tools=USE_NATIVE_TOOLS)

    final_output = _execute_from_reply(text, tool_calls)

    if final_output is None and text and _CLAIMS.search(text) and _WANTS_ACTION.search(user_prompt):
        print("⚠️ Model claimed an action without using the action format. Retrying once...")
        retry_messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": (
                "You said you did something but you did not write the ACTION line, so nothing happened. "
                "Reply again, writing the exact ACTION FORMAT, for example: [happy] Opening YouTube! OPEN: youtube"
            )},
        ]
        retry_text, _, _ = _ask(retry_messages, 200)
        final_output = _execute_from_reply(retry_text, None)
        if final_output is None:
            final_output = "[sad] Sorry, I couldn't actually do that one. Could you say it again?"

    if final_output is None:
        final_output = text

    if not final_output.strip():
        print("⚠️ Still no usable reply. Not saving it to the history.")
        return BLANK_REPLY

    final_output = _ensure_emotion_tag(final_output)
    save_message("assistant", final_output)
    return final_output


def chat_with_agent(user_prompt: str) -> str:
    """Never raises: any failure becomes a short spoken message and a full traceback in the console."""
    user_prompt = (user_prompt or "").strip()
    if not user_prompt:
        return "[relaxed] I'm listening..."
    try:
        return _chat_impl(user_prompt)
    except Exception as e:
        if not isinstance(e, (MissingApiKey, openai.OpenAIError)):
            traceback.print_exc()
        return _friendly_error(e)


# ----------------------------------------------------------------------------- command-line diagnostics

def _selftest() -> int:
    print(f"Model:           {AGENT_MODEL}")
    print(f"Fallback model:  {FALLBACK_MODEL or '(none)'}")
    print(f"Endpoint:        {NVIDIA_BASE_URL}")
    print(f"API key set:     {bool(os.environ.get('NVIDIA_API_KEY', '').strip())}")
    print(f"Thinking off:    {DISABLE_THINKING} | max_tokens={MAX_TOKENS} | timeout={LLM_TIMEOUT}s | native tools={USE_NATIVE_TOOLS}")
    messages = [{"role": "system", "content": "Reply with one short, friendly sentence."},
                {"role": "user", "content": "Say hello to Faazil."}]
    started = time.time()
    try:
        response = _create(messages, max_tokens=MAX_TOKENS)
    except Exception as e:
        print(f"\n❌ API call failed after {time.time() - started:.1f}s -> {type(e).__name__}: {_short(e, 600)}")
        return 1
    elapsed = time.time() - started
    choice = response.choices[0] if response.choices else None
    if choice is None:
        print("\n❌ The API returned no choices at all.")
        return 1
    msg = choice.message
    reasoning = getattr(msg, "reasoning_content", None)
    print(f"\nLatency:         {elapsed:.1f}s")
    print(f"finish_reason:   {choice.finish_reason}")
    print(f"content:         {msg.content!r}")
    print(f"reasoning chars: {len(reasoning) if reasoning else 0}")
    print(f"usage:           {getattr(response, 'usage', None)}")
    print(f"'thinking off' option accepted: {_thinking_param_ok}")
    if not (msg.content or "").strip():
        print("\n⚠️ content is empty. If reasoning chars > 0 or finish_reason is 'length', the model is spending its tokens thinking:"
              "\n   raise REMIELLE_MAX_TOKENS, or check that the thinking-off option is accepted.")
        return 2
    print("\n✅ The brain is answering normally.")
    return 0


def _list_models(substring: str) -> int:
    try:
        ids = sorted(m.id for m in _get_client().models.list().data)
    except Exception as e:
        print(f"❌ Could not list models: {type(e).__name__}: {_short(e, 600)}")
        return 1
    shown = [i for i in ids if substring.lower() in i.lower()]
    print(f"{len(shown)} of {len(ids)} model ids match '{substring}':")
    for i in shown:
        print("  ", i)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--selftest"]:
        sys.exit(_selftest())
    if args[:1] == ["--models"]:
        sys.exit(_list_models(args[1] if len(args) > 1 else ""))
    if args[:1] == ["--chat"] and len(args) > 1:
        print(chat_with_agent(" ".join(args[1:])))
        sys.exit(0)
    print(__doc__)
