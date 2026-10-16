"""Urdu output routing to a configured local Qalb OpenAI-compatible server.

This module never invokes tools. In hybrid mode the caller must run the existing
brain first and pass its result here for Urdu phrasing.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import json
import logging
import re
import subprocess
import threading
import time
from typing import Any
from collections.abc import Callable
from urllib.parse import urlparse
from pathlib import Path

import httpx

from voice.config import VoiceConfig, load_config

LOG = logging.getLogger("jarvis.brain.router")
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_LATIN = re.compile(r"[A-Za-z]")
_TEMPLATE_TOKENS = re.compile(
    r"<\|im_start\|>\s*(?:assistant|user|system|developer)?|<\|[^|]{1,64}\|>|"
    r"\[/?INST\]|<s>|</s>|<<SYS>>|<</SYS>>",
    re.IGNORECASE,
)
_FABRICATED_TURN = re.compile(r"(?:^|\n)\s*(?:user|assistant|system|developer)\s*:", re.IGNORECASE)
_LLAMA3_ROLES = frozenset({"system", "developer", "user", "assistant"})
_QALB_MODEL_SYSTEM_PROMPT = (
    "آپ ایک مددگار اور بے ضرر مصنوعی ذہانت کے اسسٹنٹ ہیں۔ "
    "آپ اردو میں سوالات کے درست جوابات دیتے ہیں۔"
)
_QALB_RETRY_INSTRUCTION = (
    "صرف اردو رسم الخط میں جواب دیں۔ رومن اردو، ہندی/دیوناگری، "
    "قالبی ٹوکنز، تکرار یا خالی جواب نہ دیں۔"
)


def _llama3_prompt(messages: Sequence[dict[str, str]]) -> str:
    """Serialize chat history with the Llama 3 Instruct transcript template.

    llama.cpp adds the model's BOS token when it tokenizes a string prompt, so
    the wire prompt starts at the first message header to avoid a duplicate BOS.
    """
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).strip().lower()
        if role not in _LLAMA3_ROLES:
            raise ValueError(f"unsupported Qalb message role: {role}")
        content = str(message.get("content", ""))
        # Keep literal tokens in user history inert; only our serializer creates
        # the structural Llama 3 tokens.
        content = re.sub(r"<\|", "<\u200b|", content)
        parts.append(f"<|start_header_id|>{role}<|end_header_id|>\n\n{content}<|eot_id|>")
    parts.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    return "".join(parts)


def strip_chat_template_tokens(text: str) -> str:
    value = _TEMPLATE_TOKENS.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", value).strip()


def validate_urdu_output(text: str, *, max_latin_ratio: float = 0.35) -> tuple[bool, str]:
    raw_value = str(text or "")
    if _TEMPLATE_TOKENS.search(raw_value):
        return False, "contains leaked chat-template token"
    if _FABRICATED_TURN.search(raw_value):
        return False, "contains fabricated conversation turn"
    value = strip_chat_template_tokens(raw_value)
    if not value:
        return False, "empty output"
    if _DEVANAGARI.search(value):
        return False, "contains Devanagari"
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return False, "no letters in output"
    if letters and sum(bool(_LATIN.fullmatch(character)) for character in letters) / len(letters) > max_latin_ratio:
        return False, "mostly Latin script"
    sentences = [part.strip() for part in re.split(r"[۔؟.!]+", value) if part.strip()]
    normalized = [re.sub(r"\W+", "", item).casefold() for item in sentences]
    if not value or (len(normalized) > 1 and len(set(normalized)) < len(normalized) * 0.6):
        return False, "repetitive output"
    words = re.findall(r"\w+", value.casefold())
    if len(words) >= 8:
        half = len(words) // 2
        if words[:half] == words[half : half * 2]:
            return False, "repeated phrase"
    return True, "ok"


class QalbClient:
    def __init__(self, config: VoiceConfig | None = None, *, client: httpx.Client | None = None):
        self.config = config or load_config()
        self.settings = self.config.section("brain").get("qalb", {})
        self._client = client
        self._process: subprocess.Popen | None = None

    @property
    def model_path(self) -> str:
        return str(self.settings.get("model_path", "")).strip()

    @property
    def ready_to_start(self) -> bool:
        return bool(self.model_path)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=float(self.settings.get("timeout_s", 30)))
        return self._client

    def _api_url(self) -> str:
        base = str(self.settings.get("base_url", "http://127.0.0.1:8081/v1")).rstrip("/")
        return f"{base}/chat/completions"

    def _completion_url(self) -> str:
        parsed = urlparse(str(self.settings.get("base_url", "http://127.0.0.1:8081/v1")).rstrip("/"))
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3].rstrip("/")
        return parsed._replace(
            path=f"{path}/completion",
            params="",
            query="",
            fragment="",
        ).geturl()

    def health_check(self) -> bool:
        server = str(self.settings.get("server", "llama_cpp"))
        base = str(self.settings.get("base_url", "http://127.0.0.1:8081/v1")).rstrip("/")
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        url = f"{root}/health" if server == "llama_cpp" else f"{root}/api/tags"
        try:
            response = self._http().get(url, timeout=min(5.0, float(self.settings.get("timeout_s", 30))))
            return response.is_success
        except httpx.HTTPError:
            return False

    def start_server(self) -> bool:
        if not self.settings.get("auto_start", False):
            return False
        server = str(self.settings.get("server", "llama_cpp"))
        if server != "llama_cpp":
            LOG.warning("Qalb auto-start is implemented only for llama.cpp; start Ollama separately")
            return False
        if not self.model_path:
            LOG.warning("Qalb auto-start pending: set brain.qalb.model_path to your existing GGUF file")
            return False
        path = self.model_path
        if not Path(path).is_file():
            LOG.warning("Qalb GGUF path does not exist; server not started: %s", path)
            return False
        parsed = urlparse(str(self.settings.get("base_url", "http://127.0.0.1:8081/v1")))
        executable = str(self.settings.get("executable", "llama-server"))
        command = [
            executable,
            "-m", path,
            "--host", parsed.hostname or "127.0.0.1",
            "--port", str(parsed.port or 8081),
            "-c", str(int(self.settings.get("context_tokens", 8192))),
            "-ngl", str(int(self.settings.get("gpu_layers", 99))),
        ]
        chat_template = str(self.settings.get("chat_template", "")).strip()
        if chat_template:
            command.extend(["--chat-template", chat_template])
        try:
            self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as exc:
            LOG.warning("Could not start llama-server: %s", exc)
            return False

    def stop_server(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def warmup(self) -> bool:
        if not self.health_check():
            return False
        try:
            list(self.stream_chat([{"role": "user", "content": "سلام۔"}], max_tokens=24))
            return True
        except Exception as exc:
            LOG.warning("Qalb warm-up failed: %s", type(exc).__name__)
            return False

    def stream_chat(self, messages: Sequence[dict[str, str]], *, max_tokens: int | None = None) -> Iterator[str]:
        server = str(self.settings.get("server", "llama_cpp"))
        if server == "llama_cpp":
            yield from self._stream_llama_cpp(messages, max_tokens=max_tokens)
        elif server == "ollama":
            yield from self._stream_ollama(messages, max_tokens=max_tokens)
        else:
            yield from self._stream_openai(messages, max_tokens=max_tokens)

    def _stream_llama_cpp(self, messages: Sequence[dict[str, str]], *, max_tokens: int | None) -> Iterator[str]:
        stops = ["<|eot_id|>", "<|end_of_text|>", "<|start_header_id|>"]
        configured_stops = self.settings.get("stop")
        if isinstance(configured_stops, (list, tuple)):
            stops.extend(str(stop) for stop in configured_stops if str(stop))
        payload = {
            "prompt": _llama3_prompt(messages),
            "temperature": float(self.settings.get("temperature", 0.6)),
            "top_p": float(self.settings.get("top_p", 0.9)),
            "repeat_penalty": float(self.settings.get("repeat_penalty", 1.1)),
            "n_predict": int(max_tokens or self.settings.get("max_tokens", 400)),
            "stream": bool(self.settings.get("stream", True)),
            "ignore_eos": False,
            "stop": list(dict.fromkeys(stops)),
        }
        with self._http().stream("POST", self._completion_url(), json=payload) as response:
            response.raise_for_status()
            if not payload["stream"]:
                result = response.json()
                content = str(result.get("content", ""))
                stopping_word = str(result.get("stopping_word", ""))
                if stopping_word.startswith("<|start_header_id|>"):
                    content += stopping_word
                if content:
                    yield content
                return
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                item = json.loads(data)
                token = item.get("content")
                if token:
                    yield str(token)
                if item.get("stop") is True:
                    stopping_word = str(item.get("stopping_word", ""))
                    if stopping_word.startswith("<|start_header_id|>"):
                        # Preserve the reason for stopping so QalbRouter rejects
                        # this as a fabricated turn instead of accepting a
                        # sentence that happened to precede it.
                        yield stopping_word
                    break

    def _stream_openai(self, messages: Sequence[dict[str, str]], *, max_tokens: int | None) -> Iterator[str]:
        payload = {
            "model": str(self.settings.get("model", "Qalb-1.0-8B-Instruct.Q4_K_M.gguf")),
            "messages": list(messages),
            "temperature": float(self.settings.get("temperature", 0.6)),
            "top_p": float(self.settings.get("top_p", 0.9)),
            "repeat_penalty": float(self.settings.get("repeat_penalty", 1.1)),
            "max_tokens": int(max_tokens or self.settings.get("max_tokens", 400)),
            "stream": bool(self.settings.get("stream", True)),
        }
        stops = self.settings.get("stop")
        if isinstance(stops, list) and stops:
            payload["stop"] = [str(stop) for stop in stops]
        with self._http().stream("POST", self._api_url(), json=payload) as response:
            response.raise_for_status()
            if not payload["stream"]:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if content:
                    yield str(content)
                return
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                item = json.loads(data)
                choices = item.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                token = delta.get("content")
                if token:
                    yield str(token)

    def _stream_ollama(self, messages: Sequence[dict[str, str]], *, max_tokens: int | None) -> Iterator[str]:
        base = str(self.settings.get("base_url", "http://127.0.0.1:11434/v1")).rstrip("/")
        parsed = urlparse(base)
        root = f"{parsed.scheme}://{parsed.netloc}"
        payload = {
            "model": str(self.settings.get("ollama_model", self.settings.get("model", "qalb"))),
            "messages": list(messages),
            "stream": True,
            "options": {
                "temperature": float(self.settings.get("temperature", 0.6)),
                "top_p": float(self.settings.get("top_p", 0.9)),
                "repeat_penalty": float(self.settings.get("repeat_penalty", 1.1)),
                "num_predict": int(max_tokens or self.settings.get("max_tokens", 400)),
            },
        }
        stops = self.settings.get("stop")
        if isinstance(stops, list) and stops:
            payload["options"]["stop"] = [str(stop) for stop in stops]
        with self._http().stream("POST", f"{root}/api/chat", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                item = json.loads(line)
                token = (item.get("message") or {}).get("content")
                if token:
                    yield str(token)


class QalbRouter:
    def __init__(self, config: VoiceConfig | None = None, client: QalbClient | None = None):
        self.config = config or load_config()
        self.settings = self.config.section("brain").get("qalb", {})
        self.client = client or QalbClient(self.config)
        self.history: list[dict[str, str]] = []

    @property
    def mode(self) -> str:
        return str(self.settings.get("mode", "hybrid"))

    def startup(self) -> bool:
        if not self.settings.get("enabled", True):
            return False
        if self.client.health_check():
            return self.client.warmup()
        started = self.client.start_server()
        if self.client.health_check():
            return self.client.warmup()
        if self.settings.get("auto_start", False) and not started:
            LOG.warning("Qalb auto-start did not launch llama-server; Urdu will use the existing brain fallback")
            return False
        if not self.client.ready_to_start and not self.settings.get("auto_start", False):
            LOG.warning("Qalb pending: configure brain.qalb.model_path after placing your own GGUF file")
            return False
        deadline = time.monotonic() + min(20.0, float(self.settings.get("timeout_s", 30)))
        while time.monotonic() < deadline:
            if self.client.health_check():
                return self.client.warmup()
            time.sleep(0.25)
        if not self.client.ready_to_start:
            LOG.warning("Qalb pending: configure brain.qalb.model_path after placing your own GGUF file")
        else:
            LOG.warning("Qalb server is not healthy; Urdu will use the existing brain fallback")
        return False

    def route_urdu(
        self,
        user_text: str,
        *,
        brain_result: str | None = None,
        history: Sequence[dict[str, str]] | None = None,
        on_sentence: Callable[[str], None] | None = None,
        on_reset: Callable[[], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Return direct Qalb Urdu, or rewrite the existing brain result in Urdu.

        The caller owns tool/action execution. Direct mode deliberately receives
        only conversation text and never has a tool interface.
        """
        history = self._trim_history(list(history) if history is not None else self.history)
        while history and history[-1].get("role") == "user" and history[-1].get("content") == user_text:
            history.pop()
        configured_system_prompt = str(self.settings.get("system_prompt", "")).strip()
        # Qalb's model card uses this Urdu system prompt. Keep the configured
        # JARVIS persona after it, while presenting Qalb's native instruction
        # first so the model follows its trained Urdu response pattern.
        system_prompt = "\n\n".join(
            part for part in (_QALB_MODEL_SYSTEM_PROMPT, configured_system_prompt) if part
        )
        if self.mode == "hybrid":
            if brain_result is None:
                raise ValueError("hybrid Urdu mode requires the existing brain result")
            history = [
                *history,
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": brain_result},
                {"role": "user", "content": "Rewrite your preceding answer in concise Urdu script. Preserve facts and outcome; do not add facts or take actions."},
            ]
            instruction = "Rewrite the existing brain result in concise, natural Urdu script. Preserve facts and outcome. Do not add facts or take actions.\n\n" + brain_result
        else:
            history = [*history, {"role": "user", "content": user_text}]
            instruction = user_text
        history = self._trim_history(history)
        messages = [{"role": "system", "content": system_prompt}, *history]
        return self._generate_validated(
            messages,
            instruction,
            remember_user=user_text,
            on_sentence=on_sentence,
            on_reset=on_reset,
            cancel_event=cancel_event,
        )

    def _generate_validated(
        self,
        messages: list[dict[str, str]],
        task_instruction: str,
        *,
        remember_user: str,
        on_sentence: Callable[[str], None] | None = None,
        on_reset: Callable[[], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if not self.client.health_check():
            raise RuntimeError("Qalb server is unavailable")
        last_error: Exception | None = None
        for attempt in range(2):
            current = list(messages)
            if attempt:
                current[0] = {
                    "role": "system",
                    "content": str(current[0].get("content", ""))
                    + "\n\n"
                    + _QALB_RETRY_INSTRUCTION,
                }
                if self.mode == "hybrid":
                    current[-1] = {"role": "user", "content": "اس نتیجے کو صرف اردو رسم الخط میں دوبارہ لکھیں: " + task_instruction}
            try:
                chunks: list[str] = []
                sentence_buffer = ""
                emitted_sentences: list[str] = []
                for token in self.client.stream_chat(current, max_tokens=int(self.settings.get("max_tokens", 400))):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("Qalb generation cancelled by voice barge-in")
                    token = str(token)
                    chunks.append(token)
                    sentence_buffer += token
                    if on_sentence is None:
                        continue
                    while True:
                        match = re.search(r"[.!?۔؟](?:[\"'’)]*)\s*", sentence_buffer)
                        if not match:
                            break
                        raw_sentence = sentence_buffer[:match.end()].strip()
                        sentence_buffer = sentence_buffer[match.end():]
                        if not raw_sentence:
                            continue
                        valid_prefix, prefix_reason = validate_urdu_output(" ".join((*emitted_sentences, raw_sentence)))
                        if not valid_prefix:
                            raise ValueError(prefix_reason)
                        sentence = strip_chat_template_tokens(raw_sentence)
                        if not sentence:
                            continue
                        emitted_sentences.append(sentence)
                        on_sentence(sentence)
                raw = "".join(chunks)
            except Exception as exc:
                if isinstance(exc, InterruptedError):
                    raise
                last_error = exc
                LOG.warning("Qalb request %s failed: %s", attempt + 1, type(exc).__name__)
                if on_reset is not None and 'emitted_sentences' in locals() and emitted_sentences:
                    on_reset()
                continue
            valid, reason = validate_urdu_output(raw)
            text = strip_chat_template_tokens(raw)
            if valid:
                if on_sentence is not None:
                    remaining = strip_chat_template_tokens(sentence_buffer).strip()
                    if remaining:
                        valid_prefix, prefix_reason = validate_urdu_output(" ".join((*emitted_sentences, remaining)))
                        if not valid_prefix:
                            last_error = ValueError(prefix_reason)
                            if on_reset is not None and emitted_sentences:
                                on_reset()
                            LOG.warning("Qalb output rejected (%s); regenerating once", prefix_reason)
                            continue
                        on_sentence(remaining)
                self._remember(user_text=remember_user, assistant_text=text)
                return text
            last_error = ValueError(reason)
            LOG.warning("Qalb output rejected (%s); regenerating once", reason)
            if on_reset is not None and emitted_sentences:
                on_reset()
        if last_error is not None:
            raise RuntimeError(f"Qalb failed validation or request twice: {last_error}") from last_error
        raise RuntimeError("Qalb returned no output")

    def _trim_history(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        limit = max(512, int(self.settings.get("context_tokens", 8192)))
        prompt = str(self.settings.get("system_prompt", ""))
        available_chars = max(512, (limit - int(self.settings.get("max_tokens", 400)) - 128) * 3)
        result: list[dict[str, str]] = []
        used = len(prompt)
        for item in reversed(history):
            content = str(item.get("content", ""))
            cost = len(content) + 32
            if used + cost > available_chars:
                break
            result.append({"role": item.get("role", "user"), "content": content})
            used += cost
        return list(reversed(result))

    def _remember(self, *, user_text: str, assistant_text: str) -> None:
        self.history.extend([
            {"role": "user", "content": str(user_text)},
            {"role": "assistant", "content": str(assistant_text)},
        ])
        self.history = self._trim_history(self.history)


def detect_voice_language(language: str = "", text: str = "", hint: str = "") -> str:
    """Normalize speech detections to the only supported routes: en or ur.

    Whisper/ASR may classify spoken Urdu as Hindi (hi); this maps it to the
    Urdu route so output remains Urdu script.
    """
    detected = str(language or "").strip().lower()
    if detected in {"ur", "urdu", "hi", "hindi"}:
        return "ur"
    if detected in {"en", "english"}:
        return "en"
    hint_value = str(hint or "").strip().lower()
    if hint_value in {"ur", "urdu", "urdu_script", "roman_urdu_or_hinglish", "hi", "hindi", "hindi_script"}:
        return "ur"
    value = str(text or "")
    if any("\u0600" <= char <= "\u06ff" or "\u0900" <= char <= "\u097f" for char in value):
        return "ur"
    roman_urdu = {"mujhe", "muje", "aap", "apko", "tumhe", "meri", "mere", "yaad", "yad", "bhool", "bhul", "kya", "kia", "hai", "nahi", "nhi", "karo", "krdo", "karna", "kyun"}
    return "ur" if roman_urdu.intersection(value.casefold().split()) else "en"


class BrainRouter:
    """Keeps English on the main brain and adapts Urdu via configured Qalb."""

    def __init__(self, qalb: QalbRouter | None = None):
        self.qalb = qalb or QalbRouter()

    def answer(
        self,
        user_text: str,
        lang: str,
        main_brain,
        *,
        history: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        language = detect_voice_language(lang, user_text)
        if language == "en":
            return {"text": str(main_brain(user_text, "en")), "lang": "en", "provider": "main_brain"}
        if self.qalb.mode == "direct":
            try:
                reply = self.qalb.route_urdu(user_text, history=history)
                return {"text": reply, "lang": "ur", "provider": "qalb"}
            except Exception as exc:
                LOG.warning("Qalb direct mode failed; falling back to the main brain: %s", type(exc).__name__)
                return {"text": str(main_brain(user_text, "ur")), "lang": "ur", "provider": "main_brain_fallback"}
        brain_result = str(main_brain(user_text, "ur"))
        try:
            reply = self.qalb.route_urdu(user_text, brain_result=brain_result, history=history)
            return {"text": reply, "lang": "ur", "provider": "qalb"}
        except Exception as exc:
            LOG.warning("Qalb hybrid mode failed; retaining main-brain Urdu response: %s", type(exc).__name__)
            return {"text": brain_result, "lang": "ur", "provider": "main_brain_fallback"}
