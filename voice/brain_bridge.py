"""Socket.IO adapter from VoiceService to the existing JARVIS brain contract."""

from __future__ import annotations

from collections.abc import Iterator
import logging
import queue
import re
import threading
import time
import uuid
from typing import Any

import socketio

LOG = logging.getLogger("jarvis.voice.brain")
_END = object()


class VoiceBrainBridge:
    def __init__(self, *, url: str = "http://127.0.0.1:5000", session_id: str = "primary-user", token: str = ""):
        self.url = url
        self.session_id = session_id
        self.token = token
        self.socket = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self._requests: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

        @self.socket.on("jarvis_reply_token")
        def on_token(payload):
            if not isinstance(payload, dict):
                return
            request_id = str(payload.get("request_id", ""))
            with self._lock:
                state = self._requests.get(request_id)
            if state is None:
                return
            if payload.get("reset"):
                state["buffer"] = ""
                state["sent_prefix"] = ""
                state["queue"].put({"reset": True})
                return
            token_text = str(payload.get("token", ""))
            state["buffer"] += token_text
            while True:
                match = re.search(r"[.!?۔؟](?:[\"'’)]*)\s+", state["buffer"])
                if not match:
                    break
                sentence = state["buffer"][:match.end()].strip()
                state["buffer"] = state["buffer"][match.end():]
                if sentence:
                    state["sent_prefix"] += sentence
                    if not state.get("first_sentence_logged"):
                        state["first_sentence_logged"] = True
                        LOG.info(
                            "stt_done_to_first_llm_sentence_ms=%.1f",
                            (time.perf_counter() - state["submitted_at"]) * 1000,
                        )
                    state["queue"].put(sentence)

        @self.socket.on("jarvis_reply")
        def on_reply(payload):
            if not isinstance(payload, dict):
                return
            request_id = str(payload.get("request_id", ""))
            with self._lock:
                state = self._requests.get(request_id)
            if state is None:
                return
            remaining = str(state.get("buffer", "")).strip()
            final_text = str(payload.get("text", "")).strip()
            if remaining:
                if not state.get("first_sentence_logged"):
                    state["first_sentence_logged"] = True
                    LOG.info(
                        "stt_done_to_first_llm_sentence_ms=%.1f",
                        (time.perf_counter() - state["submitted_at"]) * 1000,
                    )
                state["queue"].put(remaining)
            elif not state.get("sent_prefix") and final_text:
                if not state.get("first_sentence_logged"):
                    state["first_sentence_logged"] = True
                    LOG.info(
                        "stt_done_to_first_llm_sentence_ms=%.1f",
                        (time.perf_counter() - state["submitted_at"]) * 1000,
                    )
                state["queue"].put(final_text)
            state["queue"].put(_END)
            state["done"].set()

        @self.socket.on("status")
        def on_status(payload):
            if isinstance(payload, dict) and payload.get("state") in {"failed", "error"}:
                LOG.warning("Brain request ended with state=%s", payload.get("state"))

    def connect(self) -> None:
        if self.socket.connected:
            return
        auth: dict[str, str] = {"role": "local_voice", "session_id": self.session_id}
        if self.token:
            auth["token"] = self.token
        self.socket.connect(self.url, auth=auth, wait=True, wait_timeout=10)

    def submit(self, text: str, lang: str) -> Iterator[str]:
        request_id = f"voice-{uuid.uuid4().hex[:12]}"
        state = {
            "queue": queue.Queue(),
            "done": threading.Event(),
            "buffer": "",
            "sent_prefix": "",
            "submitted_at": time.perf_counter(),
            "first_sentence_logged": False,
        }
        with self._lock:
            self._requests[request_id] = state

        def request() -> None:
            try:
                self.connect()
                language_hint = "ur" if lang == "ur" else "en"
                self.socket.emit(
                    "user_speech",
                    {
                        "text": str(text),
                        "request_id": request_id,
                        "language_hint": language_hint,
                        "lang": language_hint,
                        "source": "local_voice",
                    },
                )
                if not state["done"].wait(timeout=180):
                    state["queue"].put(RuntimeError("JARVIS reply timed out"))
                    state["queue"].put(_END)
            except Exception as exc:
                state["queue"].put(exc)
                state["queue"].put(_END)

        threading.Thread(target=request, name=f"jarvis-brain-{request_id}", daemon=True).start()

        def sentences() -> Iterator[str]:
            try:
                while True:
                    item = state["queue"].get()
                    if item is _END:
                        return
                    if isinstance(item, Exception):
                        LOG.warning("Voice brain request failed: %s", type(item).__name__)
                        continue
                    if isinstance(item, dict) and item.get("reset"):
                        yield item
                        continue
                    yield str(item)
            finally:
                with self._lock:
                    self._requests.pop(request_id, None)

        return sentences()

    def close(self) -> None:
        try:
            self.socket.disconnect()
        except Exception:
            pass
