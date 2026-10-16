"""Parallel, request-scoped coordination using configured ProviderManager models."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
import re
import time
import uuid
from typing import Any

from .artifact_bus import Artifact, ArtifactBus
from .base_provider import ProviderError, ProviderResponse
from .task_router import RouteDecision
from core.contracts import is_turn_current


_FAST_MODEL_MARKERS = re.compile(r"(?:-20b|-8b|-7b|-mini|-flash|-haiku|-instant|-turbo|small)", re.I)
_MAX_PROVIDER_TIMEOUT_SECONDS = 25.0


class Coordinator:
    def __init__(self, provider_manager, artifact_bus, synthesis_model_pref=None):
        self.provider_manager = provider_manager
        self.artifact_bus = artifact_bus
        self.synthesis_model_pref = synthesis_model_pref
        self._latencies: dict[tuple[str, str], float] = {}

    def run(
        self,
        request_id,
        messages,
        temperature=0.65,
        max_tokens=700,
        timeout=25.0,
        metadata=None,
        cancel_event=None,
        on_synthesis_chunk=None,
    ) -> ProviderResponse:
        request_id = str(request_id or uuid.uuid4().hex)
        candidates = self._discover_candidates()
        if not candidates:
            raise ProviderError("coordinator", "no configured models are available")

        try:
            max_candidates = max(1, int(os.getenv("JARVIS_COORDINATOR_MAX_CANDIDATES", "3")))
        except (TypeError, ValueError):
            max_candidates = 3
        larger_candidates = [pair for pair in candidates if not _FAST_MODEL_MARKERS.search(pair[1])]
        if len(larger_candidates) >= max_candidates:
            candidates = larger_candidates
        candidates = candidates[:max_candidates]
        call_timeout = min(_MAX_PROVIDER_TIMEOUT_SECONDS, max(1.0, float(timeout)))
        base_metadata = dict(metadata or {})
        base_metadata["request_id"] = request_id
        turn_id = str(base_metadata.get("turn_id", "") or "")
        session_id = str(base_metadata.get("session_id", "") or "")

        def cancelled_or_stale() -> bool:
            stale = bool(turn_id and session_id and not is_turn_current(session_id, turn_id))
            if stale and cancel_event is not None:
                cancel_event.set()
            return bool(cancel_event is not None and cancel_event.is_set()) or stale

        safe_messages = self.provider_manager._redact_messages(messages)
        responses: list[tuple[str, str, ProviderResponse, int]] = []
        failures: list[dict[str, str]] = []

        def call_model(provider_name, model_name):
            route = RouteDecision(
                primary_provider=provider_name,
                primary_model=model_name,
                fallback_provider=None,
                fallback_model=None,
                verify=False,
                reason="parallel coordinator candidate",
            )
            started = time.monotonic()
            candidate_metadata = {**base_metadata, "coordinator_role": "candidate"}
            if cancel_event is None or not callable(getattr(self.provider_manager, "stream_generate", None)):
                response = self.provider_manager.generate(
                    route, safe_messages, temperature=temperature, max_tokens=max_tokens,
                    timeout=call_timeout, metadata=candidate_metadata,
                )
            else:
                response = self._stream_cancellable_response(
                    route, safe_messages, temperature=temperature, max_tokens=max_tokens,
                    timeout=call_timeout, metadata=candidate_metadata, cancel_event=cancel_event,
                )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not str(getattr(response, "text", "") or "").strip():
                raise ProviderError(f"{provider_name}/{model_name}", "empty model response")
            return provider_name, model_name, response, elapsed_ms

        pool = ThreadPoolExecutor(max_workers=len(candidates), thread_name_prefix="jarvis-coordinator")
        futures = {
            pool.submit(call_model, provider_name, model_name): (provider_name, model_name)
            for provider_name, model_name in candidates
        }
        pending = set(futures)
        was_cancelled = False
        try:
            while pending:
                if cancelled_or_stale():
                    was_cancelled = True
                    raise InterruptedError("Coordinator cancelled")
                done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    provider_name, model_name = futures[future]
                    try:
                        response_item = future.result()
                        provider_name, model_name, response, elapsed_ms = response_item
                        if cancelled_or_stale():
                            was_cancelled = True
                            raise InterruptedError("Coordinator cancelled")
                        producer = f"{provider_name}/{model_name}"
                        responses.append(response_item)
                        self._latencies[(provider_name, model_name)] = float(elapsed_ms)
                        self.artifact_bus.add(Artifact(
                            id=uuid.uuid4().hex,
                            request_id=request_id,
                            kind="model_response",
                            producer=producer,
                            content=str(response.text).strip(),
                            metadata={"latency_ms": elapsed_ms, **({"turn_id": turn_id} if turn_id else {})},
                        ))
                    except Exception as exc:
                        if isinstance(exc, InterruptedError) and cancel_event is not None and cancel_event.is_set():
                            was_cancelled = True
                            raise
                        failures.append({
                            "provider": provider_name,
                            "model": model_name,
                            "error": self.provider_manager.secret_detector.redact(
                                f"{type(exc).__name__}: {exc}"
                            )[:400],
                        })
            if cancelled_or_stale():
                was_cancelled = True
                raise InterruptedError("Coordinator cancelled")
        finally:
            if was_cancelled:
                if cancel_event is not None:
                    cancel_event.set()
                for future in pending:
                    future.cancel()
            pool.shutdown(wait=not was_cancelled, cancel_futures=was_cancelled)

        if cancelled_or_stale():
            raise InterruptedError("Coordinator cancelled")
        if not responses:
            raise ProviderError("coordinator", "all candidate model calls failed")

        producer_names = [f"{provider}/{model}" for provider, model, _, _ in responses]
        if len(responses) == 1:
            response = responses[0][2]
            response.metadata.update({
                "coordinator_members": 1,
                "coordinator_models": producer_names,
                "coordinator_failures": failures,
            })
            return response

        synth_provider, synth_model = self._select_synthesis_model()
        synthesis_messages = self._build_synthesis_messages(
            safe_messages,
            [response.text for _, _, response, _ in responses],
        )
        synth_route = RouteDecision(
            primary_provider=synth_provider,
            primary_model=synth_model,
            fallback_provider=None,
            fallback_model=None,
            verify=False,
            reason="coordinator synthesis",
        )
        if cancelled_or_stale():
            raise InterruptedError("Coordinator cancelled before synthesis")
        synthesis_metadata = {**base_metadata, "coordinator_role": "synthesis", "coordinator_members": len(responses)}
        if cancel_event is None or not callable(getattr(self.provider_manager, "stream_generate", None)):
            synthesis = self.provider_manager.generate(
                synth_route,
                synthesis_messages,
                temperature=min(float(temperature), 0.4),
                max_tokens=max_tokens,
                timeout=call_timeout,
                metadata=synthesis_metadata,
            )
        else:
            synthesis = self._stream_cancellable_response(
                synth_route, synthesis_messages, temperature=min(float(temperature), 0.4),
                max_tokens=max_tokens, timeout=call_timeout, metadata=synthesis_metadata,
                cancel_event=cancel_event, on_chunk=on_synthesis_chunk,
            )
        if not str(getattr(synthesis, "text", "") or "").strip():
            raise ProviderError("coordinator", "synthesis model returned an empty response")
        if cancelled_or_stale():
            raise InterruptedError("Coordinator cancelled")
        synthesis.metadata.update({
            "coordinator_members": len(responses),
            "coordinator_models": producer_names,
            "coordinator_failures": failures,
        })
        self.artifact_bus.add(Artifact(
            id=uuid.uuid4().hex,
            request_id=request_id,
            kind="synthesis",
            producer=f"{synth_provider}/{synth_model}",
            content=str(synthesis.text).strip(),
            metadata={
                "candidate_count": len(responses),
                "latency_ms": synthesis.metadata.get("latency_ms"),
                **({"turn_id": turn_id} if turn_id else {}),
            },
        ))
        return synthesis

    def _stream_cancellable_response(
        self, route, messages, *, temperature, max_tokens, timeout, metadata, cancel_event,
        on_chunk=None,
    ):
        chunks = []
        terminal = None
        stream = self.provider_manager.stream_generate(
            route, messages, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, metadata=metadata, cancel_event=cancel_event,
        )
        try:
            for item in stream:
                if cancel_event.is_set():
                    raise InterruptedError("Coordinator generation cancelled")
                if isinstance(item, dict):
                    if item.get("error"):
                        raise ProviderError("coordinator", str(item["error"]))
                    if item.get("__done__"):
                        terminal = item
                elif isinstance(item, str):
                    chunks.append(item)
                    if on_chunk is not None:
                        on_chunk(item)
        finally:
            closer = getattr(stream, "close", None)
            if callable(closer):
                closer()
        if cancel_event.is_set():
            raise InterruptedError("Coordinator generation cancelled")
        text = str((terminal or {}).get("full_text", "")) or "".join(chunks)
        if not text.strip():
            raise ProviderError("coordinator", "provider returned an empty cancellable response")
        return ProviderResponse(
            text=text,
            provider=str((terminal or {}).get("provider", route.primary_provider)),
            model=str((terminal or {}).get("model", route.primary_model)),
            metadata={"request": metadata},
        )

    def _discover_candidates(self) -> list[tuple[str, str]]:
        configured = getattr(self.provider_manager, "providers", {})
        candidates = []
        for key, adapter in configured.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            provider_name, model_name = str(key[0]).strip().lower(), str(key[1]).strip()
            if provider_name and model_name and getattr(adapter, "is_configured", True):
                candidates.append((provider_name, model_name))
        return sorted(set(candidates))

    def _select_synthesis_model(self) -> tuple[str, str]:
        candidates = self._discover_candidates()
        preference = self.synthesis_model_pref or os.getenv("JARVIS_COORDINATOR_SYNTH_MODEL", "").strip()
        if preference:
            if isinstance(preference, tuple) and len(preference) == 2:
                preferred_pair = (str(preference[0]).strip().lower(), str(preference[1]).strip())
                if preferred_pair in candidates:
                    return preferred_pair
            else:
                preferred_model = str(preference).strip()
                matching = [pair for pair in candidates if pair[1] == preferred_model or f"{pair[0]}:{pair[1]}" == preferred_model]
                if matching:
                    return matching[0]

        # First prefer a recognizably smaller/fast model; then use measured
        # latency from earlier requests; finally make a deterministic choice.
        fast = [pair for pair in candidates if _FAST_MODEL_MARKERS.search(pair[1])]
        pool = fast or candidates
        return min(pool, key=lambda pair: (self._latencies.get(pair, float("inf")), pair))

    @staticmethod
    def _build_synthesis_messages(messages: list[dict[str, Any]], candidate_texts: list[str]) -> list[dict[str, Any]]:
        result = [dict(message) for message in messages]
        last_user = next((i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"), None)
        if last_user is None:
            raise ValueError("Coordinator synthesis requires a user message")
        original_question = str(result[last_user].get("content", ""))
        candidates = "\n\n".join(
            f"Candidate {index}:\n{text}" for index, text in enumerate(candidate_texts, start=1)
        )
        result[last_user] = {
            "role": "user",
            "content": (
                f"Original user question:\n{original_question}\n\n{candidates}\n\n"
                "Cross-check the candidates. Explicitly resolve factual disagreements by stating which is correct and why. "
                "Produce ONE best final answer in the same conversational voice as the system prompt: short, natural, and without markdown."
            ),
        }
        return result
