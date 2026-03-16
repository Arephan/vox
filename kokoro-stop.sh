#!/bin/bash
# Stop kokoro speech immediately
python3 -c "
import socket, json
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(1)
sock.connect('/tmp/kokoro-tts.sock')
sock.sendall(json.dumps({'cmd': 'stop'}).encode())
sock.close()
" 2>/dev/null
