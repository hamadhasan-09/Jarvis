"""Socket.IO adapter from VoiceService to the existing JARVIS brain contract."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import logging
import queue
import re
import threading
import time
import uuid
from typing import Any

import socketio
from core.contracts import begin_turn, is_turn_current, end_turn

LOG = logging.getLogger("jarvis.voice.brain")
_END = object()


class VoiceBridgeError(RuntimeError):
    """Safe operational failure code; never includes transport credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Voice brain request failed: {code}")


@dataclass(frozen=True)
class ReplySentence:
    request_id: str
    text: str
    session_id: str | None = None
    turn_id: str | None = None


class ReplyStream:
    """Cancelable sentence iterator for the local voice client."""

    def __init__(self, bridge: "VoiceBrainBridge", request_id: str, state: dict[str, Any]):
        self.bridge = bridge
        self.request_id = request_id
        self.state = state
        self._closed = False

    @property
    def session_id(self) -> str:
        return str(self.state.get("session_id") or self.bridge.session_id)

    @property
    def turn_id(self) -> str | None:
        value = self.state.get("turn_id")
        return str(value) if value else None

    def __iter__(self):
        return self

    def __next__(self):
        if self._closed:
            raise StopIteration
        while True:
            item = self.state["queue"].get()
            if item is _END:
                self._closed = True
                with self.bridge._lock:
                    self.bridge._requests.pop(self.request_id, None)
                raise StopIteration
            if isinstance(item, Exception):
                self._closed = True
                with self.bridge._lock:
                    self.bridge._requests.pop(self.request_id, None)
                raise item
            if isinstance(item, dict) and item.get("reset"):
                return item
            return ReplySentence(self.request_id, str(item), self.session_id, self.turn_id)

    def cancel(self, heard_text: str) -> None:
        if self._closed or self.state["cancelled"].is_set():
            return
        self.bridge._finish(self.state, cancelled=True)
        if not self.bridge.socket.connected:
            return
        try:
            self.bridge.socket.emit("voice_cancel", {
                "request_id": self.request_id, "turn_id": self.turn_id,
                "heard_text": str(heard_text or ""),
            })
        except Exception:
            LOG.warning("voice_stage=brain state=failed code=cancel_transport_error")


