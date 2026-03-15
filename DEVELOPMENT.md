# Vox Development Notes

## Architecture

Vox runs as **two processes** launched from one `Vox.app`:

1. **Swift binary** (`launcher.swift` → `Vox.app/Contents/MacOS/Vox`)
   - Registers global hotkey via Carbon `RegisterEventHotKey` (no Accessibility permission needed)
   - Polls `/tmp/vox-screenshot-request` and takes screenshots via `CGDisplayCreateImage`
   - Launches the Python menu bar app as a child process
   - Monitors Python — if it dies, Swift exits too

2. **Python app** (`app.py` → runs inside `Vox.app/Contents/Resources/app.py`)
   - Menu bar UI via `rumps`
   - STT via `faster-whisper` (base model, kept loaded for speed)
   - Routes to Claude Code via `claude -p` subprocess
   - TTS via kokoro-server Unix socket
   - Polls `/tmp/vox-recording` signal file for hotkey toggle

## Communication between processes

- **Hotkey toggle**: Swift creates/removes `/tmp/vox-recording`, Python polls it every 0.3s via `rumps.Timer`
- **Screenshot request**: Python creates `/tmp/vox-screenshot-request`, Swift takes screenshot to `/tmp/vox-screen.png`, creates `/tmp/vox-screenshot-done` when ready
- **TTS**: Python sends JSON to kokoro-server via Unix socket at `/tmp/kokoro-tts.sock`

## Critical gotchas

### macOS permissions
- **Screen Recording**: Tied to the app's code signature. Every time you rebuild and re-sign `Vox.app`, the old permission is invalidated. User must re-grant it. Use `tccutil reset ScreenCapture com.arephan.vox` to force a fresh prompt on next launch.
- **Microphone**: Granted per-app via the `NSMicrophoneUsageDescription` plist key. Shows as "Vox" automatically.
- **Accessibility**: NOT needed. We use Carbon `RegisterEventHotKey` which bypasses Accessibility entirely. Do NOT use `pynput` for hotkeys — it requires Accessibility and shows as "Python" not "Vox".
- **Gatekeeper**: Downloaded apps get quarantined. Users need `xattr -cr /Applications/Vox.app` to open unsigned builds.

### PATH issues
- When launched via `open Vox.app`, the PATH is minimal — no nvm, no homebrew.
- `claude` is a Node.js script installed via nvm, so both `claude` AND `node` must be in PATH.
- Fixed by: (1) `find_claude()` in Python searches nvm directories, (2) adds the claude binary's directory to PATH so `node` is found too.
- The Swift launcher also injects nvm paths into the environment before launching Python.

### PyInstaller issues (abandoned)
- PyInstaller builds caused memory leaks — the bundled Python runtime + faster-whisper model consumed 1GB+.
- PyInstaller bundled `pynput` had callback signature mismatches (`GlobalHotKeys._on_press() missing 1 required positional argument: 'injected'`).
- `rumps` menu callbacks didn't fire in PyInstaller builds.
- **Decision**: Use native Swift wrapper + Python script instead. Much lighter, more reliable.

### Process lifecycle
- The Swift launcher must NOT call `python.waitUntilExit()` on the main thread — it blocks the Carbon event loop and kills hotkey/screenshot functionality.
- Instead, monitor Python exit on a background `DispatchQueue` and terminate the Swift app when Python dies.
- The `/tmp/vox-debug.log` file handle must exist before Swift launches Python (Swift opens it for stdout/stderr redirect).

### Kokoro TTS
- Default voice: `af_heart`, speed: `1.15`
- Server runs as launchd daemon at `~/Library/LaunchAgents/com.kokoro-server.plist`
- Model takes ~8 seconds to load on startup
- Audio uses callback-based `sd.OutputStream` with a deque sample buffer to prevent crackling
- First sentence synthesized alone for low latency, remaining batched in groups of 3

### Whisper STT
- Using `base` model (not `small`) for speed — transcription in ~1-2 seconds
- Model kept loaded in memory (~140MB) for fast response
- If memory is a concern, can load/unload per transcription (adds ~4 seconds latency)
- Requires Python 3.10 — faster-whisper/ctranslate2 don't support 3.11+

### Claude API integration
- **Current approach (fast)**: Uses Anthropic Python SDK directly with Claude Code's OAuth token
- OAuth token extracted from macOS Keychain: `security find-generic-password -s "Claude Code-credentials" -w`
- Requires header `anthropic-beta: oauth-2025-04-20` for OAuth auth to work
- Streams responses via `_client.messages.stream()` — speaks first sentence as soon as it arrives
- Uses `speak()` for first sentence, `speak_append()` for remaining text
- Haiku for conversation (fast), Sonnet for work tasks (detects keywords like "build", "create", "fix", "code")
- **Important**: No conversation persistence yet — each call is stateless. Need to implement message history.

#### Previous approaches (abandoned)
- `claude -p` subprocess: Too slow (2-5 seconds before any output), no streaming
- `claude -p --continue`: Loaded heavy session context, often timed out
- `claude -p --output-format stream-json --verbose`: Worked but still slower than direct API
- OAuth without `anthropic-beta` header: Returns 401 authentication error

### Screenshot for screen sharing
- Only triggered when user says keywords: "see my screen", "look at this", "what do you see", etc.
- Screenshot taken by Swift helper via `CGDisplayCreateImage` (shows as "Vox" in permissions)
- Sent to Claude as base64 PNG in the API call
- Cleaned up after each use

## File structure

```
vox/
├── app.py              # Python menu bar app (STT, Claude, TTS orchestration)
├── launcher.swift      # Swift binary (hotkey, screenshots, process launcher)
├── install.sh          # First-launch installer (kokoro-env, dependencies, launchd)
├── vox-toggle.sh       # Hotkey signal script
├── setup.py            # py2app setup (unused, kept for reference)
├── README.md           # User-facing docs
└── DEVELOPMENT.md      # This file
```

## Dependencies (installed to ~/kokoro-env)

- kokoro (TTS)
- faster-whisper (STT)
- sounddevice, soundfile, numpy (audio I/O)
- rumps (menu bar UI)
- ctranslate2 (whisper backend)

## Building

```bash
# Compile Swift launcher
swiftc launcher.swift -o Vox -framework Cocoa -framework Carbon -framework CoreGraphics

# Build Vox.app bundle
mkdir -p Vox.app/Contents/{MacOS,Resources}
cp Vox Vox.app/Contents/MacOS/
cp app.py install.sh Vox.app/Contents/Resources/
# Add Info.plist with LSUIElement, NSMicrophoneUsageDescription, NSScreenCaptureUsageDescription
codesign --force --deep --sign - Vox.app

# Create DMG
mkdir /tmp/vox-dmg
cp -R Vox.app /tmp/vox-dmg/
ln -s /Applications /tmp/vox-dmg/Applications
hdiutil create -volname "Vox" -srcfolder /tmp/vox-dmg -format UDZO Vox.dmg
```

## Related projects

- **claude-voice** (https://github.com/Arephan/claude-voice) — Claude Code plugin for TTS-only (no STT, no menu bar). Simpler, just speaks responses aloud.
- **Telegram bot** (`~/bin/telegram-claude-bot.py`) — Telegram interface to Claude with voice responses. Bot token in the script.
- **Shared todo app** (`~/projects/shared-todo/server.py`) — localhost:3456, shared between user and Claude.
