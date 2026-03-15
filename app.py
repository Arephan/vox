#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import rumps
import sounddevice as sd
import soundfile as sf
import base64
import shutil
import anthropic

KOKORO_SOCK = "/tmp/kokoro-tts.sock"
SAMPLE_RATE = 16000
TMUX_SESSION = "vox-claude"

SYSTEM = (
    "You are Vox, a voice assistant. Give concise, conversational answers. "
    "No markdown, no bullet points, no code blocks. Plain sentences only. "
    "Keep it short unless asked for detail."
)

def _make_client():
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
        print(f"[vox] OAuth error: {e}", flush=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    raise RuntimeError("No credentials")

_client = _make_client()

# Warm API connection in background
def _warmup_api():
    try:
        _client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1,
                                messages=[{"role": "user", "content": "hi"}])
        print("[vox] API warm", flush=True)
    except Exception:
        pass
threading.Thread(target=_warmup_api, daemon=True).start()

# Persistent conversation history
HISTORY_FILE = os.path.expanduser("~/.vox_history.json")
MAX_HISTORY = 20

def _load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def _save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history[-MAX_HISTORY:], f)

_conversation_history = _load_history()

# Find claude
def _find_claude():
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    nvm_dir = os.path.join(home, ".nvm/versions/node")
    if os.path.isdir(nvm_dir):
        for d in os.listdir(nvm_dir):
            c = os.path.join(nvm_dir, d, "bin/claude")
            if os.path.isfile(c):
                return c
    return "claude"

CLAUDE_BIN = _find_claude()
_claude_dir = os.path.dirname(CLAUDE_BIN)
if _claude_dir:
    os.environ["PATH"] = _claude_dir + ":" + os.environ.get("PATH", "")
print(f"[vox] Claude: {CLAUDE_BIN}", flush=True)

# Keep whisper loaded
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
    print(f"[vox] SPEAK: {text[:80]}", flush=True)
    _send_tts("speak", text)

def speak_append(text):
    print(f"[vox] SPEAK_APPEND: {text[:80]}", flush=True)
    _send_tts("speak_append", text)

def stop_speech():
    _send_tts("stop")

def notify(title, message):
    try:
        rumps.notification(title, "", message, sound=False)
    except Exception:
        pass


# --- Fast API streaming (for conversation) ---

def query_claude_api(text, model, image_path=None):
    """Stream via Anthropic API — fast, speaks immediately."""
    global _conversation_history
    model_ids = {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-6"}
    model_id = model_ids.get(model, "claude-haiku-4-5-20251001")

    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": text},
        ]
    else:
        user_content = text

    _conversation_history.append({"role": "user", "content": user_content})
    if len(_conversation_history) > MAX_HISTORY:
        _conversation_history = _conversation_history[-MAX_HISTORY:]

    buffer = ""
    full_response = ""
    first_spoken = False

    with _client.messages.stream(
        model=model_id, max_tokens=512, system=SYSTEM,
        messages=_conversation_history,
    ) as stream:
        for chunk in stream.text_stream:
            buffer += chunk
            full_response += chunk
            if not first_spoken:
                m = re.search(r'[.!?][)\'"]*\s', buffer)
                if m:
                    speak(buffer[:m.end()])
                    buffer = buffer[m.end():]
                    first_spoken = True
                elif len(buffer) > 150:
                    speak(buffer)
                    buffer = ""
                    first_spoken = True

    remaining = buffer.strip()
    if remaining:
        if first_spoken:
            speak_append(remaining)
        else:
            speak(remaining)

    if full_response:
        _conversation_history.append({"role": "assistant", "content": full_response})
        _save_history(_conversation_history)

    print(f"[vox] api response: {full_response[:80]}", flush=True)


# --- tmux Claude session (for tool work) ---

def ensure_tmux():
    """Start persistent Claude session in tmux if not running."""
    result = subprocess.run(["tmux", "has-session", "-t", TMUX_SESSION], capture_output=True)
    if result.returncode == 0:
        return

    print("[vox] Starting Claude tmux session...", flush=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-x", "200", "-y", "50"])
    time.sleep(1)

    cmd = f"VOX_NO_HOOK=1 {CLAUDE_BIN} --model haiku --dangerously-skip-permissions --system-prompt 'You are Vox. Be concise. No markdown, no bullet points. Plain sentences only.'"
    subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, cmd, "Enter"])
    time.sleep(3)

    # Accept trust prompt
    pane = subprocess.run(["tmux", "capture-pane", "-t", TMUX_SESSION, "-p"],
                          capture_output=True, text=True).stdout
    if "trust" in pane.lower():
        subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, "Enter"])
        time.sleep(8)

    print("[vox] Claude session ready", flush=True)


