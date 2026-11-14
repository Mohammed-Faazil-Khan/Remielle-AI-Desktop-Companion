import os
import re
import sys
import threading
import time
from collections import deque

import pyaudio
import numpy as np
from faster_whisper import WhisperModel

import remielle_config  # noqa: F401  (loads the .env file so the two settings below can be set there)

# ======================= SETTINGS YOU CAN TUNE =======================
# Both can also be set in .env:  WHISPER_DEVICE=cpu  (frees GPU memory for GPT-SoVITS)   WHISPER_MODEL=base.en
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small.en")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
GPU_COMPUTE_TYPE = "int8_float16"
CPU_COMPUTE_TYPE = "int8"
CPU_THREADS = 8

MIC_DEVICE_INDEX = None

INITIAL_PROMPT = (
    "Conversation with Remielle, an AI assistant. Faazil asks her to open YouTube, Spotify, Chrome "
    "or VS Code, and talks about Python, machine learning and GitHub."
)

PRE_ROLL_SECONDS = 0.5
TAIL_SECONDS = 0.35
HIGHPASS_HZ = 80
TARGET_LEVEL = 0.6
MAX_GAIN = 30.0
# ====================================================================

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024
RECORD_SECONDS = 10

SILENCE_PEAK = 150


def _add_cuda_dll_dirs() -> None:
    """Windows: make the cuBLAS / cuDNN DLLs findable using your exact PyTorch path."""
    if os.name != "nt":
        return

    # Injected PyTorch CUDA directory path
    manual_path = r"C:\Users\Faazil Khan\PycharmProjects\.venv\Lib\site-packages\torch\lib"
    if os.path.isdir(manual_path):
        try:
            os.add_dll_directory(manual_path)
        except OSError:
            pass
        os.environ["PATH"] = manual_path + os.pathsep + os.environ.get("PATH", "")

    try:
        import nvidia
    except ImportError:
        return
    for base in list(getattr(nvidia, "__path__", [])):
        for sub in ("cublas", "cudnn"):
            d = os.path.join(base, sub, "bin")
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _load_model() -> WhisperModel:
    if DEVICE in ("cuda", "auto"):
        _add_cuda_dll_dirs()
        try:
            import ctranslate2
            gpus = ctranslate2.get_cuda_device_count()
        except Exception as e:
            gpus = 0
            print(f"⚠️ Could not query CUDA: {e}")
        if gpus > 0:
            try:
                print(f"Loading Whisper model ({MODEL_SIZE}) on the GPU...")
                m = WhisperModel(MODEL_SIZE, device="cuda", compute_type=GPU_COMPUTE_TYPE)
                segments, _ = m.transcribe(np.zeros(RATE, dtype=np.float32), language="en")
                list(segments)
                print(f"✅ Whisper {MODEL_SIZE} is running on the GPU ({GPU_COMPUTE_TYPE})")
                return m
            except Exception as e:
                print(f"⚠️ GPU failed ({e}). Falling back to the CPU.")
        else:
            print("⚠️ No CUDA GPU is visible to CTranslate2. Falling back to the CPU.")
    print(f"Loading Whisper model ({MODEL_SIZE}) on the CPU...")
    m = WhisperModel(MODEL_SIZE, device="cpu", compute_type=CPU_COMPUTE_TYPE, cpu_threads=CPU_THREADS)
    print(f"✅ Whisper {MODEL_SIZE} is running on the CPU ({CPU_COMPUTE_TYPE})")
    return m


model = _load_model()


def _clean_audio(audio_int16: np.ndarray) -> np.ndarray:
    x = audio_int16.astype(np.float32) / 32768.0
    if len(x) == 0:
        return x
    x -= float(np.mean(x))

    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / RATE)
    spec *= np.clip((freqs - (HIGHPASS_HZ - 20)) / 40.0, 0.0, 1.0)
    x = np.fft.irfft(spec, n=len(x)).astype(np.float32)

    ref = float(np.percentile(np.abs(x), 99.5))
    if ref > 1e-4:
        x *= min(TARGET_LEVEL / ref, MAX_GAIN)
    return np.clip(x, -1.0, 1.0)


_PROMPT_START = re.sub(r"[^a-z ]", "", INITIAL_PROMPT.lower())[:40]
_JUNK = {"", "thanks for watching", "thank you for watching", "please subscribe",
         "subtitles by the amara org community"}


def _is_junk(text: str) -> bool:
    t = re.sub(r"[^a-z ]", "", text.lower()).strip()
    return t in _JUNK or (len(t) > 20 and t.startswith(_PROMPT_START))


