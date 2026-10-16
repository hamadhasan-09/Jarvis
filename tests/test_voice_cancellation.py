from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.provider_manager import ProviderManager
from voice.audio_output import AudioOutput
from voice.config import load_config
from voice.service import VoiceService, VoiceState


@pytest.fixture(autouse=True)
def isolated_server(isolated_auth_server):
    global server
    server = isolated_auth_server


def _authorized_sid(role="browser"):
    from unittest.mock import Mock
    # These legacy tests replace memory with write-only spies; keep the genuine
    # paired/token-authenticated handshake independent of that partial spy.
    memory = server.memory
    server.memory = Mock()
    server.memory.get_stats.return_value = {"messages": 0, "facts": 0, "sessions": 0}
    try:
        if role == "local_voice":
            server.VOICE_LOCAL_TOKEN = "synthetic-legacy-voice-token"
            client = server.socketio.test_client(server.app, auth={"role": role, "token": server.VOICE_LOCAL_TOKEN})
        else:
            http = server.app.test_client()
            token = server.browser_auth.issue_owner_pairing_token()
            assert http.post("/auth/pair", json={"token": token}, headers={"X-Nexus-Browser-Auth": "1"}).status_code == 200
            client = server.socketio.test_client(server.app, flask_test_client=http)
        assert client.is_connected()
        return server.socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")
    finally:
        server.memory = memory


def _control_identity(sid):
    turn = server.begin_turn(server._connection_control_scopes[sid])
    return {"control_scope": server._connection_control_scopes[sid],
            "turn_id": turn, "control_turn_id": turn,
            "session_id": server._connection_sessions[sid], "memory_write": True}


def test_browser_voice_path_emits_existing_cancel_event():
    conversation = Path("ui/hud/conversation.js").read_text(encoding="utf-8")
    voice = Path("ui/hud/voice.js").read_text(encoding="utf-8")
    app = Path("ui/hud/app.js").read_text(encoding="utf-8")
    assert 'socket.emit("voice_cancel"' in conversation
    assert "onInterrupt(heardText, wasSpeaking)" in voice
    assert "conversation.cancelActiveReply(heardText)" in app
    assert 'socket.emit("voice_playback_done"' in conversation
    assert 'this.tts.on("error", (event = {}) => this.cancelActiveReply(event.spokenText || ""))' in conversation


def test_stream_generate_stops_immediately_and_propagates_cancel_event():
    class Provider:
        is_configured = True

        def __init__(self):
            self.observed_cancel = threading.Event()

        def stream(self, *, cancel_event, **_kwargs):
            yield "partial"
            while not cancel_event.wait(0.01):
                yield "next"
            self.observed_cancel.set()

    class Manager(ProviderManager):
        def __init__(self, provider):
            self.provider = provider
            self.timeout = 4
            self.secret_detector = SimpleNamespace(redact=lambda text: text)

        def _get_provider(self, *_args):
            return self.provider

    provider = Provider()
    manager = Manager(provider)
    cancel_event = threading.Event()
    from llm.task_router import RouteDecision
    stream = manager.stream_generate(
        RouteDecision("fake", "fake-model", None, None, False, "test"),
        [{"role": "user", "content": "hello"}],
        timeout=3, cancel_event=cancel_event,
    )
    assert next(stream) == "partial"
    cancel_event.set()
    started = time.perf_counter()
    try:
        next(stream)
        assert False, "cancelled stream must not yield another chunk"
    except StopIteration:
        pass
    assert time.perf_counter() - started < 0.3
    assert provider.observed_cancel.wait(0.5)