def _count_response_blocks():
    """Count ⏺ blocks in current pane."""
    pane = subprocess.run(["tmux", "capture-pane", "-t", TMUX_SESSION, "-p", "-S", "-100"],
                          capture_output=True, text=True).stdout
    return pane.count('⏺'), pane


SKIP_PREFIXES = ['Read ', 'Reading ', 'Wrote ', 'Ran ', 'Search', 'plugin:', 'Edit', 'Bash',
                  'Running', 'running', 'Stop:', 'hook', 'Hook', 'Glob', 'Grep', 'Write',
                  'Agent', 'Task', 'LSP', 'Bash(']
SKIP_CONTENT = ['running stop', 'hook', 'bypass permissions', 'mcp server', 'shift+tab',
                'boogieing', 'thinking', 'moonwalking', 'grooving', 'vibing', 'shimmy',
                'ctrl+o', 'expand', '/tmp/', '✻', '─────', 'file changed', 'files changed',
                'sublimating', 'breakdancing']


def _get_new_response(pane_content, skip_blocks=0):
    """Get text from new ⏺ blocks, skipping the first skip_blocks."""
    lines = pane_content.strip().split('\n')
    lines = [re.sub(r'\x1b\[[0-9;]*m', '', l) for l in lines]

    block_index = 0
    blocks = []
    current = []
    in_block = False

    for line in lines:
        s = line.strip()
        if s.startswith('⏺'):
            if current:
                blocks.append((block_index, ' '.join(current)))
            current = []
            block_index += 1
            in_block = True
            text = s[1:].strip()
            if any(text.startswith(k) for k in SKIP_PREFIXES):
                in_block = False
                continue
            if text:
                current.append(text)
            continue
        if in_block:
            if s.startswith('❯') or s.startswith('⏵⏵'):
                in_block = False
                continue
            if s.startswith('⎿'):
                text = s[1:].strip()
                if text and not text.startswith('[Image') and not text.startswith('['):
                    current.append(text)
            elif s and not any(skip in s.lower() for skip in SKIP_CONTENT):
                current.append(s)

    if current:
        blocks.append((block_index, ' '.join(current)))

    # Return last text block that is NEW (index > skip_blocks)
    for idx, text in reversed(blocks):
        if idx > skip_blocks and text.strip() and len(text.strip()) > 1:
            return text.strip()
    return ""


def _is_prompt_ready(pane_content):
    """Check if Claude is done (empty ❯ at the bottom)."""
    lines = pane_content.strip().split('\n')
    clean = [re.sub(r'\x1b\[[0-9;]*m', '', l).strip() for l in lines[-4:]]
    return any(l == '❯' for l in clean)


def query_claude_tmux(text, image_path=None):
    """Send message to persistent tmux Claude session, speak response."""
    ensure_tmux()

    if image_path and os.path.exists(image_path):
        text = f"Look at this screenshot file and describe what you see: {image_path} — {text}"
    text = text.replace('\n', ' ').strip()

    # Count blocks before sending
    before_count, _ = _count_response_blocks()

    # Send message
    subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, "-l", text])
    subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, "Enter"])

    first_spoken = False
    spoken_text = ""
    start = time.time()
    last_response = ""

    time.sleep(0.2)

    while time.time() - start < 60:
        current_count, pane = _count_response_blocks()

        # Only look at response if new blocks appeared
        if current_count > before_count:
            response = _get_new_response(pane, skip_blocks=before_count)

            if response and response != last_response:
                last_response = response

                # Speak as soon as we have ANY text — don't wait for full sentence
                if not first_spoken and len(response) > 10:
                    m = re.search(r'[.!?,;:]\s', response)
                    if m:
                        speak(response[:m.end()])
                        spoken_text = response[:m.end()]
                        first_spoken = True
                    elif len(response) > 80:
                        # No punctuation yet, speak what we have
                        speak(response)
                        spoken_text = response
                        first_spoken = True

            # Check if Claude is done
            if _is_prompt_ready(pane) and response:
                if not first_spoken:
                    speak(response)
                else:
                    remaining = response[len(spoken_text):].strip()
                    if remaining:
                        speak_append(remaining)
                # Show response as notification
                notify("Vox", response[:150])
                print(f"[vox] done: {response[:80]}", flush=True)
                return response

        time.sleep(0.1)

    if not first_spoken:
        speak("Sorry, that took too long.")
    return last_response or "timeout"


