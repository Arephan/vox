#!/usr/bin/env python3
import base64
import json
import os
import re
import socket
import subprocess
import tempfile
import threading

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rumps
import sounddevice as sd
import soundfile as sf
import anthropic

KOKORO_SOCK = "/tmp/kokoro-tts.sock"
SAMPLE_RATE = 16000

SYSTEM = (
    "You are Vox, a voice assistant. Give concise, conversational answers. "
    "Avoid markdown, bullet points, headers, and code blocks — speak in plain sentences. "
    "Keep answers short unless asked for detail."
)

# Keep whisper loaded for speed
print("[vox] Loading whisper...", flush=True)
from faster_whisper import WhisperModel
whisper_model = WhisperModel("base", device="auto")
print("[vox] Whisper ready", flush=True)


def _make_client():
    """Create Anthropic client using Claude Code's OAuth token."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            creds = json.loads(result.stdout.strip())
            token = creds.get("claudeAiOauth", {}).get("accessToken")
            if token:
                print("[vox] Using Claude Code OAuth token", flush=True)
                return anthropic.Anthropic(
                    auth_token=token,
                    default_headers={"anthropic-beta": "oauth-2025-04-20"},
                )
    except Exception as e:
        print(f"[vox] OAuth token error: {e}", flush=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)

    raise RuntimeError("No credentials. Set ANTHROPIC_API_KEY or log in to Claude Code.")


_client = _make_client()


def transcribe(audio_data):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio_data, SAMPLE_RATE)
        tmppath = f.name
    try:
        segments, _ = whisper_model.transcribe(tmppath, language="en")
        return " ".join(seg.text for seg in segments).strip()
    finally:
        os.unlink(tmppath)


def _send_tts(cmd, text=""):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(KOKORO_SOCK)
        sock.sendall(json.dumps({"cmd": cmd, "text": text}).encode())
        sock.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        pass


def speak(text):
    _send_tts("speak", text)


def speak_append(text):
    _send_tts("speak_append", text)


def stop_speech():
    _send_tts("stop")


def query_claude_streaming(text, model, image_path=None):
    """Stream Claude response, speaking first sentence immediately."""
    model_ids = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
    }
    model_id = model_ids.get(model, "claude-haiku-4-5-20251001")

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": text},
        ]
    else:
        content = text

    buffer = ""
    first_spoken = False

    with _client.messages.stream(
        model=model_id,
        max_tokens=512,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for chunk in stream.text_stream:
            buffer += chunk
            if not first_spoken:
                m = re.search(r'[.!?][)\'"]*\s', buffer)
                if m:
                    speak(buffer[:m.end()])
                    buffer = buffer[m.end():]
                    first_spoken = True
                elif len(buffer) > 200:
                    word_m = re.search(r'\s\S+$', buffer)
                    cut = word_m.start() if word_m else len(buffer)
                    speak(buffer[:cut])
                    buffer = buffer[cut:]
                    first_spoken = True

    remaining = buffer.strip()
    if remaining:
        if first_spoken:
            speak_append(remaining)
        else:
            speak(remaining)


class VoxApp(rumps.App):
    def __init__(self):
        super().__init__("Vox", title="Vox")
        self.recording = False
        self.audio_frames = []
        self.stream = None
        self.busy = False
        self.last_signal_state = False
        self.menu = [
            rumps.MenuItem("Talk", callback=self.toggle_recording),
            None,
            rumps.MenuItem("Status: Ready"),
            None,
            rumps.MenuItem("Stop Speech", callback=self.on_stop_speech),
        ]
        try:
            os.unlink("/tmp/vox-recording")
        except OSError:
            pass
        # Tight poll for fast hotkey response
        self._hotkey_timer = rumps.Timer(self._check_hotkey, 0.05)
        self._hotkey_timer.start()
        print("[vox] Started", flush=True)

    def _check_hotkey(self, _):
        signal_exists = os.path.exists("/tmp/vox-recording")
        if signal_exists != self.last_signal_state:
            self.last_signal_state = signal_exists
            self.toggle_recording(None)

    def set_status(self, text):
        print(f"[vox] {text}", flush=True)
        for item in self.menu.values():
            if isinstance(item, rumps.MenuItem) and item.title.startswith("Status:"):
                item.title = f"Status: {text}"
                break

    def toggle_recording(self, sender):
        if self.busy:
            return
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        self.recording = True
        self.audio_frames = []
        self.title = "🔴"
        self.set_status("Listening...")
        stop_speech()

        def audio_callback(indata, frames, time_info, status):
            if self.recording:
                self.audio_frames.append(indata.copy())

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1,
            blocksize=1024, callback=audio_callback
        )
        self.stream.start()

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self.title = "Vox"

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if not self.audio_frames:
            self.set_status("Ready")
            return

        audio = np.concatenate(self.audio_frames).flatten()
        self.audio_frames = []
        threading.Thread(target=self._process_audio, args=(audio,), daemon=True).start()

    def _process_audio(self, audio):
        self.busy = True
        self.set_status("Transcribing...")

        text = transcribe(audio)
        del audio

        if not text or len(text.strip()) < 2:
            self.set_status("Ready")
            self.busy = False
            return

        self.set_status(f"You: {text[:50]}")
        self.title = "💭"

        work_keywords = ["build", "create", "write", "fix", "edit", "code",
                         "implement", "refactor", "deploy", "install", "make"]
        is_work = any(kw in text.lower() for kw in work_keywords)
        model = "sonnet" if is_work else "haiku"

        screen_keywords = ["see my screen", "see what I see", "look at my screen",
                           "what's on my screen", "what do you see", "can you see",
                           "look at this", "what am I looking at", "screen"]
        wants_screen = any(kw in text.lower() for kw in screen_keywords)

        image_path = None
        if wants_screen:
            self.set_status("Capturing screen...")
            screenshot = "/tmp/vox-screen.png"
            try:
                os.unlink("/tmp/vox-screenshot-done")
            except OSError:
                pass
            open("/tmp/vox-screenshot-request", "w").close()
            import time
            for _ in range(30):
                if os.path.exists("/tmp/vox-screenshot-done"):
                    break
                time.sleep(0.1)
            try:
                os.unlink("/tmp/vox-screenshot-done")
            except OSError:
                pass
            if os.path.exists(screenshot):
                image_path = screenshot
                print(f"[vox] screenshot ready ({os.path.getsize(screenshot)} bytes)", flush=True)
            else:
                text += " (Note: screen capture failed — Vox may need Screen Recording permission.)"
                print("[vox] screenshot failed", flush=True)

        try:
            self.set_status("Thinking...")
            query_claude_streaming(text, model, image_path=image_path)
            try:
                os.unlink("/tmp/vox-screen.png")
            except OSError:
                pass
        except Exception as e:
            print(f"[vox] error: {e}", flush=True)
            speak("Sorry, something went wrong.")

        self.title = "🔊"
        self.set_status("Ready")
        self.title = "Vox"
        self.busy = False

    def on_stop_speech(self, sender):
        stop_speech()


if __name__ == "__main__":
    VoxApp().run()