def test_brain_bridge_discards_stale_turn_token_and_reply_and_ends_stream(monkeypatch):
    import queue
    import uuid

    import voice.brain_bridge as brain_bridge
    from core.contracts import begin_turn, end_turn

    class FakeSocket:
        def __init__(self, **_kwargs):
            self.handlers = {}

        def on(self, event_name):
            def register(handler):
                self.handlers[event_name] = handler
                return handler
            return register

    monkeypatch.setattr(brain_bridge.socketio, "Client", FakeSocket)
    session_id = f"stale-bridge-{uuid.uuid4().hex}"
    old_turn_id = begin_turn(session_id)
    current_turn_id = begin_turn(session_id)
    bridge = brain_bridge.VoiceBrainBridge(session_id=session_id)
    request_id = "stale-turn-request"
    state = {
        "queue": queue.Queue(),
        "done": threading.Event(),
        "cancelled": threading.Event(),
        "session_id": session_id,
        "turn_id": old_turn_id,
        "buffer": "",
        "sent_prefix": "",
        "submitted_at": time.perf_counter(),
        "first_sentence_logged": False,
    }
    state["queue"].put("queued text from the superseded turn")
    bridge._requests[request_id] = state
    stream = brain_bridge.ReplyStream(bridge, request_id, state)

    try:
        bridge.socket.handlers["jarvis_reply_token"]({
            "request_id": request_id,
            "session_id": session_id,
            "turn_id": old_turn_id,
            "token": "late token sentence. ",
        })
        bridge.socket.handlers["jarvis_reply"]({
            "request_id": request_id,
            "session_id": session_id,
            "turn_id": old_turn_id,
            "text": "fabricated late final reply",
        })

        assert state["cancelled"].is_set()
        assert state["done"].is_set()
        assert state["buffer"] == ""
        assert state["sent_prefix"] == ""
        assert state["queue"].qsize() == 1  # only the end marker remains
        with pytest.raises(StopIteration):
            next(stream)
        assert stream._closed
        assert request_id not in bridge._requests
        assert state["queue"].empty()
    finally:
        end_turn(session_id, current_turn_id)


def test_audio_output_interrupt_aborts_active_playback_and_flushes_queue():
    entered = threading.Event()
    aborted = threading.Event()
    completed = []
    writes = []

    class Stream:
        latency = 0.0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, samples):
            writes.append(samples.copy())
            entered.set()
            aborted.wait(1.0)

        def abort(self):
            aborted.set()

        def stop(self):
            aborted.set()

    config = load_config()
    output = AudioOutput(config, stream_factory=lambda _rate: Stream())
    output.start()
    generation = output.generation
    assert output.enqueue([0.1] * 2000, 24000, generation, on_complete=lambda ok: completed.append(("first", ok)))
    assert entered.wait(1.0)
    assert output.enqueue([0.2] * 2000, 24000, generation, on_complete=lambda ok: completed.append(("queued", ok)))
    output.interrupt()
    assert output.wait_empty(timeout=1.0)
    output.close()
    assert aborted.is_set()
    assert len(writes) == 1
    assert ("first", False) in completed
    assert ("queued", False) in completed


def test_voice_speaking_state_waits_for_audio_playback_start():
    written = threading.Event()

    class Stream:
        latency = 0.0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _samples):
            written.set()

    config = load_config()
    output = AudioOutput(config, stream_factory=lambda _rate: Stream())
    service = VoiceService(
        config,
        audio_input=object(),
        vad=object(),
        wakeword=object(),
        stt=object(),
        tts=object(),
        audio_output=output,
    )
    try:
        service._enqueue_speech("The core begins speaking.", "en", output.generation, add_end=False)
        assert service.current_state == VoiceState.IDLE
        output.start()
        assert output.enqueue([0.1] * 400, 24000, output.generation)
        assert written.wait(1.0)
        assert service.current_state == VoiceState.SPEAKING
    finally:
        output.close()


