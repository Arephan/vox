# Vox Development Notes

## Quick Setup for AI Agents

> If you're an AI on another computer setting this up, read the README.md "For AI Agents" section first. This file has deeper context.

## Architecture

Vox runs as **three processes**:

1. **Swift binary** (`Vox.app/Contents/MacOS/Vox`)
   - Registers global hotkeys via Carbon (no Accessibility permission needed)
   - Option+Shift+A = toggle recording, Option+Shift+S = stop speech
   - Takes screenshots via `CGDisplayCreateImage` (shows as "Vox" in Screen Recording permissions)
   - Launches the Python menu bar app as a child process
   - Starts kokoro-server if not running
   - Runs first-time installer if dependencies missing

2. **Python menu bar app** (`app.py`)
   - Menu bar UI via `rumps` (shows 🎙 icon)
   - STT via `faster-whisper` (base model, kept loaded in memory)
   - Communicates with Claude via persistent tmux session
   - TTS via kokoro-server Unix socket
   - Polls `/tmp/vox-recording` at 50ms for hotkey toggle
   - Model selection menu (Auto/Haiku/Sonnet/Opus) sends `/model` to tmux

3. **Kokoro TTS server** (`~/bin/kokoro-server.py`)
   - launchd daemon, auto-starts on login
   - Keeps Kokoro model warm in memory
   - Accepts `speak`, `speak_append`, `stop` commands via Unix socket `/tmp/kokoro-tts.sock`
   - Callback-based audio stream with deque buffer (no crackling)
   - Voice: `af_heart`, speed: `1.15`

## Communication

- **Hotkey** → Swift creates/removes `/tmp/vox-recording` → Python polls at 50ms
- **Screenshot** → Python creates `/tmp/vox-screenshot-request` → Swift captures → creates `/tmp/vox-screenshot-done`
- **Screenshot fallback** → If Swift capture fails, Python falls back to `screencapture -x` command
- **TTS** → Python sends JSON to kokoro-server via Unix socket `/tmp/kokoro-tts.sock`
- **Claude** → Python sends text via `tmux send-keys` to persistent `vox-claude` tmux session, polls `tmux capture-pane` at 100ms for responses

## Claude tmux session

- Session name: `vox-claude`
- Launched with `--dangerously-skip-permissions` (no permission prompts)
- System prompt tells Claude it has full machine access
- `VOX_NO_HOOK=1` prevents the Claude Code Stop hook from double-speaking
- Auto-accepts trust prompts and bypass-permissions prompts
- `_is_claude_alive()` checks if Claude is responsive (not just if tmux session exists)
- If Claude dies but tmux stays, it kills the session and recreates

## Response parsing from tmux

This is the trickiest part. Claude Code's terminal output has:
- `⏺` marks the start of Claude's response blocks
- `❯` is the input prompt (empty `❯` on its own line = Claude is done)
- `⏵⏵` is the status bar at the bottom
- Tool use blocks start with `⏺ Bash(...)`, `⏺ Read ...`, `⏺ Edit ...`, etc.
- Status text appears briefly: "Boogieing", "Moonwalking", "Shimmy", "Sublimating", etc.
- Hook status: "Running stop hook", "bypass permissions on"

