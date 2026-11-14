"""Instant command router.

A small local LLM often forgets to write the exact "OPEN: youtube" style command, so asking it to
"open YouTube" silently does nothing. This module catches clear commands in plain Python BEFORE the
model is involved, runs the real function, and builds an honest reply from what actually happened.
Anything it does not recognise falls through to the model as before.

Handled here:  open / launch (sites, apps, paths), screenshot, webcam, media keys, Wikipedia, list folder.
Left to the model (free-form content or risky to run from a possibly misheard sentence):
create_project_file and run_terminal_command.
"""
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from system_tools import AVAILABLE_FUNCTIONS, can_open

# ----------------------------------------------------------------------------- helpers

_FAIL_RE = re.compile(
    r"^\s*(?:failed|error|could not|invalid|directory not found|command failed|execution error|"
    r"search failed|the command took too long|the search term is too broad)",
    re.IGNORECASE,
)


def result_failed(result: str) -> bool:
    """The tools return plain strings. These are the prefixes they use when something went wrong."""
    return bool(_FAIL_RE.match(result or ""))


def _speakable(text: str, limit: int = 350) -> str:
    text = re.sub(r"[*`#]", "", (text or "").replace("_", " "))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: last + 1] if last > 100 else cut.rstrip() + "...")


# ------------------------------------------------------------------ Minecraft
# Game words are only claimed while she is actually standing in the world (or when "minecraft" is said),
# so "come here" or "stop" in normal life still reach the normal handlers / the model.
_MC_WORD = re.compile(r"\bminecraft\b|\bin[- ]game\b")
_MC_RULES = [
    (re.compile(r"(?:follow me|follow|come with me|stick with me|stay with me|walk with me)"), "follow"),
    (re.compile(r"(?:come(?: over)?(?: here| to me)?|get over here|come back|over here)"), "come"),
    (re.compile(r"(?:stop|cancel|halt|abort|stand down|never ?mind|stop (?:that|it|what you(?:'re| are) doing)|stop (?:following|fighting|mining|collecting))"), "stop"),
    (re.compile(r"(?:stay|wait|stay (?:here|there|put)|wait (?:here|there)|hold (?:on|still|position))"), "stay"),
    (re.compile(r"(?:guard me|protect me|watch my back|guard|defend me|keep me safe|cover me)"), "guard"),
    (re.compile(r"(?:go home|head home|return home|take me home|go back home|go to (?:your )?base)"), "home"),
    (re.compile(r"(?:set (?:your |our |the )?home(?: here)?|remember (?:this|here) as home|this is home)"), "sethome"),
    (re.compile(r"(?:jump|jump up|jump for me|hop|do a jump|jump once|jump a little|leap|jump up and down)"), "jump"),
    (re.compile(r"(?:go to sleep|sleep|go sleep|time to sleep|get in bed|use the bed)"), "sleep"),
    (re.compile(r"(?:eat|eat something|have some food|get some food|you should eat)"), "eat"),
    (re.compile(r"(?:what(?:'s| is| do you have) (?:in )?your (?:inventory|bag|backpack)|show (?:me )?your inventory|what do you have|what(?:'s| is) in your bag|inventory)"), "inventory"),
    (re.compile(r"(?:how are you doing|how(?:'s| is) your health|status|status report|are you ok|are you okay|are you hurt)"), "status"),
    (re.compile(r"(?:where are you|where am i|your position|coordinates)"), "where"),
]
_MC_ARG_RULES = [
    (re.compile(r"(?:jump|hop)\s+(\d+|two|three|four|five)(?:\s+times)?"), lambda m: "jump " + m.group(1)),
    (re.compile(r"(?:go to|goto|walk to|run to|head to|travel to)\s+(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)"), lambda m: "goto %s %s %s" % m.groups()),
    (re.compile(r"(?:attack|kill|fight|slay|destroy|get rid of)\s+(?:the\s+|that\s+|those\s+|nearby\s+|nearest\s+)*(.+)"), lambda m: "attack " + m.group(1)),
    (re.compile(r"(?:hunt|go hunting for|hunt for|find me some|get me some meat from)\s+(?:the\s+|some\s+)*(.+)"), lambda m: "hunt " + m.group(1)),
    (re.compile(r"(?:go hunting|hunt (?:some )?(?:food|animals))"), lambda m: "hunt"),
    (re.compile(r"(?:collect|mine|gather|chop|get me|get|grab|fetch|harvest|dig(?: up)?|cut(?: down)?|farm)\s+(?:me\s+)?(?:some\s+|the\s+|a\s+|an\s+)*(.+)"), lambda m: "collect " + m.group(1)),
    (re.compile(r"(?:craft|make|build|create)\s+(?:me\s+)?(?:some\s+|a\s+|an\s+|the\s+)*(.+)"), lambda m: "craft " + m.group(1)),
    (re.compile(r"(?:give me|hand me|toss me|drop for me|throw me|pass me)\s+(?:some\s+|the\s+|a\s+|an\s+)*(.+)"), lambda m: "give " + m.group(1)),
    (re.compile(r"(?:tell everyone|say in chat|tell them|chat|say in the chat|write in chat)\s*[:,]?\s+(.+)"), lambda m: "say " + m.group(1)),
]
# things that are clearly about the real computer, never the game
_MC_NOT_GAME = re.compile(r"\b(?:file|folder|project|code|spotify|song|music|video|website|page|screenshot|photo|webcam|wikipedia|document|presentation|slide|app|terminal)\b")