@pytest.mark.parametrize(
    ("heard_text", "expected_text"),
    [("", ""), ("This part was", "This part was"), ("Fabricated text", "")],
)
def test_browser_cancel_event_reaches_active_backend_generation_and_validates_reported_prefix(monkeypatch, heard_text, expected_text):

    class Memory:
        def __init__(self):
            self.saved = []
            self.secret_detector = SimpleNamespace(redact=lambda text: text)

        def get_stats(self):
            return {"messages": 0, "facts": 0, "sessions": 0}

        def get_context(self, **_kwargs):
            return ""

        def get_recent(self, **_kwargs):
            return []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)
            return True

        def remember_from_text(self, *_args, **_kwargs):
            return None

        def find_facts(self, *_args, **_kwargs):
            return []

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    monkeypatch.setattr(server, "knowledge", None)
    monkeypatch.setattr(server, "web_research", None)
    monkeypatch.setattr(server, "KNOWLEDGE_ENABLED", False)
    monkeypatch.setattr(server, "WEB_ENABLED", False)
    monkeypatch.setattr(server, "ENSEMBLE_ENABLED", False)
    monkeypatch.setattr(server, "COORDINATOR_ENABLED", False)
    monkeypatch.setattr(server.task_router, "verification_enabled", False)
    monkeypatch.setattr(server.job_runtime, "pending_announcements", lambda: [])
    started = threading.Event()
    observed_cancel = threading.Event()

    def stream_generate(_route, _messages, **kwargs):
        cancel_event = kwargs["cancel_event"]
        started.set()
        yield "This part was never spoken."
        cancel_event.wait(2.0)
        if cancel_event.is_set():
            observed_cancel.set()
            return
        yield {"__done__": True, "provider": "fake", "model": "fake-model", "full_text": "This part was never spoken."}

    monkeypatch.setattr(server.provider_manager, "stream_generate", stream_generate)
    browser_http = server.app.test_client()
    token = server.browser_auth.issue_owner_pairing_token()
    assert browser_http.post("/auth/pair", json={"token": token}, headers={"X-Nexus-Browser-Auth": "1"}).status_code == 200
    client = server.socketio.test_client(server.app, flask_test_client=browser_http)
    assert client.is_connected()
    initial_events = client.get_received()
    assert any(event["name"] == "voice_state" for event in initial_events)
    browser_sid = next(sid for sid, role in server._connection_roles.items() if role == "browser")
    request_worker = threading.Thread(target=lambda: client.emit("user_speech", {"text": "Explain gravity", "request_id": "browser-cancel-1"}))
    request_worker.start()
    try:
        assert started.wait(2.0)
        monkeypatch.setattr(server, "request", SimpleNamespace(sid=browser_sid))
        server.on_voice_cancel({"request_id": "browser-cancel-1", "heard_text": heard_text})
        request_worker.join(2.0)
        assert not request_worker.is_alive()
        assert observed_cancel.wait(0.5)
        assistant_saves = [row for row in memory.saved if row[1] == "assistant"]
        assert [row[2] for row in assistant_saves] == ([expected_text] if expected_text else [])
        replies = [item["args"][0] for item in client.get_received() if item["name"] == "jarvis_reply"]
        assert replies and replies[-1]["text"] == expected_text
    finally:
        client.disconnect()


def test_finished_browser_reply_cancel_retains_only_reported_spoken_prefix(monkeypatch):

    class Memory:
        def __init__(self):
            self.saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)
            return True

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id = "played-prefix-only"
    sid = _authorized_sid()
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = {
            **_control_identity(sid),
        "sid": sid,
            "cancel_event": threading.Event(),
            "heard_text": "",
            "awaiting_playback": True,
            "generation_done": True,
            "playback_done": False,
            "assistant_text": "This full reply was heard. Extra is unspoken.",
            "memory_metadata": {"request_id": request_id, "provider": "fake"},
        }
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))
    server.on_voice_cancel({"request_id": request_id, "heard_text": "This full reply was heard."})
    assert memory.saved[0][:3] == (server.SESSION_ID, "assistant", "This full reply was heard.")
    with server._voice_request_controls_lock:
        assert (sid, request_id) not in server._voice_request_controls


def _voice_control(sid, *, assistant_text="", generated_text=None, awaiting_playback=True, generation_done=True):
    control = {
        **_control_identity(sid),
        "sid": sid,
        "cancel_event": threading.Event(),
        "heard_text": "",
        "awaiting_playback": awaiting_playback,
        "generation_done": generation_done,
        "playback_done": False,
        "assistant_text": assistant_text,
        "memory_metadata": {"provider": "fake"},
    }
    if generated_text is not None:
        control["generated_text"] = generated_text
    return control


