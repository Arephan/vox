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

### Requirements

- macOS 13+ on Apple Silicon (M1/M2/M3/M4)
- Python 3.10 (`brew install python@3.10`)
- [Claude Code](https://github.com/anthropics/claude-code) installed

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