def _transcribe(audio_int16: np.ndarray) -> str:
    audio = _clean_audio(audio_int16)
    segments, info = model.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
        condition_on_previous_text=False,
        initial_prompt=INITIAL_PROMPT,
        without_timestamps=True,
    )
    parts = []
    for seg in segments:
        if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
            continue
        parts.append(seg.text)
    text = "".join(parts).strip()
    return "" if _is_junk(text) else text


def transcribe_pcm16(audio_int16, min_seconds: float = 0.4, label: str = "microphone", hint: str = "") -> str:
    """Cleaning, level checks and Whisper for 16 kHz mono PCM16 from any source: this PC's mic or your phone.

    Returns '' for a tap too short to be a sentence, and raises when the audio was pure silence.
    """
    if isinstance(audio_int16, (bytes, bytearray, memoryview)):
        audio_int16 = np.frombuffer(audio_int16, dtype=np.int16)
    seconds = len(audio_int16) / RATE
    peak = int(np.abs(audio_int16).max()) if len(audio_int16) else 0
    print(f"🎙️ {label}: {seconds:.1f}s recorded, peak level {peak}")
    if seconds < min_seconds:
        return ""
    if peak < SILENCE_PEAK:
        raise RuntimeError(
            f"{label} captured nothing but silence." + (f" {hint}" if hint else
            " Check Windows Settings > Privacy > Microphone (allow desktop apps), the default input device, "
            "and any Fn mic-mute key.")
        )
    return _transcribe(audio_int16)


def list_input_devices() -> None:
    pa = pyaudio.PyAudio()
    try:
        print("Input devices (put the index in MIC_DEVICE_INDEX):")
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                print(f"  [{i}] {info['name']}  ({int(info['defaultSampleRate'])} Hz)")
    finally:
        pa.terminate()


def _open_kwargs() -> dict:
    kwargs = dict(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    if MIC_DEVICE_INDEX is not None:
        kwargs["input_device_index"] = MIC_DEVICE_INDEX
    return kwargs


def listen_and_transcribe() -> str:
    audio = pyaudio.PyAudio()
    stream = audio.open(**_open_kwargs())

    print("\n🎙️ Listening...")
    frames = []

    try:
        for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(np.frombuffer(data, dtype=np.int16))
    finally:
        print("⏸️ Processing audio...")
        stream.stop_stream()
        stream.close()
        audio.terminate()

    return _transcribe(np.concatenate(frames))


class PushToTalkRecorder:
    def __init__(self):
        self._lock = threading.Lock()
        self._ring = deque(maxlen=max(1, int(PRE_ROLL_SECONDS * RATE / CHUNK) + 1))
        self._frames = []
        self._recording = False
        self._thread = None
        self._error = None
        self._ready = threading.Event()
        self._open_stream_async()

    @property
    def active(self) -> bool:
        return self._recording

    def _open_stream_async(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(**_open_kwargs())
            self._ready.set()
            while True:
                data = stream.read(CHUNK, exception_on_overflow=False)
                chunk = np.frombuffer(data, dtype=np.int16)
                with self._lock:
                    if self._recording:
                        self._frames.append(chunk)
                    else:
                        self._ring.append(chunk)
        except Exception as e:
            self._error = str(e)
        finally:
            self._ready.set()
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pa.terminate()

    def start(self) -> None:
        if self._recording:
            return
        self._open_stream_async()
        self._ready.wait(timeout=3)
        if self._error:
            raise RuntimeError(f"Could not open microphone: {self._error}")
        with self._lock:
            self._frames = list(self._ring)
            self._ring.clear()
            self._recording = True

    def stop_and_transcribe(self) -> str:
        time.sleep(TAIL_SECONDS)
        with self._lock:
            self._recording = False
            frames, self._frames = self._frames, []
        if self._error and not frames:
            raise RuntimeError(f"Microphone error: {self._error}")
        if not frames:
            return ""
        return transcribe_pcm16(np.concatenate(frames),
                                min_seconds=0.4 + PRE_ROLL_SECONDS + TAIL_SECONDS,
                                label="this computer")


if __name__ == "__main__":
    if "--devices" in sys.argv:
        list_input_devices()
        sys.exit(0)
    print("Testing STT module independently. Press Ctrl+C to stop.")
    while True:
        try:
            transcribed_text = listen_and_transcribe()
            if transcribed_text:
                print(f"✅ Model heard: {transcribed_text}")
        except KeyboardInterrupt:
            print("\n🛑 Test stopped.")
            break