def test_cancel_empty_prefix_is_safe_and_does_not_persist(monkeypatch):

    class Memory:
        saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id, sid = "cancel-empty-prefix", _authorized_sid()
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = _voice_control(sid, assistant_text="A full reply.")
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))

    server.on_voice_cancel({"request_id": request_id, "heard_text": ""})

    assert memory.saved == []


def test_cancel_rejects_nonprefix_even_for_local_voice(monkeypatch):

    class Memory:
        saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id, sid = "cancel-invalid-local-prefix", _authorized_sid("local_voice")
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = _voice_control(
            sid, assistant_text="The generated answer.", awaiting_playback=False
        )
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))

    server.on_voice_cancel({"request_id": request_id, "heard_text": "A fabricated answer."})

    assert memory.saved == []
    with server._voice_request_controls_lock:
        server._voice_request_controls.pop((sid, request_id), None)


def test_cancel_rejects_text_longer_than_generated_reply(monkeypatch):

    class Memory:
        saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id, sid = "cancel-overlong-prefix", _authorized_sid()
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = _voice_control(sid, assistant_text="Short answer.")
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))

    server.on_voice_cancel({"request_id": request_id, "heard_text": "Short answer. Plus fabricated text."})

    assert memory.saved == []


def test_in_progress_cancel_accepts_only_prefix_of_server_stream_buffer(monkeypatch):

    class Memory:
        saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id, sid = "cancel-live-prefix", _authorized_sid("local_voice")
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = _voice_control(
            sid,
            generated_text="The heard part is a prefix; unseen words follow",
            awaiting_playback=False,
            generation_done=False,
        )
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))

    server.on_voice_cancel({"request_id": request_id, "heard_text": "The heard part is a prefix;"})

    with server._voice_request_controls_lock:
        assert server._voice_request_controls[(sid, request_id)]["heard_text"] == "The heard part is a prefix;"
        server._voice_request_controls.pop((sid, request_id), None)


def test_cancelled_turn_does_not_block_next_normal_memory_persistence(monkeypatch):

    class Memory:
        def __init__(self):
            self.saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    cancelled_id, normal_id, sid = "cancel-then-normal", "normal-after-cancel", _authorized_sid()
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, cancelled_id)] = _voice_control(
            sid, assistant_text="Spoken prefix. Unspoken suffix."
        )
        server._voice_request_controls[(sid, normal_id)] = _voice_control(
            sid, assistant_text="The next complete reply."
        )
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))

    server.on_voice_cancel({"request_id": cancelled_id, "heard_text": "Spoken prefix."})
    server.on_voice_playback_done({"request_id": normal_id})

    assert [row[2] for row in memory.saved] == ["Spoken prefix.", "The next complete reply."]


def test_normal_browser_playback_completion_persists_full_reply(monkeypatch):

    class Memory:
        def __init__(self):
            self.saved = []

        def save_message(self, *args, **kwargs):
            self.saved.append(args)
            return True

    memory = Memory()
    monkeypatch.setattr(server, "memory", memory)
    request_id = "normal-playback-complete"
    sid = _authorized_sid()
    with server._voice_request_controls_lock:
        server._voice_request_controls[(sid, request_id)] = {
            **_control_identity(sid),
        "sid": sid,
            "cancel_event": threading.Event(),
            "heard_text": "",
            "awaiting_playback": True,
            "generation_done": True,
            "playback_done": False,
            "assistant_text": "The entire answer was spoken.",
            "memory_metadata": {"request_id": request_id, "provider": "fake"},
        }
    monkeypatch.setattr(server, "request", SimpleNamespace(sid=sid))
    server.on_voice_playback_done({"request_id": request_id})
    assert memory.saved[0][:3] == (server.SESSION_ID, "assistant", "The entire answer was spoken.")
    with server._voice_request_controls_lock:
        assert (sid, request_id) not in server._voice_request_controls
