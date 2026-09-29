"""Socket.IO adapter for the existing JARVIS voice event contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import threading
from typing import Any

import socketio


class VoiceSocketBridge:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:5000",
        session_id: str = "primary-user",
        auth_token: str | None = None,
        on_token: Callable[[dict[str, Any]], None] | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        on_approval: Callable[[dict[str, Any]], None] | None = None,
        socket_client: Any | None = None,
    ):
        self.url = url
        self.session_id = str(session_id)
        self.auth_token = str(auth_token or "").strip()
        self.socket = socket_client or socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self.on_token = on_token
        self.on_status = on_status
        self.on_approval = on_approval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: dict[str, asyncio.Future] = {}
        self._lock = threading.RLock()
        self.connected = False

        @self.socket.event
        def connect():
            self.connected = True

        @self.socket.event
        def disconnect():
            self.connected = False
            with self._lock:
                waiters = list(self._waiters.values())
                self._waiters.clear()
            for future in waiters:
                self._resolve(future, exception=ConnectionError("JARVIS Socket.IO disconnected"))

        @self.socket.on("jarvis_reply_token")
        def _token(data):
            if isinstance(data, dict) and self.on_token:
                self.on_token(data)

        @self.socket.on("jarvis_reply")
        def _reply(data):
            if not isinstance(data, dict):
                return
            request_id = str(data.get("request_id", ""))
            with self._lock:
                future = self._waiters.pop(request_id, None)
            if future is not None:
                self._resolve(future, result=data)

        @self.socket.on("status")
        def _status(data):
            if isinstance(data, dict) and self.on_status:
                self.on_status(data)

        @self.socket.on("approval_request")
        def _approval(data):
            if isinstance(data, dict) and self.on_approval:
                self.on_approval(data)

    def connect(self) -> None:
        if self.connected:
            return
        auth = {
            "role": "local_voice",
            "session_id": self.session_id,
        }
        if self.auth_token:
            auth["token"] = self.auth_token
        self.socket.connect(self.url, auth=auth, wait=True, wait_timeout=10)
        self.connected = True

    def disconnect(self) -> None:
        try:
            self.socket.disconnect()
        finally:
            self.connected = False

    def send_user_speech(
        self,
        *,
        text: str,
        request_id: str,
        turn_id: str,
        language_hint: str = "",
    ) -> None:
        self.connect()
        self.socket.emit(
            "user_speech",
            {
                "text": str(text),
                "request_id": str(request_id),
                "turn_id": str(turn_id),
                "session_id": self.session_id,
                "language_hint": str(language_hint),
                "source": "local_voice",
            },
        )

    async def request_reply(
        self,
        *,
        text: str,
        request_id: str,
        turn_id: str,
        language_hint: str = "",
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        self._loop = asyncio.get_running_loop()
        future = self._loop.create_future()
        with self._lock:
            self._waiters[str(request_id)] = future
        try:
            self.send_user_speech(
                text=text,
                request_id=request_id,
                turn_id=turn_id,
                language_hint=language_hint,
            )
            return await asyncio.wait_for(future, timeout=max(1.0, float(timeout)))
        finally:
            with self._lock:
                self._waiters.pop(str(request_id), None)

    def send_approval_response(
        self, *, approval_id: str, task_id: str, result: bool
    ) -> None:
        self.connect()
        self.socket.emit(
            "approval_response",
            {
                "id": str(approval_id),
                "task_id": str(task_id),
                "result": type(result) is bool and result,
                "session_id": self.session_id,
                "source": "local_voice",
            },
        )

    def _resolve(self, future: asyncio.Future, *, result=None, exception=None) -> None:
        loop = self._loop
        if loop is None or future.done():
            return
        callback = future.set_exception if exception is not None else future.set_result
        value = exception if exception is not None else result
        loop.call_soon_threadsafe(callback, value)
