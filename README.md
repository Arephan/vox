# Vox

Talk to Claude Code with your voice. Fully local, free, offline TTS and STT.

Press **Option+Shift+A** from anywhere on your Mac → speak → Claude responds with voice.

## What it does

- **Menu bar app** — lives in your toolbar, out of the way
- **Push-to-talk hotkey** (Option+Shift+A) — works system-wide, no Accessibility permission needed
- **Offline speech-to-text** — Whisper (base model), runs locally on Apple Silicon
- **Offline text-to-speech** — Kokoro TTS, natural voice, streams audio with no gaps
- **Routes to Claude Code** — Haiku for conversation, Sonnet for work tasks
- **Background tasks** — say "work on this in the background" and get a notification when done

## Install

1. Download `Vox.dmg` from [Releases](https://github.com/Arephan/vox/releases)
2. Drag **Vox** to Applications
3. Open Vox — it will prompt to install dependencies on first launch

If macOS blocks the app, run this in Terminal:
```bash
xattr -cr /Applications/Vox.app
```

### Requirements

- macOS 13+ on Apple Silicon (M1/M2/M3/M4)
- Python 3.10 (`brew install python@3.10`)
- [Claude Code](https://github.com/anthropics/claude-code) installed

---

## For AI Agents

> If you're an AI agent setting this up for a user, follow these steps exactly. Do not skip any.

### Step 1 — Prerequisites

```bash
# Check macOS and architecture
uname -m   # Must be arm64 (Apple Silicon)
sw_vers -productVersion   # Must be 13.0+

# Install Python 3.10 (required — Kokoro/Whisper don't support 3.11+)
brew install python@3.10
python3.10 --version   # Verify: Python 3.10.x

# Install tmux
brew install tmux

# Verify Claude Code is installed and logged in
which claude   # Must return a path
claude --version   # Should work without errors
```

### Step 2 — Install Vox

**Option A: From DMG (recommended)**
```bash
# Download latest release
curl -L -o /tmp/Vox.dmg https://github.com/Arephan/vox/releases/latest/download/Vox.dmg
hdiutil attach /tmp/Vox.dmg
cp -R /Volumes/Vox/Vox.app /Applications/
hdiutil detach /Volumes/Vox
xattr -cr /Applications/Vox.app
```

**Option B: From source**
```bash
git clone https://github.com/Arephan/vox.git ~/vox
cd ~/vox
swiftc launcher.swift -o Vox -framework Cocoa -framework Carbon -framework CoreGraphics
mkdir -p /Applications/Vox.app/Contents/{MacOS,Resources}
cp Vox /Applications/Vox.app/Contents/MacOS/
cp app.py install.sh kokoro-server.py claude-speak.py /Applications/Vox.app/Contents/Resources/
# Create Info.plist with LSUIElement=true, NSMicrophoneUsageDescription, NSScreenCaptureUsageDescription
codesign --force --deep --sign - /Applications/Vox.app
xattr -cr /Applications/Vox.app
```

### Step 3 — Run first-time setup

```bash
# Open Vox — it will show a setup wizard and install dependencies
open /Applications/Vox.app
```

If the setup wizard doesn't trigger (kokoro-env already exists), run manually:
```bash
bash /Applications/Vox.app/Contents/Resources/install.sh
```

This installs:
- Python virtualenv at `~/kokoro-env` with kokoro, faster-whisper, rumps, sounddevice
- `~/bin/kokoro-server.py` and `~/bin/claude-speak.py`
- launchd service for kokoro-server (auto-starts on login)
- `shh` alias in ~/.zshrc to stop speech

### Step 4 — Verify everything works

```bash
# Check kokoro-server is running
ls /tmp/kokoro-tts.sock   # Socket file must exist
tail -3 /tmp/kokoro-server.log   # Should say "Model loaded and warm"

# Test TTS
python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('/tmp/kokoro-tts.sock')
s.sendall(json.dumps({'cmd': 'speak', 'text': 'Vox is working.'}).encode())
s.close()
"

# Check Vox processes
ps aux | grep -v grep | grep Vox   # Should show Swift launcher
ps aux | grep -v grep | grep app.py   # Should show Python menu bar
```

### Step 5 — Grant permissions

macOS will prompt for these on first use:
- **Microphone** — prompted when user first records. Shows as "Vox".
- **Screen Recording** — prompted when user says "can you see my screen". Shows as "Vox". If it only captures wallpaper, remove and re-add Vox in System Settings → Privacy → Screen Recording.

After granting Screen Recording, restart Vox:
```bash
pkill -f Vox; open /Applications/Vox.app
```

### Gotchas

- **Gatekeeper blocks unsigned apps**: Always run `xattr -cr /Applications/Vox.app` after installing
- **Python 3.10 only**: Kokoro and faster-whisper don't work on 3.11+. `brew install python@3.10`
- **Screen Recording shows wallpaper only**: Permission was granted to an old binary. Remove Vox from Screen Recording, re-add, restart Vox
- **"No such file or directory" for node**: Claude Code needs node in PATH. The app auto-discovers nvm paths, but if claude is installed elsewhere, check `which claude` and ensure its directory is in PATH
- **Double speech**: If responses are spoken twice, check that `VOX_NO_HOOK=1` is set in the tmux session. The Claude Code Stop hook (`~/bin/claude-speak.py`) checks this env var and skips if set
- **kokoro-server not starting**: Run `tail -20 /tmp/kokoro-server.log` to see errors. Common fix: `launchctl unload ~/Library/LaunchAgents/com.kokoro-server.plist && launchctl load ~/Library/LaunchAgents/com.kokoro-server.plist`
- **Whisper model download**: First transcription downloads the Whisper base model (~140MB). Needs internet once, then works offline
- **tmux session trust prompt**: On first launch, Claude Code asks to trust the workspace. Vox auto-accepts this, but if it gets stuck, run `tmux attach -t vox-claude` and accept manually

---

## How it works

```
Option+Shift+A  →  start recording (menu bar turns 🔴)
Option+Shift+A  →  stop recording
                    ↓
              Whisper transcribes your speech (~1 second)
                    ↓
              Claude Code responds (Haiku for chat, Sonnet for work)
                    ↓
              Kokoro speaks the response aloud
```

The app bundles:
- A **native Swift launcher** with Carbon hotkey registration (no Accessibility permission needed)
- A **Python menu bar app** (rumps) for the UI and orchestration
- **Kokoro TTS server** — keeps the voice model warm in memory for instant playback
- **Whisper STT** — offline speech recognition

## Voice & Speed

Default voice is `af_heart` at 1.15x speed. Edit `~/bin/kokoro-server.py` to change, then restart the server.

Available voices: `af_heart`, `af_bella`, `af_jessica`, `af_nova`, `af_sky`, `am_adam`, `am_echo`, `am_eric`, and many more.

## Stop speech

Type `shh` in any terminal, or click "Stop Speech" in the Vox menu.

## Architecture

Two always-on processes:
1. **Vox.app** — Swift launcher (hotkey) + Python menu bar (UI, STT, Claude)
2. **kokoro-server** — TTS daemon, keeps model in memory, auto-starts on login via launchd

Communication via Unix socket at `/tmp/kokoro-tts.sock`. Hotkey triggers via temp file signal at `/tmp/vox-recording`.

## Building from source

```bash
# Build the Swift launcher
swiftc launcher.swift -o vox -framework Cocoa -framework Carbon

# Run the Python app directly
python3.10 app.py
```
