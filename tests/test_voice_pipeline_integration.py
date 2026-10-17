"""Authenticated real Socket.IO transport with synthetic provider/audio edges."""
import logging
import json
import threading
import time
from types import SimpleNamespace

import pytest
import socketio
from werkzeug.serving import make_server, WSGIRequestHandler

from llm.provider_manager import ProviderManager
from llm.task_router import TaskRouter
from voice.brain_bridge import VoiceBrainBridge, VoiceBridgeError
from voice.service import VoiceState
from tests.voice_pipeline_harness import SyntheticProvider, build_pipeline


def until(predicate, timeout=3):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return True
        time.sleep(.01)
    return bool(predicate())


@pytest.fixture
def voice_server(isolated_auth_server, monkeypatch):
    s = isolated_auth_server
    s.app.config["SERVER_NAME"] = None
    s.VOICE_LOCAL_TOKEN = "synthetic-integration-token"
    for key, value in {"JARVIS_PRIMARY_PROVIDER":"synthetic", "JARVIS_PRIMARY_MODEL":"fixture", "JARVIS_FALLBACK_PROVIDER":"", "JARVIS_REASONING_PROVIDER":"synthetic", "JARVIS_REASONING_MODEL":"fixture"}.items():
        monkeypatch.setenv(key, value)
    provider = SyntheticProvider()
    monkeypatch.setattr(ProviderManager, "_build_configured_providers", lambda self: setattr(self, "providers", {("synthetic","fixture"):provider}))
    s.provider_manager = ProviderManager(broker=SimpleNamespace(key_manager=None))
    s.task_router = TaskRouter(s.provider_manager)
    s.task_router.verification_enabled = False
    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs): pass
    http = make_server("127.0.0.1", 0, s.app, threaded=True, request_handler=Quiet)
    s.JARVIS_PORT = http.server_port
    worker = threading.Thread(target=http.serve_forever, daemon=True, name="synthetic-voice-http")
    worker.start()
    url = f"http://127.0.0.1:{http.server_port}"
    bridges = []
    clients = []
    def bridge(token=s.VOICE_LOCAL_TOKEN):
        client = VoiceBrainBridge(url=url, session_id="pipeline-owner", token=token)
        connect = client.socket.connect
        # Polling exercises actual Engine.IO/Socket.IO HTTP without a WS server dependency.
        client.socket.connect = lambda *a, **kw: connect(*a, transports=["polling"], **kw)
        bridges.append(client)
        return client
    yield SimpleNamespace(server=s, provider=provider, bridge=bridge, url=url, clients=clients)
    provider.release.set()
    for client in clients:
        if client.connected: client.disconnect()
    for client in bridges: client.close()
    http.shutdown();http.server_close();worker.join(2)
    assert not worker.is_alive()


