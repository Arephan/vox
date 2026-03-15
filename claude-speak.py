#!/Users/hankim/kokoro-env/bin/python3.10
"""Claude Code Stop hook: sends text to kokoro-server for instant TTS."""
import json
import socket
import sys
import os

SOCKET_PATH = "/tmp/kokoro-tts.sock"


def send_to_server(msg):
    """Send a message to the kokoro-server. Fails silently if server is down."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(msg).encode("utf-8"))
        sock.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        pass  # Server not running — silent fail


def main():
    # Skip if Vox triggered this via claude -p
    if os.environ.get("VOX_NO_HOOK"):
        return

    data = json.load(sys.stdin)

    message = data.get("last_assistant_message", "")

    if not message:
        msg = data.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            message = " ".join(parts)
        elif isinstance(content, str):
            message = content

    if message:
        send_to_server({"cmd": "speak", "text": message})


if __name__ == "__main__":
    main()
