import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

import remielle_config as cfg  # loads .env

TTS_URL = os.environ.get("SOVITS_URL", "http://127.0.0.1:9880/tts")
TTS_TIMEOUT = cfg.env_int("REMIELLE_TTS_TIMEOUT", 120)  # seconds
# Optional speed knob for GPT-SoVITS v3/v4: fewer sampling steps = faster, slightly rougher voice (the default is 32).
# Put SOVITS_SAMPLE_STEPS=8 in .env to try it. It is only sent when you set it.
SAMPLE_STEPS = cfg.env_int("SOVITS_SAMPLE_STEPS", 0)
KEEP_LAST_FILES = 6
# How her voice spells "Faazil". Real English words ("Fa zeal", "Fuzz ill") are pronounced reliably;
# invented spellings ("Fazeel", "zil") get guessed and can come out garbled or spelled letter by letter.
SPOKEN_NAME = os.environ.get("REMIELLE_SPOKEN_NAME", "").strip() or "Fa zeal"

_PROJECT_DIR = Path(__file__).resolve().parent
SOVITS_DIR = Path(r"C:\GPT-SoVITS\GPT-SoVITS-v4-20250422fix")

DEFAULT_REF = SOVITS_DIR / "remielle_clean.wav"
DEFAULT_PROMPT = "After all this time, shouldn't you be a little more welcoming of your old friend here? Truth is, I wasn't really sure how to greet you."

EMOTION_MAP = {
    "happy": {
        "audio": SOVITS_DIR / "happy.wav",
        "prompt": "INSERT THE EXACT TRANSCRIPT OF HAPPY.WAV HERE"
    },
    "sad": {
        "audio": SOVITS_DIR / "sad.wav",
        "prompt": "INSERT THE EXACT TRANSCRIPT OF SAD.WAV HERE"
    },
    "angry": {
        "audio": SOVITS_DIR / "angry.wav",
        "prompt": "INSERT THE EXACT TRANSCRIPT OF ANGRY.WAV HERE"
    },
    "surprised": {
        "audio": SOVITS_DIR / "surprised.wav",
        "prompt": "INSERT THE EXACT TRANSCRIPT OF SURPRISED.WAV HERE"
    }
}


def _resolve_ref_and_prompt(target_emotion: Optional[str]) -> tuple:
    if target_emotion in EMOTION_MAP:
        cfg_entry = EMOTION_MAP[target_emotion]
        audio_file = cfg_entry["audio"]

        if audio_file.exists():
            ref_path = str(audio_file)
        elif (_PROJECT_DIR / "static" / audio_file.name).exists():
            ref_path = str(_PROJECT_DIR / "static" / audio_file.name)
        else:
            ref_path = None

        if ref_path is not None and not cfg_entry["prompt"].startswith("INSERT THE EXACT"):
            return ref_path, cfg_entry["prompt"]

    if DEFAULT_REF.exists():
        return str(DEFAULT_REF), DEFAULT_PROMPT

    fallback_static = _PROJECT_DIR / "static" / "remielle_sample.wav"
    return str(fallback_static), DEFAULT_PROMPT


def clean_text_for_speech(text: str) -> str:
    """Sanitizes text so TTS reads URLs, symbols, ALL-CAPS, and names naturally."""
    # Phonetic fix for your name. Never use a hyphen here: the voice reads "-" out loud as "minus".
    # Change the spelling in .env (REMIELLE_SPOKEN_NAME=...) until she says it the way you like.
    text = re.sub(r'\bFaazil\b', SPOKEN_NAME, text, flags=re.IGNORECASE)

    # Convert words written in ALL CAPS into normal lowercase text so they aren't spelled out letter-by-letter
    text = re.sub(r'\b[A-Z]{2,}\b', lambda m: m.group(0).lower(), text)

    # Clean URLs and extensions
    text = re.sub(r'\bwww\.', 'w w w dot ', text, flags=re.IGNORECASE)
    extensions = ['com', 'org', 'net', 'io', 'co', 'in', 'edu']
    for ext in extensions:
        text = re.sub(rf'\.{ext}\b', f' dot {ext}', text, flags=re.IGNORECASE)

    return text


def _cleanup_old_files(folder: Path) -> None:
    """Keeps only the newest few generated speech files (and tries to remove the old single speech.wav)."""
    try:
        files = sorted(folder.glob("speech_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[KEEP_LAST_FILES:]:
            try:
                old.unlink()
            except OSError:
                pass   # still being played by the browser: it will be removed next time
        legacy = folder / "speech.wav"
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
    except OSError:
        pass


def play_anime_voice(text: str, output_path: Optional[str] = None) -> Optional[str]:
    """Synthesises `text` with GPT-SoVITS and returns the path of the new wav file, or None if it failed.

    Every reply gets its own file name. Before, one shared speech.wav was deleted and rewritten each time; if the
    browser still had it open (or the delete failed) the NEW reply was never generated and the OLD audio was
    served again, so she could say something different from her text.
    """
    detected_emotion = None
    text = text or ""

    emotion_match = re.search(r'^\[([a-zA-Z]+)\]', text)
    if emotion_match:
        detected_emotion = emotion_match.group(1).lower()
        text = re.sub(r'^\[[a-zA-Z]+\]\s*', '', text).strip()

    # Clean text for phonetic reading
    text = clean_text_for_speech(text).strip()
    if not text:
        print("⚠️ Nothing to say (empty text), skipping TTS.")
        return None

    ref_audio_path, prompt_text = _resolve_ref_and_prompt(detected_emotion)

    payload = {
        "text": text,
        "text_lang": "en",
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text,
        "prompt_lang": "en",
        "text_split_method": "cut5",
        "media_type": "wav",
        "top_k": 10,
        "top_p": 1.0,
        "temperature": 0.6
    }
    if SAMPLE_STEPS > 0:
        payload["sample_steps"] = SAMPLE_STEPS

    if output_path is None:
        output_path = os.path.join("static", f"speech_{int(time.time() * 1000)}.wav")

    started = time.time()
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        print(f"⚡ Sending request to GPT-SoVITS (Emotion: {detected_emotion or 'default'}, {len(text)} chars)...")
        response = requests.post(url=TTS_URL, json=payload, timeout=TTS_TIMEOUT)

        if response.status_code == 200 and len(response.content) > 1000:
            partial = output_path + ".part"
            with open(partial, "wb") as f:
                f.write(response.content)
            os.replace(partial, output_path)   # the browser never sees a half-written file
            print(f"✅ Speech saved to {output_path} ({time.time() - started:.1f}s)")
            _cleanup_old_files(Path(output_path).parent)
            return output_path

        print(f"❌ GPT-SoVITS API error {response.status_code}: {response.text[:300]}")

    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to GPT-SoVITS - is api_v2.py running on port 9880? (Start_Voice_Server.bat)")
    except requests.exceptions.Timeout:
        print(f"❌ GPT-SoVITS did not respond within {TTS_TIMEOUT}s.")
    except Exception as e:
        print(f"❌ Error communicating with GPT-SoVITS: {e}")
    return None


if __name__ == "__main__":
    print(play_anime_voice("[happy] I am fully updated and ready to run on the Gemini engine!"))
