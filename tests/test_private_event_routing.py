"""Request confidentiality tests; no production services or voice engines run."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from security_v2.browser_auth import SESSION_KEY


PRIVATE_EVENTS = ("status", "jarvis_reply", "jarvis_reply_token", "request_progress",
                  "approval_request", "approval_status")


def received(client, name):
    return [packet["args"][0] for packet in client.get_received() if packet["name"] == name]


def sid(server, client):
    return server.socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")


def local_voice(server, *, token="", session_id="synthetic-voice"):
    return server.socketio.test_client(server.app, auth={
        "role": "local_voice", "session_id": session_id, "token": token,
    })


@pytest.mark.parametrize("event", PRIVATE_EVENTS)
def test_local_request_events_exclude_guests_and_other_voice_clients(isolated_auth_server, paired_browser, event):
    s = isolated_auth_server
    _, paired = paired_browser
    guest = s.socketio.test_client(s.app)
    tokenless = local_voice(s)
    s.VOICE_LOCAL_TOKEN = "synthetic-voice-token"
    owner = local_voice(s, token=s.VOICE_LOCAL_TOKEN, session_id="owner")
    other_voice = local_voice(s, token=s.VOICE_LOCAL_TOKEN, session_id="other")
    clients = (paired, guest, tokenless, owner, other_voice)
    for client in clients:
        client.get_received()
    payload = {"state": "waiting_confirmation", "request_id": "private-request",
               "turn_id": "private-turn", "session_id": "private-session",
               "message": "private details", "context": {"objective": "private objective"}}
    s._route_event(event, payload, sid(s, owner), local_voice=True)
    assert received(owner, event) == [payload]
    assert received(paired, event) == ([] if event.startswith("approval") else [payload])
    for client in (guest, tokenless, other_voice):
        assert received(client, event) == []


@pytest.mark.parametrize("event", PRIVATE_EVENTS)
def test_unprivileged_origin_cannot_receive_private_events(isolated_auth_server, event):
    s = isolated_auth_server
    guest = s.socketio.test_client(s.app)
    guest.get_received()
    s._route_event(event, {"request_id": "private"}, sid(s, guest), local_voice=False)
    assert received(guest, event) == []


@pytest.mark.parametrize("reason", ["revoked", "expired"])
@pytest.mark.parametrize("event", ["status", "jarvis_reply", "jarvis_reply_token", "request_progress"])
def test_revalidate_after_presentation_recipient_snapshot(isolated_auth_server, paired_browser, monkeypatch, reason, event):
    s = isolated_auth_server
    http, paired = paired_browser
    paired_sid = sid(s, paired)
    with http.session_transaction() as cookie:
        credential_id = cookie[SESSION_KEY]
    s.VOICE_LOCAL_TOKEN = "synthetic-voice-token"
    owner = local_voice(s, token=s.VOICE_LOCAL_TOKEN)
    owner.get_received()
    select = s._authorized_presentation_sids

    def stale_snapshot():
        targets = select()
        assert paired_sid in targets
        if reason == "revoked":
            s.browser_auth.revoke(credential_id)
        else:
            s.now[0] += 30
            s.browser_auth.expire_and_notify()
        return targets

    monkeypatch.setattr(s, "_authorized_presentation_sids", stale_snapshot)
    deliver = Mock(wraps=s.socketio.emit)
    monkeypatch.setattr(s.socketio, "emit", deliver)
    payload = {"request_id": "private", "message": "private details"}
    s._route_event(event, payload, sid(s, owner), local_voice=True)
    assert not paired.is_connected()
    assert received(owner, event) == [payload]
    assert all(call.kwargs.get("to") != paired_sid for call in deliver.call_args_list)


@pytest.mark.parametrize("state,expected", [("THINKING", "thinking"), ("private request text", "idle"), ({"request_id": "private"}, "idle")])
def test_only_sanitized_global_voice_state_is_public(isolated_auth_server, state, expected):
    s = isolated_auth_server
    guest = s.socketio.test_client(s.app)
    tokenless = local_voice(s)
    for client in (guest, tokenless):
        client.get_received()
    payload = {"state": state, "request_id": "private-request", "message": "private text",
               "session_id": "private-session", "turn_id": "private-turn"}
    s._route_event("voice_state", payload, sid(s, tokenless), local_voice=True)
    assert received(guest, "voice_state") == [{"state": expected}]
    assert received(tokenless, "voice_state") == [{"state": expected}]
    # The real service callback uses the same safe projection.
    s._publish_overlay_voice_state(SimpleNamespace(value=state, request_id="private-request"))
    assert received(guest, "voice_state") == [{"state": expected}]
    assert s._overlay_voice_state == {"state": expected}


def test_connection_voice_state_drops_cached_private_metadata(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, _ = paired_browser
    s._overlay_voice_state.update(state="private text", request_id="private-request", message="private")
    for client in (s.socketio.test_client(s.app), s.socketio.test_client(s.app, flask_test_client=http)):
        assert received(client, "voice_state") == [{"state": "idle"}]


@pytest.mark.parametrize("token", ["synthetic-configured-voice-token", ""])
def test_normal_startup_token_handoff_and_real_bridge_connect(isolated_auth_server, monkeypatch, token):
    from voice import brain_bridge

    s = isolated_auth_server
    s.VOICE_LOCAL_TOKEN = token
    socket = Mock(connected=False)
    monkeypatch.setattr(brain_bridge.socketio, "Client", Mock(return_value=socket))
    # Evaluate the actual constructor call in the normal startup initializer,
    # without running VoiceService, configuration loading, microphone or models.
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ui/server.py").read_text(encoding="utf-8"))
    constructor = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == "VoiceBrainBridge")
    environment = {"VoiceBrainBridge": brain_bridge.VoiceBrainBridge,
                   "JARVIS_HOST": s.JARVIS_HOST, "JARVIS_PORT": s.JARVIS_PORT,
                   "VOICE_ENGINE_SESSION_ID": s.VOICE_ENGINE_SESSION_ID,
                   "VOICE_LOCAL_TOKEN": token}
    bridge = eval(compile(ast.Expression(body=constructor), "ui/server.py", "eval"), environment)
    bridge.connect()
    socket.connect.assert_called_once()
    auth = socket.connect.call_args.kwargs["auth"]
    assert auth == {"role": "local_voice", "session_id": s.VOICE_ENGINE_SESSION_ID,
                    **({"token": token} if token else {})}
    assert socket.connect.call_args.args[0] == f"http://{s.JARVIS_HOST}:{s.JARVIS_PORT}"
    local = s.socketio.test_client(s.app, auth=auth)
    assert local.is_connected()
    assert s._has_capability(sid(s, local), s.CONVERSATION_CAPABILITY) is bool(token)
    assert s._has_capability(sid(s, local), s.MEMORY_READ_CAPABILITY) is bool(token)
    if not token:
        local.get_received()
        local.emit("user_speech", {"text": "hello", "request_id": "tokenless"})
        assert received(local, "authorization_error") == [{"error": "pairing_required"}]
        s.provider_manager.stream_generate.assert_not_called()
        s.memory.save_message.assert_not_called()
        s.engine_orchestrator.receive.assert_not_called()


def test_unset_voice_token_default_is_empty_without_reading_owner_environment():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "ui/server.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "VOICE_LOCAL_TOKEN" for target in node.targets))
    environment = {"os": SimpleNamespace(getenv=lambda _name, default: default)}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), "ui/server.py", "exec"), environment)
    assert environment["VOICE_LOCAL_TOKEN"] == ""
