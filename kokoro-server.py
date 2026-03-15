#!/Users/hankim/kokoro-env/bin/python3.10
"""Persistent Kokoro TTS server — keeps model warm, streams audio instantly."""
import collections
import json
import os
import queue
import re
import signal
import socket
import sys
import threading

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import sounddevice as sd
from kokoro import KPipeline

SOCKET_PATH = "/tmp/kokoro-tts.sock"
pipeline = None
play_lock = threading.Lock()
stop_event = threading.Event()


def clean_for_speech(text):
    """Strip markdown formatting for cleaner speech."""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'[#*_~>|]', '', text)
    text = re.sub(r'^\s*[-]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def make_chunks(text):
    """Split into chunks: first sentence alone (for fast start), then groups of 3."""
    parts = re.split(r'(?<=[.!?])\s+', text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return []
    # First sentence solo for low latency
    chunks = [parts[0]]
    # Batch remaining sentences in groups of 3 for natural flow
    for i in range(1, len(parts), 3):
        chunks.append(' '.join(parts[i:i+3]))
    return chunks


def _play_chunks(chunks, voice):
    """Core playback: synthesize and play chunks. Must be called with play_lock held."""
    global pipeline

    audio_queue = queue.Queue(maxsize=4)
    synth_done = threading.Event()

    def synthesize():
        for chunk in chunks:
            if stop_event.is_set():
                break
            for _, _, audio in pipeline(chunk, voice=voice, speed=1.15):
                if stop_event.is_set():
                    break
                audio_queue.put(audio)
        synth_done.set()

    sample_buf = collections.deque()
    buf_lock = threading.Lock()
    fill_done = threading.Event()

    def callback(outdata, frames, time_info, status):
        out = np.zeros(frames, dtype=np.float32)
        remaining = frames
        offset = 0
        while remaining > 0:
            with buf_lock:
                if not sample_buf:
                    break
                chunk = sample_buf[0]
                n = min(remaining, len(chunk))
                out[offset:offset + n] = chunk[:n]
                if n == len(chunk):
                    sample_buf.popleft()
                else:
                    sample_buf[0] = chunk[n:]
            offset += n
            remaining -= n
        outdata[:, 0] = out
        with buf_lock:
            still_buffered = sum(len(c) for c in sample_buf)
        if fill_done.is_set() and still_buffered == 0 and remaining == frames:
            raise sd.CallbackStop

    def fill_buffer():
        while True:
            if stop_event.is_set():
                break
            try:
                audio = audio_queue.get(timeout=0.05)
                audio = audio.cpu().numpy() if hasattr(audio, 'cpu') else audio
                audio = np.ascontiguousarray(audio.flatten().astype(np.float32))
                with buf_lock:
                    sample_buf.append(audio)
            except queue.Empty:
                if synth_done.is_set() and audio_queue.empty():
                    break
        fill_done.set()

    t = threading.Thread(target=synthesize, daemon=True)
    t.start()
    f = threading.Thread(target=fill_buffer, daemon=True)
    f.start()

    while not stop_event.is_set():
        with buf_lock:
            buffered = sum(len(c) for c in sample_buf)
        if buffered >= 3600 or fill_done.is_set():
            break
        threading.Event().wait(0.01)

    finished = threading.Event()

    def on_finish():
        finished.set()

    stream = sd.OutputStream(
        samplerate=24000, channels=1, dtype='float32',
        blocksize=256, callback=callback, finished_callback=on_finish
    )
    stream.start()
    try:
        while not stop_event.is_set() and not finished.is_set():
            threading.Event().wait(0.05)
    finally:
        stream.stop()
        stream.close()


def speak(text, voice=None):
    """Stop any current speech and play text immediately."""
    global pipeline
    text = clean_for_speech(text)
    if not text or len(text) < 3:
        return
    if len(text) > 2000:
        text = text[:2000] + "... I'll stop reading here."
    voice = voice or 'af_heart'
    chunks = make_chunks(text)
    if not chunks:
        return

    stop_event.set()
    sd.stop()
    with play_lock:
        stop_event.clear()
        _play_chunks(chunks, voice)


def speak_append(text, voice=None):
    """Queue text to play after current speech finishes (no interruption)."""
    global pipeline
    text = clean_for_speech(text)
    if not text or len(text) < 3:
        return
    if len(text) > 2000:
        text = text[:2000] + "... I'll stop reading here."
    voice = voice or 'af_heart'
    chunks = make_chunks(text)
    if not chunks:
        return

    # Acquire lock — blocks until current speech finishes, then plays
    with play_lock:
        if stop_event.is_set():
            return  # was interrupted, skip
        _play_chunks(chunks, voice)


def handle_client(conn):
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        conn.close()

        msg = json.loads(data.decode("utf-8"))
        cmd = msg.get("cmd", "speak")

        if cmd == "stop":
            stop_event.set()
            sd.stop()
            return

        if cmd == "ping":
            return

        text = msg.get("text", "")
        voice = msg.get("voice")
        if text:
            if cmd == "speak_append":
                t = threading.Thread(target=speak_append, args=(text, voice), daemon=True)
            else:
                t = threading.Thread(target=speak, args=(text, voice), daemon=True)
            t.start()
    except Exception as e:
        print(f"[kokoro-server] error: {e}", file=sys.stderr)


def cleanup(*_):
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    sys.exit(0)


def main():
    global pipeline

    print("[kokoro-server] Loading Kokoro model...", file=sys.stderr)
    pipeline = KPipeline(lang_code='a')
    # Warm up with a tiny utterance so first real request is fast
    for _, _, audio in pipeline("ready", voice='af_heart', speed=1.15):
        pass
    print("[kokoro-server] Model loaded and warm. Listening.", file=sys.stderr)

    # Clean up stale socket
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(5)
    # Make socket accessible
    os.chmod(SOCKET_PATH, 0o777)

    print(f"[kokoro-server] Listening on {SOCKET_PATH}", file=sys.stderr)

    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