class VoiceBrainBridge:
    def __init__(self, *, url: str = "http://127.0.0.1:5000", session_id: str = "primary-user", token: str = "", reply_timeout: float = 180.0):
        self.url = url
        self.session_id = str(session_id).strip()[:120] or "primary-user"
        self.token = token
        self.socket = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self._requests: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._closed = threading.Event()
        self._workers: set[threading.Thread] = set()
        self.reply_timeout = max(.01, float(reply_timeout))

        def serialized(handler):
            def locked(payload):
                with self._lock:
                    return handler(payload)
            return locked

        def update_turn(state, payload) -> bool:
            if state["done"].is_set() or state["cancelled"].is_set():
                return False
            # The server echoes the locally minted wire identity. Never adopt a
            # different connection/turn merely because it is current elsewhere.
            if any(payload.get(key) and str(payload[key]) != str(state.get(key))
                   for key in ("session_id", "turn_id")):
                LOG.warning("voice_stage=brain state=ignored code=reply_identity_mismatch")
                return False
            turn_id = str(state.get("turn_id") or "")
            session_id = str(state.get("session_id") or self.session_id)
            if turn_id:
                state["turn_id"] = turn_id
                state["session_id"] = session_id
                if not is_turn_current(session_id, turn_id):
                    state["cancelled"].set()
                    while True:
                        try:
                            state["queue"].get_nowait()
                        except queue.Empty:
                            break
                    state["queue"].put(_END)
                    state["done"].set()
                    return False
            return not state["cancelled"].is_set()

        @self.socket.on("jarvis_reply_token")
        @serialized
        def on_token(payload):
            if not isinstance(payload, dict):
                return
            request_id = str(payload.get("request_id", ""))
            with self._lock:
                state = self._requests.get(request_id)
            if state is None or not update_turn(state, payload):
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
        @serialized
        def on_reply(payload):
            if not isinstance(payload, dict):
                return
            request_id = str(payload.get("request_id", ""))
            with self._lock:
                state = self._requests.get(request_id)
            if state is None or not update_turn(state, payload):
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
        @serialized
        def on_status(payload):
            if not isinstance(payload, dict):
                return
            request_id = str(payload.get("request_id", ""))
            with self._lock:
                state = self._requests.get(request_id)
            if state is not None:
                update_turn(state, payload)
            if payload.get("state") in {"failed", "error"}:
                LOG.warning("Brain request ended with state=%s", payload.get("state"))

        @self.socket.on("authorization_error")
        def on_authorization_error(payload):
            request_id = str(payload.get("request_id", "")) if isinstance(payload, dict) else ""
            with self._lock:
                states = ([self._requests[request_id]] if request_id in self._requests
                          else [] if request_id else list(self._requests.values()))
            for state in states:
                self._finish(state, error=VoiceBridgeError("authorization_required"))

        @self.socket.on("disconnect")
        def on_disconnect(*_args):
            with self._lock:
                states = list(self._requests.values())
            for state in states:
                self._finish(state, error=VoiceBridgeError("disconnected"))

    def _finish(self, state, *, error=None, cancelled=False) -> None:
        with self._lock:
            if state["done"].is_set() and not cancelled:
                return
            if error is not None or cancelled:
                state["cancelled"].set()
                end_turn(state.get("session_id"), state.get("turn_id"))
                while True:
                    try:
                        state["queue"].get_nowait()
                    except queue.Empty:
                        break
            if error is not None:
                state["queue"].put(error)
                LOG.warning("voice_stage=brain state=failed code=%s", error.code)
            state["queue"].put(_END)
            state["done"].set()

    def connect(self) -> None:
        with self._connect_lock:
            if self._closed.is_set():
                raise VoiceBridgeError("bridge_closed")
            if self.socket.connected:
                return
            auth: dict[str, str] = {"role": "local_voice", "session_id": self.session_id}
            if self.token:
                auth["token"] = self.token
            self.socket.connect(self.url, auth=auth, wait=True, wait_timeout=10)
            LOG.info("voice_stage=brain state=connected")

    def submit(self, text: str, lang: str) -> Iterator[str]:
        if self._closed.is_set():
            raise VoiceBridgeError("bridge_closed")
        request_id = f"voice-{uuid.uuid4().hex[:12]}"
        turn_id = begin_turn(self.session_id)
        state = {
            "queue": queue.Queue(),
            "done": threading.Event(),
            "buffer": "",
            "sent_prefix": "",
            "submitted_at": time.perf_counter(),
            "first_sentence_logged": False,
            "cancelled": threading.Event(),
            "session_id": self.session_id,
            "turn_id": turn_id,
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
                        "turn_id": turn_id,
                        "language_hint": language_hint,
                        "lang": language_hint,
                        "source": "local_voice",
                    },
                )
                if not state["done"].wait(timeout=self.reply_timeout):
                    self._finish(state, error=VoiceBridgeError("reply_timeout"))
                    if self.socket.connected:
                        try:
                            self.socket.emit("voice_cancel", {"request_id": request_id, "turn_id": turn_id, "heard_text": ""})
                        except Exception:
                            LOG.warning("voice_stage=brain state=failed code=timeout_cancel_transport_error")
            except Exception:
                self._finish(state, error=VoiceBridgeError("transport_unavailable"))
            finally:
                with self._lock:
                    self._workers.discard(threading.current_thread())

        worker = threading.Thread(target=request, name=f"jarvis-brain-{request_id}", daemon=True)
        with self._lock:
            if self._closed.is_set():
                self._requests.pop(request_id, None)
                end_turn(self.session_id, turn_id)
                raise VoiceBridgeError("bridge_closed")
            self._workers.add(worker)
            worker.start()

        return ReplyStream(self, request_id, state)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            states = list(self._requests.values())
        for state in states:
            self._finish(state, error=VoiceBridgeError("bridge_closed"))
        try:
            self.socket.disconnect()
        except Exception:
            LOG.warning("voice_stage=shutdown state=failed code=disconnect_error")
        with self._lock:
            workers = list(self._workers)
            self._requests.clear()
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=1)
        LOG.info("voice_stage=shutdown state=bridge_closed pending_workers=%s",
                 sum(worker.is_alive() for worker in workers))
