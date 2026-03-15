#!/usr/bin/env python3
import shutil
import sys
import os
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rumps
import sounddevice as sd
import soundfile as sf
import threading
import json
import socket
import subprocess
import tempfile

KOKORO_SOCK = "/tmp/kokoro-tts.sock"
SAMPLE_RATE = 16000

# Find claude binary dynamically
def find_claude():
    # Check PATH first
    found = shutil.which("claude")
    if found:
        return found
    # Common locations
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".nvm/versions/node", d, "bin/claude")
        for d in os.listdir(os.path.join(home, ".nvm/versions/node")) if os.path.isdir(os.path.join(home, ".nvm/versions/node", d))
    ] if os.path.isdir(os.path.join(home, ".nvm/versions/node")) else []
    candidates += [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "claude"  # fallback, hope it's in PATH

CLAUDE_BIN = find_claude()
print(f"[vox] Claude: {CLAUDE_BIN}", flush=True)

# Keep whisper loaded for speed
print("[vox] Loading whisper...", flush=True)
from faster_whisper import WhisperModel
whisper_model = WhisperModel("base", device="auto")
print("[vox] Whisper ready", flush=True)


def transcribe(audio_data):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio_data, SAMPLE_RATE)
        tmppath = f.name
    try:
        segments, _ = whisper_model.transcribe(tmppath, language="en")
        return " ".join(seg.text for seg in segments).strip()
    finally:
        os.unlink(tmppath)


def speak(text):
    stop_speech()  # Always stop previous speech first
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(KOKORO_SOCK)
        sock.sendall(json.dumps({"cmd": "speak", "text": text}).encode())
        sock.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        pass


def stop_speech():
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(KOKORO_SOCK)
        sock.sendall(json.dumps({"cmd": "stop"}).encode())
        sock.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        pass


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
        # Clean up stale signal
        try:
            os.unlink("/tmp/vox-recording")
        except OSError:
            pass
        # Poll for hotkey signal
        self._hotkey_timer = rumps.Timer(self._check_hotkey, 0.3)
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

        # Check if user wants screen context
        screen_keywords = ["see my screen", "see what I see", "look at my screen",
                           "what's on my screen", "what do you see", "can you see",
                           "look at this", "what am I looking at", "screen"]
        wants_screen = any(kw in text.lower() for kw in screen_keywords)

        try:
            env = os.environ.copy()
            env["VOX_NO_HOOK"] = "1"
            prompt = text

            if wants_screen:
                self.set_status("Capturing screen...")
                screenshot = "/tmp/vox-screen.png"
                # Signal the Swift launcher to take the screenshot (shows as "Vox" in permissions)
                try:
                    os.unlink("/tmp/vox-screenshot-done")
                except OSError:
                    pass
                open("/tmp/vox-screenshot-request", "w").close()
                # Wait for Swift to take it (up to 3 seconds)
                for _ in range(30):
                    if os.path.exists("/tmp/vox-screenshot-done"):
                        break
                    import time
                    time.sleep(0.1)
                try:
                    os.unlink("/tmp/vox-screenshot-done")
                except OSError:
                    pass
                print(f"[vox] screenshot exists={os.path.exists(screenshot)}", flush=True)
                if os.path.exists(screenshot):
                    size = os.path.getsize(screenshot)
                    print(f"[vox] screenshot size={size} bytes", flush=True)
                    prompt = f"Read this image file and describe what you see, then answer the user's question.\n\nImage: {screenshot}\n\nUser: {text}"
                else:
                    print(f"[vox] screenshot failed", flush=True)
                    prompt = text + " (Note: I tried to capture the screen but it failed — Vox may need Screen Recording permission in System Settings.)"

            print(f"[vox] sending to claude (model={model})...", flush=True)
            result = subprocess.run(
                [CLAUDE_BIN, "-p", "--model", model, prompt],
                capture_output=True, text=True, timeout=300,
                cwd=os.path.expanduser("~"),
                env=env
            )
            response = result.stdout.strip()
            print(f"[vox] claude exit={result.returncode}, response={response[:100]}", flush=True)
            if result.returncode != 0:
                print(f"[vox] claude stderr: {result.stderr[:200]}", flush=True)
                response = f"Error: {result.stderr.strip()}"

            # Clean up screenshot
            try:
                os.unlink("/tmp/vox-screen.png")
            except OSError:
                pass
        except subprocess.TimeoutExpired:
            response = "That took too long."
        except Exception as e:
            print(f"[vox] exception: {e}", flush=True)
            response = f"Error: {e}"

        self.title = "🔊"
        speak(response)
        self.set_status("Ready")
        self.title = "Vox"
        self.busy = False

    def on_stop_speech(self, sender):
        stop_speech()


if __name__ == "__main__":
    VoxApp().run()