class VoxApp(rumps.App):
    def __init__(self):
        super().__init__("Vox", title="Vox")
        self.recording = False
        self.audio_frames = []
        self.stream = None
        self.busy = False
        self.last_signal_state = False
        self.current_model = "auto"

        self.menu = [
            rumps.MenuItem("Talk", callback=self.toggle_recording),
            None,
            rumps.MenuItem("Status: Ready"),
            None,
            rumps.MenuItem("Model: Auto", callback=None),
            rumps.MenuItem("  ✓ Auto (Haiku/Sonnet)", callback=self.set_model_auto),
            rumps.MenuItem("    Haiku (fast)", callback=self.set_model_haiku),
            rumps.MenuItem("    Sonnet (balanced)", callback=self.set_model_sonnet),
            rumps.MenuItem("    Opus (powerful)", callback=self.set_model_opus),
            None,
            rumps.MenuItem("Stop Speech", callback=self.on_stop_speech),
        ]
        try:
            os.unlink("/tmp/vox-recording")
        except OSError:
            pass
        self._hotkey_timer = rumps.Timer(self._check_hotkey, 0.05)
        self._hotkey_timer.start()

        # Pre-warm tmux session in background
        threading.Thread(target=ensure_tmux, daemon=True).start()

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

    def _update_model_menu(self):
        labels = {"auto": "Auto (Haiku/Sonnet)", "haiku": "Haiku (fast)",
                  "sonnet": "Sonnet (balanced)", "opus": "Opus (powerful)"}
        for key, label in labels.items():
            menu_key = f"  ✓ {label}" if self.current_model == key else f"    {label}"
            old_checked = f"  ✓ {label}"
            old_unchecked = f"    {label}"
            for item in self.menu.values():
                if isinstance(item, rumps.MenuItem) and item.title in (old_checked, old_unchecked):
                    item.title = menu_key
                    break
        for item in self.menu.values():
            if isinstance(item, rumps.MenuItem) and item.title.startswith("Model:"):
                item.title = f"Model: {labels[self.current_model].split(' (')[0]}"
                break

    def set_model_auto(self, _): self.current_model = "auto"; self._update_model_menu()
    def set_model_haiku(self, _): self.current_model = "haiku"; self._update_model_menu()
    def set_model_sonnet(self, _): self.current_model = "sonnet"; self._update_model_menu()
    def set_model_opus(self, _): self.current_model = "opus"; self._update_model_menu()

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
        self.title = "🎙"

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
        notify("Vox", f"🎤 \"{text[:60]}...\"" if len(text) > 60 else f"🎤 \"{text}\"")

        # Determine model
        work_keywords = ["build", "create", "write", "fix", "edit", "code",
                         "implement", "refactor", "deploy", "install", "make",
                         "run", "execute", "delete", "update", "commit", "push"]
        if self.current_model == "auto":
            is_work = any(kw in text.lower() for kw in work_keywords)
            model = "sonnet" if is_work else "haiku"
        else:
            model = self.current_model
            is_work = any(kw in text.lower() for kw in work_keywords)

        # Screenshot
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
                text += " (screen capture failed)"
                print("[vox] screenshot failed", flush=True)

        try:
            self.set_status("Thinking...")
            query_claude_tmux(text, image_path=image_path)
            try:
                os.unlink("/tmp/vox-screen.png")
            except OSError:
                pass
        except Exception as e:
            print(f"[vox] error: {e}", flush=True)
            speak("Sorry, something went wrong.")

        self.title = "🔊"
        self.set_status("Ready")
        self.title = "🎙"
        self.busy = False

    def on_stop_speech(self, sender):
        stop_speech()


if __name__ == "__main__":
    VoxApp().run()
