#!/bin/bash
set -e

# Vox installer — sets up Kokoro TTS, Whisper STT, and the menu bar app

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/bin"
VENV_DIR="$HOME/kokoro-env"
PLIST="$HOME/Library/LaunchAgents/com.kokoro-server.plist"

echo ""
echo "🎙  Vox Installer"
echo "─────────────────"
echo ""

# 1. Check for Python 3.10
echo "→ Checking for Python 3.10..."
PYTHON=""
for candidate in python3.10 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | grep -o '3\.10')
        if [ "$version" = "3.10" ]; then
            PYTHON=$(command -v "$candidate")
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "❌  Python 3.10 is required but not found."
    echo "    Install it with: brew install python@3.10"
    exit 1
fi
echo "   Found: $PYTHON"

# 1b. Check for tmux
echo "→ Checking for tmux..."
if ! command -v tmux &>/dev/null; then
    echo ""
    echo "❌  tmux is required but not found."
    echo "    Install it with: brew install tmux"
    exit 1
fi
echo "   Found: $(command -v tmux)"

# 2. Create virtualenv
echo "→ Creating Python virtualenv at $VENV_DIR..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

# 3. Install dependencies
echo "→ Installing kokoro, whisper, sounddevice, rumps..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet kokoro sounddevice numpy soundfile faster-whisper rumps anthropic

# 4. Copy server scripts to ~/bin
echo "→ Installing scripts to $BIN_DIR..."
mkdir -p "$BIN_DIR"

VENV_PYTHON="$VENV_DIR/bin/python3"

for script in kokoro-server.py claude-speak.py kokoro-stop.sh; do
    if [ -f "$REPO_DIR/$script" ]; then
        dest="$BIN_DIR/$script"
        cp "$REPO_DIR/$script" "$dest"
        sed -i.bak "1s|.*|#!$VENV_PYTHON|" "$dest" && rm "$dest.bak"
        chmod +x "$dest"
    fi
done

# 5. Install launchd plist for kokoro-server
echo "→ Installing kokoro-server launchd service..."
cat > "$PLIST" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kokoro-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$BIN_DIR/kokoro-server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/kokoro-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/kokoro-server.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTORCH_ENABLE_MPS_FALLBACK</key>
        <string>1</string>
    </dict>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# 6. Add shh alias
if ! grep -q "kokoro-stop" "$HOME/.zshrc" 2>/dev/null; then
    echo 'alias shh="~/bin/kokoro-stop.sh"' >> "$HOME/.zshrc"
fi

# 7. Wait for server
echo ""
echo "→ Waiting for Kokoro model to load (~10 seconds)..."
for i in $(seq 1 30); do
    if [ -S "/tmp/kokoro-tts.sock" ]; then
        echo "   Server is ready!"
        break
    fi
    sleep 1
done

echo ""
echo "✅  Vox is ready! Press Option+Shift+A to talk."
echo ""