def match_minecraft(text: str) -> Optional[str]:
    """Returns the bot command for a clear game request, else None. Only called when it makes sense."""
    s = _clean(text)
    if not s:
        return None
    s = re.sub(r"\s+(?:in|on) minecraft$", "", s)
    s = re.sub(r"\bminecraft\b\s*", "", s).strip()
    s = re.sub(r"(?:\s+(?:with me|for me|please|now|real quick))+$", "", s).strip()
    s = re.sub(r"^(?:and|then)\s+", "", s)
    for pattern, command in _MC_RULES:
        if pattern.fullmatch(s):
            return command
    if _MC_NOT_GAME.search(s):
        return None
    for pattern, build in _MC_ARG_RULES:
        m = pattern.fullmatch(s)
        if m and m.group(1 if m.re.groups else 0).strip():
            return build(m)
    return None


def _minecraft_active(text: str) -> bool:
    """Is it reasonable to treat this sentence as a game command?"""
    try:
        import minecraft_agent
        s = (text or "").lower()
        if _MC_WORD.search(s):
            return True
        return minecraft_agent.is_connected()
    except Exception:
        return False


_WAKE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|yo)[\s,]+)?(?:remielle|remiel|remi|rimiel|remy|ramiel)\b[\s,.:!?-]*",
    re.IGNORECASE,
)
_PREFIX = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+|i(?:'d| would)?\s+(?:want|need|like)\s+you\s+to\s+|"
    r"go\s+ahead\s+and\s+|please\s+|just\s+|let's\s+|hey\s+|ok(?:ay)?\s+|so\s+|now\s+|and\s+|then\s+)+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"(?:\s+(?:for me|please|now|right now|real quick|quickly|thanks|thank you|okay|ok))+$", re.IGNORECASE)


def _clean(text: str) -> str:
    s = (text or "").lower().replace("’", "'")
    s = re.sub(r"[\"`“”]", "", s).strip()
    s = _WAKE.sub("", s)
    s = re.sub(r"[\s.!?,;:]+$", "", s)
    s = _PREFIX.sub("", s)
    s = _SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------------------- patterns

_MEDIA_NOUN = r"(?: the| my| this)?(?: music| song| video| media| playback| spotify| youtube| track)?"
_MEDIA = [
    (rf"(?:pause|stop|hold){_MEDIA_NOUN}", "pause"),
    (rf"(?:resume|unpause|continue|play){_MEDIA_NOUN}", "play"),
    (r"(?:skip|next)(?: the| this)?(?: song| track| video)?|play (?:the )?next(?: song| track)?|skip to (?:the )?next.*", "next"),
    (r"(?:previous|go back|last|back)(?: song| track)?|play (?:the )?(?:previous|last)(?: song| track)?", "previous"),
    (r"turn (?:the )?(?:volume )?up|turn it up|volume up|louder|(?:increase|raise) (?:the )?volume|make it louder", "volume up"),
    (r"turn (?:the )?(?:volume )?down|turn it down|volume down|quieter|(?:lower|decrease|reduce) (?:the )?volume|make it quieter", "volume down"),
    (r"(?:mute|unmute)(?: the| my)?(?: volume| sound| audio| music| computer| pc| laptop)?", "mute"),
]
_MEDIA_RE = [(re.compile(p, re.IGNORECASE), a) for p, a in _MEDIA]

_WEBCAM = re.compile(
    r"look at me|see me|do you see me|"
    r"(?:use|open|start|turn on|activate)\s+(?:the\s+|my\s+)?(?:web\s?cam|camera)|"
    r"look through (?:the |my )?(?:web\s?cam|camera)|"
    r"(?:take|snap|capture)\s+(?:a\s+|my\s+)?(?:photo|picture|pic|selfie)(?:\s+of me)?",
    re.IGNORECASE,
)

_SCREENSHOT = re.compile(
    r"(?:take|grab|capture|get|make|snap|do|save|shoot)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+|my\s+)?(?:quick\s+)?"
    r"(?:screenshot|screen\s?shot|screen\s?capture|(?:picture|photo|image) of (?:my|the) screen|(?:my|the) screen)"
    r"(?:\s+of (?:my|the) screen)?|screenshot|screen\s?shot",
    re.IGNORECASE,
)

_WIKI = [
    re.compile(r"^(?:search|look up|lookup|find|check|google)?\s*(?:on\s+)?(?:wikipedia|wiki)\s*(?:for|about|on)?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:search|look up|lookup|find|check)\s+(?:for\s+)?(.+?)\s+(?:on|in|from|using)\s+(?:wikipedia|wiki)$", re.IGNORECASE),
]

_LIST = re.compile(
    r"^(?:list|show|display)(?: me)?(?: all)?(?: the)? (?:files|folders|contents|items)"
    r"(?: and (?:files|folders))?(?: (?:in|of|inside|on|at|from))?(?: the| my)?\s*(.*)$",
    re.IGNORECASE,
)
_LIST_WHATS_IN = re.compile(r"^what(?:'s| is) (?:in|inside|on) (?:the |my )?(.+?)(?: folder| directory)?$", re.IGNORECASE)
_HOME_FOLDERS = {"desktop", "downloads", "documents", "pictures", "music", "videos"}

_OPEN = re.compile(
    r"^(?:open|launch|start|run|fire up|pull up|bring up|go to|goto|take me to|navigate to|visit|load|show me)\s+(?:up\s+)?(.+)$",
    re.IGNORECASE,
)


# "play blinding lights", "play some jazz on spotify", "search for X on spotify", "spotify X"
_SPOTIFY_PLAY = [
    re.compile(r"^(?:play|put on|queue|search(?: for)?|find)\s+(.+?)\s+on spotify$", re.IGNORECASE),
    re.compile(r"^(?:play|put on|queue)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^spotify\s+(.+)$", re.IGNORECASE),
]

def _tidy_target(raw: str) -> str:
    t = raw.strip()
    t = re.sub(r"^(?:up\s+)?(?:the\s+|my\s+|a\s+|an\s+)+", "", t)
    t = re.sub(r"(?:\s+(?:app|application|website|web site|site|page|program|window|please))+$", "", t)
    t = re.sub(r"\s+dot\s+", ".", t)          # "youtube dot com" -> "youtube.com"
    return t.strip()


def _folder_arg(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"^(?:the |my )", "", name)
    name = re.sub(r"(?: folder| directory)$", "", name).strip()
    if not name or name in {"current", "this", "here", "project", "working", "current folder", "this folder"}:
        return "."
    if name in _HOME_FOLDERS:
        return str(Path.home() / name.title())
    return name


# ----------------------------------------------------------------------------- Minecraft mode
# The one toggle: the game, her bot and her own goals all at once. Only whole, clear sentences are
# claimed here - anything subtler reaches the model, which has the MODE: line.

_MCN = r"(?:minecraft|mc|game)"
_MC_MODE_ON = re.compile(
    r"(?:(?:the\s+|our\s+|her\s+)?" + _MCN + r"\s+mode\s+(?:on|up)|"
    r"(?:turn|switch|set|put|go)\s+(?:on|into|to|up)\s+(?:the\s+|our\s+)?" + _MCN + r"(?:\s+mode)?|"
    r"(?:turn|switch|set|put)\s+(?:the\s+|our\s+)?" + _MCN + r"(?:\s+mode)?\s+(?:on|up)|"
    r"(?:start|launch|boot)(?:\s+up)?\s+(?:the\s+|our\s+)?(?:minecraft|mc)(?:\s+game)?|"
    r"(?:join|enter|reach)(?:\s+in)?(?:\s+to)?\s+(?:me\s+in\s+)?(?:the\s+|our\s+)?(?:game|world|minecraft)|"
    r"(?:come|get)\s+(?:on|in|into|to|over\s+to)\s+(?:the\s+|our\s+)?(?:game|world|minecraft)|"
    r"play\s+minecraft(?:\s+(?:with|by|on)\s+(?:me|your(?:self|s+own)|your\s+own))?|"
    r"(?:bring|take)\s+(?:her|yourself|you|remielle)\s+into\s+(?:the\s+)?(?:game|world|minecraft))",
    re.IGNORECASE)
_MC_MODE_OFF = re.compile(
    r"(?:(?:the\s+|our\s+|her\s+)?" + _MCN + r"\s+mode\s+(?:off|down)|"
    r"(?:turn|switch|set|put)\s+(?:off|down)\s+(?:the\s+|our\s+)?" + _MCN + r"(?:\s+mode)?|"
    r"(?:turn|switch|set|put)\s+(?:the\s+|our\s+)?" + _MCN + r"(?:\s+mode)?\s+(?:off|down)|"
    r"stop\s+(?:playing\s+|the\s+|our\s+)?(?:minecraft|mc|game)|"
    r"(?:leave|quit|exit|close)(?:\s+out\s+of)?\s+(?:the\s+|our\s+)?(?:game|world|minecraft)|"
    r"go\s+(?:back|out)\s+(?:out\s+of\s+|of\s+)?(?:the\s+|our\s+)?(?:game|minecraft))",
    re.IGNORECASE)
_MC_MODE_STATUS = re.compile(
    r"(?:(?:what(?:'s| is)?\s+)?(?:the\s+|our\s+)?(?:minecraft|mc|game)\s+mode(?:\s+(?:status|state|now))?|"
    r"(?:is|are)\s+(?:the\s+|our\s+|her\s+)?" + _MCN + r"(?:\s+mode)?(?:\s+(?:on|off|up|running|set))?|"
    r"(?:are|do|is)\s+you\s+(?:in\s+(?:the\s+|our\s+)?(?:game|world|minecraft)|playing\s+minecraft))",
    re.IGNORECASE)


def match_mc_mode(text: str) -> Optional[str]:
    """'on' / 'off' / 'status' when the sentence asks for the whole Minecraft switch, else None."""
    s = _clean(text)
    if not s:
        return None
    if _MC_MODE_OFF.fullmatch(s) or _MC_MODE_ON.fullmatch(s):
        # "minecraft mode off" says both 'mode' and 'off'; only a bare "minecraft mode" is a question.
        return "off" if _MC_MODE_OFF.fullmatch(s) else "on"
    if _MC_MODE_STATUS.fullmatch(s):
        return "status"
    return None


# ----------------------------------------------------------------------------- initiative
# "explore on your own" is a mode switch, not one more game command: it turns her self-chosen goals on, and
# any phrasing this list misses still reaches the model, which has the INITIATIVE: line.

_INITIATIVE_ON = re.compile(
    r"(?:go\s+)?(?:on\s+your\s+own|explore(?:\s+(?:around|the\s+world))?(?:\s+on\s+your\s+own)?|"
    r"be\s+proactive|use\s+your(?:\s+own)?\s+initiative|think\s+for\s+yourself|do\s+your\s+own\s+thing|"
    r"surprise\s+me|keep\s+yourself\s+busy|play\s+(?:by|on)\s+your(?:self|s+own)|work\s+on\s+your\s+own)",
    re.IGNORECASE)
_INITIATIVE_OFF = re.compile(
    r"(?:stop|quit|cancel|end)\s+(?:exploring|being\s+proactive|your\s+initiative|thinking\s+on\s+your\s+own|"
    r"being\s+on\s+your\s+own|self[-\s]?directed\s+things)|"
    r"(?:don'?t|do\s+not)\s+(?:be\s+proactive|think\s+for\s+yourself|act\s+on\s+your\s+own|explore\s+on\s+your\s+own|"
    r"make\s+your\s+own\s+(?:decisions|goals))|"
    r"(?:turn|switch)\s+off\s+(?:her\s+|your\s+)?(?:initiative|proactivity|self[-\s]?mode)|initiative\s+off",
    re.IGNORECASE)
_INITIATIVE_STATUS = re.compile(
    r"(?:is|are)\s+you\s+(?:proactive|on\s+your\s+own|thinking\s+by\s+yourself|doing\s+your\s+own\s+things)|"
    r"initiative\s+(?:status|mode|state)|what\s+(?:do\s+you\s+do|are\s+you\s+doing)\s+on\s+your\s+own",
    re.IGNORECASE)


def match_initiative(text: str) -> Optional[str]:
    """'on' / 'off' / 'status' when the sentence is about her doing things by herself, else None."""
    s = _clean(text)
    if not s:
        return None
    if _INITIATIVE_ON.fullmatch(s):
        return "on"
    if _INITIATIVE_OFF.fullmatch(s):
        return "off"
    if _INITIATIVE_STATUS.fullmatch(s):
        return "status"
    return None


# ----------------------------------------------------------------------------- matching

def match_intent(text: str) -> Optional[tuple]:
    """Returns (tool_name, kwargs) for a clear command, otherwise None (let the model handle it)."""
    s = _clean(text)
    if not s:
        return None

    mode = match_mc_mode(text)
    if mode:
        return "minecraft_mode", {"mode": mode}

    mode = match_initiative(text)
    if mode:
        return "minecraft_initiative", {"mode": mode}

    if "minecraft_command" in AVAILABLE_FUNCTIONS and _minecraft_active(text):
        command = match_minecraft(text)
        if command:
            return "minecraft_command", {"command": command}

    for pattern, action in _MEDIA_RE:
        if pattern.fullmatch(s):
            return "control_media", {"action": action}

    if _WEBCAM.fullmatch(s):
        return "analyze_webcam", {}

    if _SCREENSHOT.fullmatch(s):
        return "take_screenshot", {"filename": f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"}

    for pattern in _WIKI:
        m = pattern.match(s)
        if m and m.group(1).strip():
            return "search_wikipedia", {"query": m.group(1).strip()}

    m = _LIST.match(s)
    if m:
        return "list_directory", {"directory_path": _folder_arg(m.group(1))}
    m = _LIST_WHATS_IN.match(s)
    if m and (m.group(1).strip().lower() in _HOME_FOLDERS or " folder" in s or " directory" in s):
        return "list_directory", {"directory_path": _folder_arg(m.group(1))}

    for pattern in _SPOTIFY_PLAY:
        m = pattern.match(s)
        if m and m.group(1).strip():
            return "play_spotify_music", {"query": m.group(1).strip()}

    m = _OPEN.match(s)
    if m:
        target = _tidy_target(m.group(1))
        # Only claim it when we know what it is, so "open my heart" or "start listening" still go to the model.
        if target and can_open(target):
            return "open_target", {"target": target}

    return None


# ----------------------------------------------------------------------------- replies

_MEDIA_SAY = {
    "pause": "Paused.", "play": "Playing.", "next": "Skipping to the next track.", "previous": "Going back.",
    "volume up": "Turning it up.", "volume down": "Turning it down.", "mute": "Toggled mute.",
}


def describe_result(tool: str, args: dict, result: str) -> str:
    """Builds the spoken reply from what ACTUALLY happened, so she never claims something that failed."""
    args = args or {}
    result = str(result or "")
    failed = result_failed(result)

    if tool == "open_target":
        target = str(args.get("target", "that")).strip()
        if failed:
            return f"[sad] I couldn't open {target}. {_speakable(result, 120)}"
        return f"[happy] Opening {target} right now!"

    if tool == "take_screenshot":
        if failed:
            return f"[sad] I couldn't take the screenshot. {_speakable(result, 120)}"
        return "[happy] Done! I took a screenshot and saved it to your Desktop."

    if tool == "analyze_webcam":
        if failed:
            return f"[sad] I couldn't use the webcam. {_speakable(result, 120)}"
        return "[surprised] I can see you now! I saved the photo to your Desktop."

    if tool == "control_media":
        if failed:
            return f"[sad] {_speakable(result, 160)}"
        return f"[relaxed] {_MEDIA_SAY.get(str(args.get('action', '')).lower(), 'Done.')}"

    if tool == "search_wikipedia":
        if failed:
            return f"[sad] {_speakable(result)}"
        return f"[happy] Here is what I found. {_speakable(result)}"

    if tool == "list_directory":
        if failed:
            return f"[sad] {_speakable(result, 160)}"
        names = re.findall(r"^- (.+?) \((?:Folder|File)\)$", result, re.MULTILINE)
        folder = str(args.get("directory_path", "."))
        where = "the current folder" if folder in (".", "") else Path(folder).name or folder
        if not names:
            return f"[relaxed] {where} is empty."
        more = " and more" if len(names) > 6 else ""
        return f"[relaxed] There are {len(names)} items in {where}: {', '.join(names[:6])}{more}."

    if tool == "play_spotify_music":
        if str(result).startswith(("Playing", "Opening")):
            return f"[happy] {_speakable(result, 200)}"
        return f"[sad] {_speakable(result, 200)}"

    if tool == "minecraft_command":
        command = str(args.get("command", ""))
        message = re.sub(r"^error:\s*", "", result, flags=re.IGNORECASE)
        if failed or result.lower().startswith("error"):
            return f"[sad] {_speakable(message, 220)}"
        if command.split(" ")[0] in ("inventory", "status", "where", "help"):
            return f"[relaxed] {_speakable(message, 300)}"
        return f"[happy] {_speakable(message, 220)}"

    if tool == "minecraft_initiative":
        return f"[sad] {_speakable(result, 200)}" if failed else f"[happy] {_speakable(result, 200)}"

    if tool == "minecraft_mode":
        message = re.sub(r"^failed:\s*", "", result, flags=re.IGNORECASE)
        if failed:
            return f"[sad] {_speakable(message, 260)}"
        if str(args.get("mode", "")).lower() in ("status", "state", "off"):
            return f"[relaxed] {_speakable(message, 260)}"
        return f"[happy] {_speakable(message, 260)}"

    if tool == "minecraft_goal":
        message = re.sub(r"^failed:\s*", "", result, flags=re.IGNORECASE)
        if failed:
            return f"[sad] {_speakable(message, 260)}"
        return f"[happy] {_speakable(message, 260)}"

    if tool == "create_project_file":
        name = args.get("filename", "the file")
        if failed:
            return f"[sad] I couldn't write {name}. {_speakable(result, 120)}"
        return f"[happy] I wrote the {name} file for you!"

    if tool == "run_terminal_command":
        if failed:
            return f"[sad] That command didn't work. {_speakable(result, 200)}"
        return f"[relaxed] I ran that command in the terminal! {_speakable(result, 250)}"

    return f"[relaxed] {_speakable(result)}"


def route_intent(user_text: str) -> Optional[str]:
    """Runs a clear command immediately. Returns the reply (with an emotion tag), or None if not a command."""
    hit = match_intent(user_text)
    if not hit:
        return None
    tool, args = hit
    print(f"⚡ [Router] {tool} {args}")
    try:
        result = str(AVAILABLE_FUNCTIONS[tool](**args))
    except Exception as e:
        result = f"Error: {e}"
    return describe_result(tool, args, result)