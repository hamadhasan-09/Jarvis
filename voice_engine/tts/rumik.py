"""Provider-agnostic Rumik WebSocket TTS adapter.

The adapter follows the useful lifecycle from pipecat-rumik without importing
Pipecat application state: mint one session, keep one warm WebSocket, serialize
one active generation, send explicit cancel, and discard orphan audio.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from urllib.parse import urlencode
from typing import Any

from voice_engine.audio.format import validate_pcm
from voice_engine.models import AudioChunk
from voice_engine.profiles.voice_profile import VoiceProfile
from voice_engine.tts.base import TTSProvider, TTSProviderError


RUMIK_SAMPLE_RATE = 24_000
RUMIK_CHANNELS = 1


class RumikWebSocketTTS(TTSProvider):
    name = "rumik"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        gateway_url: str | None = None,
        model_name: str | None = None,
        profile: VoiceProfile | None = None,
        connect_timeout: float = 10.0,
    ):
        super().__init__(profile=profile)
        self.api_key = api_key or os.getenv("RUMIK_API_KEY", "").strip()
        self.gateway_url = (gateway_url or os.getenv("RUMIK_GATEWAY_URL", "")).rstrip("/")
        self.model_name = model_name or os.getenv("RUMIK_MODEL", "muga")
        self.connect_timeout = max(1.0, float(connect_timeout))
        self._websocket: Any | None = None
        self._request_lock = asyncio.Lock()
        self._active_generation: str | None = None
        self._cancel_requested: set[str] = set()

    def _payload(self, text: str, *, include_model: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": " ".join(str(text).split())}
        if include_model:
            payload["model"] = self.model_name
        if self.profile.language and self.profile.language.lower() not in {"auto", "automatic"}:
            payload["language"] = self.profile.language
        if self.profile.speech_rate is not None:
            payload["speech_rate"] = self.profile.speech_rate
        if self.profile.description is not None:
            payload["description"] = self.profile.description
        speaker = self.profile.speaker or self.profile.voice
        if speaker is not None:
            payload["speaker"] = speaker
        if self.profile.pitch is not None:
            payload["f0_up_key"] = self.profile.pitch
        for name in ("temperature", "top_p", "top_k", "repetition_penalty", "max_new_tokens"):
            value = getattr(self.profile, name)
            if value is not None:
                payload[name] = value
        return payload

    async def connect(self) -> None:
        if self._websocket is not None and not bool(getattr(self._websocket, "closed", False)):
            return
        self._websocket = None
        if not self.api_key or not self.gateway_url:
            raise TTSProviderError("RUMIK_API_KEY and RUMIK_GATEWAY_URL are required")
        try:
            import aiohttp
            try:
                from websockets.asyncio.client import connect as websocket_connect  # type: ignore
            except ImportError:
                from websockets import connect as websocket_connect  # type: ignore
        except ImportError as exc:
            raise TTSProviderError(
                "Rumik requires aiohttp and websockets; install optional voice dependencies"
            ) from exc
        try:
            timeout = aiohttp.ClientTimeout(total=self.connect_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.gateway_url}/v1/tts/ws-connect",
                    json=self._payload("init", include_model=True),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status != 200:
                        detail = (await response.text())[:1000]
                        raise TTSProviderError(f"Rumik session failed ({response.status}): {detail}")
                    session_info = await response.json()
            ws_url = str(session_info.get("ws_url", ""))
            token = str(session_info.get("token", ""))
            if not ws_url or not token:
                raise TTSProviderError("Rumik session did not return ws_url and token")
            separator = "&" if "?" in ws_url else "?"
            self._websocket = await websocket_connect(
                f"{ws_url}{separator}{urlencode({'token': token})}",
                ping_interval=20,
                ping_timeout=30,
            )
        except TTSProviderError:
            raise
        except Exception as exc:
            self._websocket = None
            raise TTSProviderError(f"Rumik WebSocket connect failed: {exc}") from exc

    async def synthesize_stream(self, text: str, *, generation_id: str):
        generation_id = str(generation_id)
        if not "".join(str(text).split()):
            return
        async with self._request_lock:
            await self.connect()
            self._active_generation = generation_id
            self._cancel_requested.discard(generation_id)
            try:
                await self._websocket.send(json.dumps(self._payload(text)))
                while True:
                    if generation_id in self._cancel_requested:
                        return
                    message = await self._websocket.recv()
                    if isinstance(message, bytes):
                        # Rumik contract: 24 kHz, mono, signed-16 raw PCM.
                        pcm = validate_pcm(
                            message,
                            sample_rate=RUMIK_SAMPLE_RATE,
                            channels=RUMIK_CHANNELS,
                            expected_sample_rate=RUMIK_SAMPLE_RATE,
                            expected_channels=RUMIK_CHANNELS,
                        )
                        if generation_id != self._active_generation or generation_id in self._cancel_requested:
                            continue
                        yield AudioChunk(pcm, RUMIK_SAMPLE_RATE, RUMIK_CHANNELS, generation_id)
                        continue
                    try:
                        control = json.loads(message)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    message_type = control.get("type")
                    if message_type in {"queued"}:
                        continue
                    if message_type in {"done", "cancelled"}:
                        return
                    if message_type == "timeout":
                        raise TTSProviderError(str(control.get("message", "Rumik TTS timeout")))
                    if message_type == "error" or control.get("error"):
                        raise TTSProviderError(f"Rumik TTS error: {control}")
            except asyncio.CancelledError:
                await self.cancel(generation_id)
                raise
            except TTSProviderError:
                raise
            except Exception as exc:
                websocket = self._websocket
                self._websocket = None
                if websocket is not None:
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                raise TTSProviderError(f"Rumik WebSocket receive failed: {exc}") from exc
            finally:
                if self._active_generation == generation_id:
                    self._active_generation = None
                    self._cancel_requested.discard(generation_id)

    async def cancel(self, generation_id: str | None = None) -> None:
        active = self._active_generation
        if generation_id is not None and active not in {None, str(generation_id)}:
            return
        target = str(generation_id or active or "")
        if target:
            self._cancel_requested.add(target)
        if self._websocket is not None and target and active == target:
            try:
                await self._websocket.send(json.dumps({"type": "cancel"}))
            except Exception:
                pass

    async def close(self) -> None:
        websocket = self._websocket
        self._websocket = None
        self._active_generation = None
        if websocket is not None:
            try:
                await websocket.send(json.dumps({"type": "close"}))
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass


RumikTTSProvider = RumikWebSocketTTS
