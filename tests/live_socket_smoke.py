from __future__ import annotations

import os
import sys
import time
import socketio

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = os.getenv("JARVIS_TEST_URL", "http://127.0.0.1:5000")
client = socketio.Client(logger=False, engineio_logger=False, reconnection=False)
replies = []
statuses = []

@client.event
def connect():
    print("SOCKET_CONNECTED")
    client.emit("user_speech", {"text": "hello can you hear me", "request_id": "live-one", "language_hint": "english_or_mixed"})
    client.emit("user_speech", {"text": "please explain what you can help me with", "request_id": "live-two", "language_hint": "english_or_mixed"})

@client.on("status")
def on_status(data):
    statuses.append(data)

@client.on("jarvis_reply")
def on_reply(data):
    replies.append(data)
    print("LIVE_REPLY", data.get("request_id"), data.get("provider"), data.get("model"), data.get("text", "")[:180].replace("\n", " "))

@client.event
def connect_error(data):
    print("SOCKET_CONNECT_ERROR", data)

try:
    client.connect(URL, wait_timeout=10)
    deadline = time.time() + 35
    while time.time() < deadline and len(replies) < 2:
        time.sleep(0.25)
    print("LIVE_REPLY_COUNT", len(replies))
    print("LIVE_STATUS_COUNT", len(statuses))
    if len(replies) != 2:
        raise SystemExit(2)
finally:
    if client.connected:
        client.disconnect()
