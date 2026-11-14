"""Every screen Remielle is talking through, and which one of them makes the sound.

Your PC window and your phone can be open at the same time. She thinks once and answers once, but only one
device should actually speak, or you hear her twice and neither is the one you are holding. So each screen
registers itself, whoever asked her something becomes the device she answers through for a while, and every
other screen still gets the words - just muted.

Nothing in here knows about FastAPI: a client only needs a "send_json" coroutine, which makes the routing
testable without a browser or a server.
"""
import secrets
import time

DEVICES = ("pc", "phone", "web")

# Bytes of streamed microphone audio we hold: 16000 samples * 2 bytes * 15 seconds.
REMOTE_LIMIT_BYTES = 480_000
# How long a screen keeps the voice after it asks her something. Longer than any single reply.
VOICE_HOLD_SECONDS = 90.0


class Client:
    """One open page: a desktop window, a phone in the browser, or an installed home-screen app."""

    def __init__(self, websocket, client_id: str, device: str = "web", audio: bool = True):
        self.websocket = websocket
        self.id = client_id
        self.device = device if device in DEVICES else "web"
        self.wants_audio = bool(audio)
        self.speak_key = secrets.token_urlsafe(24)
        self.mic = "pc"          # "pc" listens through this computer, "phone" streams the caller's own voice in
        self.frames = []         # streamed audio waiting to be transcribed
        self.recording = False
        self.busy_until = 0.0    # this screen is listening / thinking / speaking until then
        self.alive = True

    @property
    def can_speak(self) -> bool:
        return self.alive and self.wants_audio

    def feed(self, chunk: bytes) -> bool:
        """Adds streamed microphone bytes. False when the buffer is full or nothing is being recorded."""
        if not self.recording:
            return False
        if not isinstance(chunk, (bytes, bytearray)) or not chunk:
            return False
        if sum(len(f) for f in self.frames) + len(chunk) > REMOTE_LIMIT_BYTES:
            self.recording = False
            return False
        self.frames.append(bytes(chunk))
        return True

    def start_remote(self) -> None:
        self.frames = []
        self.recording = True

    def take_remote(self) -> bytes:
        """Everything recorded from this device since it started, and stops collecting."""
        self.recording = False
        data = b"".join(self.frames)
        self.frames = []
        return data

    def __repr__(self) -> str:
        return f"<Client {self.id} {self.device} audio={self.wants_audio} mic={self.mic}>"


class Hub:
    """The set of open screens plus the voice rules that apply across them."""

    def __init__(self, now=time.monotonic):
        self.clients = []
        self.voice_id = None
        self._now = now

    # ------------------------------------------------------------------ membership
    def add(self, websocket, device: str = "web", audio: bool = True) -> Client:
        client = Client(websocket, secrets.token_hex(6), device, audio)
        self.clients.append(client)
        return client

    def drop(self, client: Client) -> None:
        client.alive = False
        client.recording = False
        client.frames = []
        if client in self.clients:
            self.clients.remove(client)
        if self.voice_id == client.id:
            self.voice_id = None

    def find(self, websocket) -> Client:
        for client in self.clients:
            if client.websocket is websocket:
                return client
        return None

    def find_by_key(self, speak_key: str) -> Client:
        """The screen that holds this speech key, or None. Keeps strangers off her voice."""
        if not speak_key:
            return None
        for client in self.clients:
            if secrets.compare_digest(client.speak_key, str(speak_key)):
                return client
        return None

    def prefer(self, device: str) -> Client:
        """Points the voice at a device type when it was not a screen that asked - the F8 key, say."""
        for client in self.clients:
            if client.device == device and client.can_speak:
                self.asked(client)
                return client
        return None

    def __len__(self) -> int:
        return len(self.clients)

    # ------------------------------------------------------------------ who owns the voice
    def asked(self, client: Client) -> None:
        """Someone spoke to her from this screen, so it is the screen she answers out loud through."""
        if client is not None and client.can_speak:
            self.voice_id = client.id

    def voice(self) -> Client:
        """The screen that should make the sound, or the first one that will take it."""
        holder = next((c for c in self.clients if c.id == self.voice_id and c.can_speak), None)
        if holder is not None:
            return holder
        return next((c for c in self.clients if c.can_speak), None) if self.clients else None

    def busy(self) -> bool:
        """True while any screen is still busy with a turn - she never talks over herself."""
        now = self._now()
        stale = [c for c in self.clients if c.busy_until and now >= c.busy_until]
        for client in stale:
            client.busy_until = 0.0
        return any(c.busy_until for c in self.clients)

    def hold_voice(self, client: Client, seconds: float = VOICE_HOLD_SECONDS) -> None:
        if client is not None:
            client.busy_until = self._now() + seconds

    def clear_busy(self, client: Client) -> None:
        if client is not None:
            client.busy_until = 0.0

    # ------------------------------------------------------------------ routing
    def route(self, payload: dict, only: Client = None) -> list:
        """What each screen receives for this message: [(client, message)], with "speak" set on one of them."""
        if only is not None:
            return [(only, dict(payload))]
        out = []
        speaker = self.voice() if payload.get("state") == "speaking" else None
        for client in list(self.clients):
            message = dict(payload)
            if payload.get("state") == "speaking":
                message["speak"] = client is speaker
            out.append((client, message))
        return out

    async def send(self, payload: dict, only: Client = None) -> int:
        """Sends to every screen (or one). A dead connection is dropped instead of raising."""
        sent = 0
        for client, message in self.route(payload, only):
            try:
                await client.websocket.send_json(message)
                sent += 1
            except Exception:
                self.drop(client)
        return sent