def test_recorded_fixture_to_authenticated_brain_and_pcm_playback(voice_server, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    bridge = voice_server.bridge()
    pipeline = build_pipeline(tmp_path, bridge, monkeypatch)
    service = pipeline.service
    try:
        service.start()
        assert until(lambda: len(pipeline.tts_calls)==2 and service._active_reply_stream is None, 5)
        assert pipeline.tts_calls == ["The authenticated brain replied.", "Playback is isolated."]
        assert len(voice_server.provider.calls)==1 and len(pipeline.stt_calls)==1
        assert len(pipeline.speaker.writes)==2
        assert service.current_state == VoiceState.LISTENING
        assert not bridge._requests
        assert voice_server.server.memory.save_message.called
        assert {d["stage"] for d in pipeline.diagnostics} >= {"stt","brain","tts","playback"}
        assert any(d["stage"]=="stt" and "elapsed_ms" in d for d in pipeline.diagnostics)
        assert not service._played_sentences
        (tmp_path/"pipeline-report.json").write_text(json.dumps(pipeline.diagnostics,indent=2))
    finally:
        service.stop();bridge.close()
    assert pipeline.speaker.closed.is_set() and pipeline.audio.closed.is_set()
    assert all(not t.is_alive() for t in [service._capture_thread,service._stt_thread,service._tts_thread])
    assert not pipeline.output._thread.is_alive()


def test_tokenless_voice_rejection_finishes_bridge_without_provider(voice_server):
    voice_server.server.VOICE_LOCAL_TOKEN = ""  # Presentation-only tokenless connection.
    bridge = voice_server.bridge(token="")
    reply = bridge.submit("Explain a greeting", "en")
    try:
        assert reply.state["done"].wait(1), "authorization rejection left bridge waiting"
        assert not voice_server.provider.calls
        with pytest.raises(VoiceBridgeError, match="authorization_required"):
            next(reply)
    finally:
        reply.cancel("");bridge.close()


def test_disconnect_finishes_active_bridge_stream(voice_server):
    voice_server.provider.block=True
    bridge=voice_server.bridge();reply=bridge.submit("Explain a greeting", "en")
    try:
        assert voice_server.provider.started.wait(2)
        bridge.close()
        assert reply.state["done"].wait(1), "disconnect left a reply consumer waiting"
    finally:
        reply.cancel("");voice_server.provider.release.set()


def test_wrong_configured_token_fails_without_provider_or_secret_log(voice_server, caplog):
    bridge=voice_server.bridge(token="synthetic-wrong-secret")
    reply=bridge.submit("Explain a greeting", "en")
    try:
        assert reply.state["done"].wait(2)
        with pytest.raises(VoiceBridgeError, match="transport_unavailable"):
            next(reply)
        assert not voice_server.provider.calls
        assert "synthetic-wrong-secret" not in caplog.text
    finally: bridge.close()


def test_bounded_timeout_cancels_owned_server_generation(voice_server):
    voice_server.provider.block=True
    bridge=voice_server.bridge();bridge.reply_timeout=.1
    reply=bridge.submit("Explain a greeting", "en")
    try:
        assert reply.state["done"].wait(2)
        with pytest.raises(VoiceBridgeError, match="reply_timeout"):
            next(reply)
        assert voice_server.provider.cancelled.wait(2)
        assert not bridge._requests
    finally: bridge.close()


def test_stop_service_cancels_blocked_reply_and_joins_feeder(voice_server, tmp_path, monkeypatch):
    voice_server.provider.block=True
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    try:
        pipeline.service.start()
        assert voice_server.provider.started.wait(2)
        assert until(lambda: bool(pipeline.service._speech_threads))
        pipeline.service.stop()
        assert voice_server.provider.cancelled.wait(2)
        assert not pipeline.service._speech_threads
        assert not pipeline.service._stt_thread.is_alive()
        assert not pipeline.service._tts_thread.is_alive()
    finally:
        voice_server.provider.release.set();bridge.close()


def test_same_client_interruption_drops_late_reply_audio(voice_server, tmp_path, monkeypatch):
    voice_server.provider.block=True
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    try:
        pipeline.service.start()
        assert pipeline.speaker.written.wait(2)
        stream=pipeline.service._active_reply_stream
        identity={"request_id":stream.request_id,"session_id":stream.session_id,"turn_id":stream.turn_id}
        pipeline.service.interrupt()
        assert voice_server.provider.cancelled.wait(2)
        assert until(lambda:not pipeline.service._speech_threads)
        sid=bridge.socket.get_sid()
        voice_server.server.socketio.emit("jarvis_reply_token",dict(identity,token="Stale private reply. "),to=sid)
        voice_server.server.socketio.emit("jarvis_reply",dict(identity,text="Stale private reply."),to=sid)
        voice_server.provider.release.set()
        bridge.close()
        assert pipeline.tts_calls==["The authenticated brain replied."]
        assert not pipeline.service._played_sentences.get("unrelated")
    finally:pipeline.service.stop();bridge.close()


def test_wrong_reply_turn_or_session_is_not_adopted(voice_server, caplog):
    caplog.set_level(logging.WARNING)
    voice_server.provider.block=True
    bridge=voice_server.bridge();reply=bridge.submit("Explain a greeting", "en")
    try:
        assert voice_server.provider.started.wait(2)
        sid=bridge.socket.get_sid()
        before=(reply.session_id,reply.turn_id)
        voice_server.server.socketio.emit("jarvis_reply_token",{
            "request_id":reply.request_id,"session_id":"foreign-session","turn_id":"foreign-turn","token":"Leaked reply. "},to=sid)
        assert until(lambda:"reply_identity_mismatch" in caplog.text)
        assert (reply.session_id,reply.turn_id)==before
        voice_server.provider.release.set()
        assert [item.text for item in reply]==["The authenticated brain replied.","Playback is isolated."]
    finally:bridge.close()


def test_tts_failure_has_stage_code_and_no_playback(voice_server,tmp_path,monkeypatch):
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    diagnostics=[];pipeline.service.on_diagnostic(diagnostics.append)
    def fail(*args):raise ValueError("synthetic-sensitive-error")
    monkeypatch.setattr(pipeline.service.tts,"synthesize",fail)
    try:
        pipeline.service.start()
        assert until(lambda:any(d["stage"]=="tts" and d["state"]=="failed" for d in diagnostics))
        assert until(lambda:pipeline.service._active_reply_stream is None)
        assert not pipeline.speaker.writes
        assert all("synthetic-sensitive-error" not in str(d) for d in diagnostics)
    finally:pipeline.service.stop();bridge.close()
    assert diagnostics[-1]["stage"]=="shutdown" and diagnostics[-1]["state"]=="complete"


def test_output_failure_reports_playback_and_releases_stream(voice_server,tmp_path,monkeypatch):
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    pipeline.speaker.fail=True
    diagnostics=[];pipeline.service.on_diagnostic(diagnostics.append)
    try:
        pipeline.service.start()
        assert until(lambda:any(d["stage"]=="playback" and d["state"]=="failed" for d in diagnostics))
    finally:pipeline.service.stop();bridge.close()
    assert pipeline.speaker.closed.is_set()


def test_authorized_browser_revocation_blocks_mirrored_voice_events(voice_server):
    import requests
    s=voice_server.server
    http=requests.Session()
    token=s.browser_auth.issue_owner_pairing_token()
    response=http.post(voice_server.url+"/auth/pair",json={"token":token},headers={"X-Nexus-Browser-Auth":"1"},timeout=2)
    assert response.status_code==200
    browser=socketio.Client(http_session=http,reconnection=False)
    guest=socketio.Client(reconnection=False)
    voice_server.clients.extend([browser,guest])
    private=[];guest_private=[]
    browser.on("jarvis_reply_token",private.append)
    guest.on("jarvis_reply_token",guest_private.append)
    browser.connect(voice_server.url,transports=["polling"])
    guest.connect(voice_server.url,transports=["polling"])
    voice_server.provider.block=True
    bridge=voice_server.bridge();reply=bridge.submit("Explain a greeting","en")
    try:
        assert until(lambda:len(private)==1)
        guest.emit("voice_cancel",{"request_id":reply.request_id,"turn_id":reply.turn_id})
        guest.emit("user_speech",{"text":"Explain a secret","source":"local_voice","role":"local_voice"})
        with s._voice_request_controls_lock:
            control=s._voice_request_controls[(bridge.socket.get_sid(),reply.request_id)]
            assert not control["cancel_event"].is_set()
        # Revocation uses the genuine registered browser credential and invalidation callback.
        credential=s._connection_browser_credentials[browser.get_sid()]
        s.browser_auth.revoke(credential)
        voice_server.provider.release.set()
        assert [item.text for item in reply]==["The authenticated brain replied.","Playback is isolated."]
        assert len(private)==1 and not guest_private
        assert len(voice_server.provider.calls)==1
    finally:
        bridge.close();http.close()


def test_closed_bridge_rejects_new_work_without_reconnect(voice_server):
    bridge=voice_server.bridge();bridge.close()
    with pytest.raises(VoiceBridgeError,match="bridge_closed"):
        bridge.submit("should not run","en")
    assert not voice_server.provider.calls


def test_authoritative_provider_failure_replaces_partial_stream(voice_server):
    voice_server.provider.fail=True
    bridge=voice_server.bridge()
    try:
        parts=list(bridge.submit("Explain a greeting","en"))
        assert any(isinstance(item,dict) and item.get("reset") for item in parts)
        assert parts[-1].text=="I am online, but the configured language provider is unavailable right now, Sir."
    finally:bridge.close()


def test_stt_failure_never_reaches_provider_and_reports_stage(voice_server,tmp_path,monkeypatch):
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    def fail(*a,**kw):raise RuntimeError("synthetic-sensitive-error")
    monkeypatch.setattr(pipeline.service.stt,"transcribe",fail)
    try:
        pipeline.service.start()
        assert until(lambda:any(d["stage"]=="stt" and d["state"]=="failed" for d in pipeline.diagnostics))
        assert not voice_server.provider.calls
        assert "synthetic-sensitive-error" not in str(pipeline.diagnostics)
    finally:pipeline.service.stop();bridge.close()


def test_transcription_only_shutdown_releases_blocked_consumer(voice_server,tmp_path,monkeypatch):
    voice_server.provider.block=True
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    pipeline.service._tts_enabled=False;pipeline.service.tts=None
    try:
        pipeline.service.start()
        assert voice_server.provider.started.wait(2)
        assert until(lambda:pipeline.service._active_reply_stream is not None)
        pipeline.service.stop()
        assert voice_server.provider.cancelled.wait(2)
        assert not pipeline.service._stt_thread.is_alive()
        assert not pipeline.speaker.writes
    finally:voice_server.provider.release.set();bridge.close()


def test_stale_end_marker_cannot_reset_new_turn_state(voice_server,tmp_path,monkeypatch):
    bridge=voice_server.bridge();pipeline=build_pipeline(tmp_path,bridge,monkeypatch)
    service=pipeline.service
    service._running.set()
    generation=service.audio_output.interrupt()
    service._set_state(VoiceState.THINKING)
    service._queue_end(generation-1)
    service._tts_queue.put(None)
    service._tts_loop()
    assert service.current_state==VoiceState.THINKING
    service._running.clear();bridge.close()


def test_temporary_voice_configuration_is_explicit_and_does_not_change_default(tmp_path,monkeypatch):
    import yaml
    from voice.config import load_config,VoiceConfigError
    monkeypatch.delenv("NEXUS_VOICE_CONFIG",raising=False)
    original=load_config()
    values=json.loads(json.dumps(original.values))
    values["stt"]["model"]="base"
    selected=tmp_path/"temporary-voice.yaml"
    selected.write_text(yaml.safe_dump(values),encoding="utf-8")
    monkeypatch.setenv("NEXUS_VOICE_CONFIG",str(selected))
    assert load_config().section("stt")["model"]=="base"
    assert load_config(original.path).section("stt")["model"]=="large-v3"
    monkeypatch.setenv("NEXUS_VOICE_CONFIG",str(tmp_path/"missing.yaml"))
    with pytest.raises(VoiceConfigError,match="not found"):load_config()
    monkeypatch.delenv("NEXUS_VOICE_CONFIG")
    assert load_config().section("stt")["model"]=="large-v3"