**What we filter out** (SKIP_PREFIXES and SKIP_CONTENT in app.py):
- Tool use: Read, Reading, Wrote, Ran, Search, plugin:, Edit, Bash, Bash(, Glob, Grep, Write, Agent, Task, LSP
- Status: running stop, hook, bypass permissions, mcp server, shift+tab, boogieing, thinking, moonwalking, grooving, vibing, shimmy, sublimating, breakdancing, ctrl+o, expand, /tmp/, ✻, ─────, file changed

**Response tracking**: Count `⏺` blocks before sending message. Only extract text from blocks with index > before_count. This prevents speaking old responses.

**Sentence streaming**: Speak complete sentences (ending in `.!?`) as they appear. Use `speak()` for first sentence, `speak_append()` for rest. If Claude is done and text remains without punctuation, speak it anyway.

## Critical Gotchas

### macOS Permissions
- **Screen Recording** is tied to the app's **code signature**. Every `codesign --force` invalidates it. User must re-grant. Use `tccutil reset ScreenCapture com.arephan.vox` to force a fresh prompt
- **Microphone** survives re-signing
- **Accessibility** is NOT needed — Carbon `RegisterEventHotKey` bypasses it
- **Gatekeeper**: `xattr -cr /Applications/Vox.app` required after every install from DMG

### PATH Issues
- When launched via `open Vox.app`, PATH is minimal — no nvm, no homebrew
- Claude Code needs both `claude` AND `node` in PATH
- Swift launcher injects nvm paths into environment
- Python app uses `_find_claude()` which searches nvm dirs, then adds claude's dir to PATH

### Install Script
- Line endings matter — `\r` characters from Write tool cause `set -e` to fail
- Always `sed -i '' 's/\r$//'` on bundled scripts
- Script uses `$REPO_DIR` which resolves to Vox.app/Contents/Resources/ when run from bundle
- Installs: kokoro-server.py, claude-speak.py, kokoro-stop.sh to ~/bin
- Creates launchd plist at ~/Library/LaunchAgents/com.kokoro-server.plist
- Adds `shh` alias to ~/.zshrc

### Kokoro TTS
- Model: Kokoro-82M, loads in ~8 seconds
- Requires Python 3.10 (not 3.11+)
- `PYTORCH_ENABLE_MPS_FALLBACK=1` required
- `speak_append` waits for current speech to finish, then plays (no interruption)
- `speak` stops current speech and plays immediately

### Whisper STT
- Using `base` model (~140MB) for speed
- First use downloads model from HuggingFace (needs internet once)
- Kept loaded in memory (~140MB RAM)
- `ctranslate2` float16 warning is harmless — auto-converts to float32

### OAuth Token
- Claude Code stores OAuth token in macOS Keychain: `security find-generic-password -s "Claude Code-credentials" -w`
- Requires `anthropic-beta: oauth-2025-04-20` header
- Used for API streaming (fast path) and warmup ping
- Falls back to `ANTHROPIC_API_KEY` env var if no OAuth

### Double Speech Prevention
- Claude Code has a Stop hook (`~/bin/claude-speak.py`) that speaks every response
- The tmux session sets `VOX_NO_HOOK=1` so the hook skips
- But: the hook checks `os.environ.get("VOX_NO_HOOK")` — only works if Claude Code passes parent env to hooks

### PyInstaller (abandoned)
- Caused memory leaks (1GB+ for bundled Python + models)
- `pynput` callback signature mismatches in bundle
- `rumps` menu callbacks didn't fire
- **Decision**: Use native Swift wrapper + Python script instead

## File Structure

```
vox/
├── app.py              # Python menu bar app
├── launcher.swift      # Swift binary (hotkeys, screenshots, process launcher)
├── install.sh          # First-launch installer
├── kokoro-server.py    # TTS server (copied to ~/bin on install)
├── claude-speak.py     # Claude Code Stop hook (copied to ~/bin on install)
├── kokoro-stop.sh      # Stop speech script
├── Vox.icns            # App icon
├── Vox.dmg             # Built DMG (not in git)
├── README.md           # User + AI agent docs
└── DEVELOPMENT.md      # This file
```

## Building

```bash
# Compile Swift launcher
swiftc launcher.swift -o Vox -framework Cocoa -framework Carbon -framework CoreGraphics

# Build Vox.app bundle
APP="dist/Vox.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp Vox "$APP/Contents/MacOS/"
cp app.py install.sh kokoro-server.py claude-speak.py kokoro-stop.sh Vox.icns "$APP/Contents/Resources/"
# Add Info.plist (see existing for template)
codesign --force --deep --sign - "$APP"

# Create DMG
mkdir /tmp/vox-dmg && cp -R "$APP" /tmp/vox-dmg/ && ln -s /Applications /tmp/vox-dmg/Applications
hdiutil create -volname "Vox" -srcfolder /tmp/vox-dmg -format UDZO Vox.dmg

# IMPORTANT: After re-signing, Screen Recording permission is invalidated
# Run: tccutil reset ScreenCapture com.arephan.vox
```

## Related Projects

- **claude-voice** (github.com/Arephan/claude-voice) — Claude Code plugin for TTS-only
- **Telegram bot** (`~/bin/telegram-claude-bot.py`) — Telegram voice interface to Claude
- **Shared todo** (`~/projects/shared-todo/server.py`) — localhost:3456
