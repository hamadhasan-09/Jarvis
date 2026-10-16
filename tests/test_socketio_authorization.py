from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _isolated_server(isolated_auth_server):
    global server
    server = isolated_auth_server


def _payloads(client, event_name: str):
    payloads = []
    for item in client.get_received():
        if item.get("name") != event_name:
            continue
        args = item.get("args") or []
        if args:
            payloads.append(args[0])
    return payloads


def _wait_for_payload(client, event_name: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payloads = _payloads(client, event_name)
        if payloads:
            return payloads[0]
        time.sleep(0.01)
    return None


def _authenticated_browser():
    http_client = server.app.test_client()
    token = server.browser_auth.issue_owner_pairing_token()
    response = http_client.post("/auth/pair", json={"token": token},
                                headers={"X-Nexus-Browser-Auth": "1"})
    assert response.status_code == 200
    socket_client = server.socketio.test_client(
        server.app,
        auth=None,
        flask_test_client=http_client,
    )
    return http_client, socket_client


def test_unauthenticated_browser_cannot_read_or_delete_memory(monkeypatch):
    browser = server.socketio.test_client(server.app, auth=None)
    original_get_stats = server.memory.get_stats
    original_find_facts = server.memory.find_facts
    monkeypatch.setattr(server.memory, "get_stats", lambda: (_ for _ in ()).throw(AssertionError("memory read was authorized")))
    monkeypatch.setattr(server.memory, "find_facts", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("memory delete was authorized")))
    try:
        _payloads(browser, "system_status")
        browser.emit("get_memory_stats")
        assert _wait_for_payload(browser, "memory_stats") == {
            "authorized": False,
            "error": "forbidden",
        }

        browser.emit("forget_memory", {"query": "my name", "confirm": True})
        assert _wait_for_payload(browser, "memory_action") == {
            "success": False,
            "error": "forbidden",
            "message": "A paired browser or authenticated local voice connection is required for memory management.",
        }
    finally:
        server.memory.get_stats = original_get_stats
        server.memory.find_facts = original_find_facts
        browser.disconnect()


def test_unauthenticated_system_status_redacts_memory_statistics(monkeypatch):
    monkeypatch.setattr(
        server.memory,
        "get_stats",
        lambda: (_ for _ in ()).throw(AssertionError("unauthenticated system status read memory")),
    )
    browser = server.socketio.test_client(server.app, auth=None)
    try:
        status = next(
            item["args"][0]
            for item in browser.get_received()
            if item.get("name") == "system_status"
        )
        assert status["memory"] == {"authorized": False}
        assert not {"messages", "facts", "sessions", "pending_tasks"} & set(status["memory"])
    finally:
        browser.disconnect()


def test_authenticated_browser_can_use_memory_handlers(monkeypatch):
    monkeypatch.setattr(
        server.memory,
        "get_stats",
        lambda: {"messages": 2, "facts": 1, "sessions": 1, "pending_tasks": 0},
    )
    monkeypatch.setattr(server.memory, "find_facts", lambda *args, **kwargs: [{"fact": "Hammad"}])
    monkeypatch.setattr(server.memory, "forget_fact", lambda *args, **kwargs: 1)
    _, browser = _authenticated_browser()
    try:
        status = next(
            item["args"][0]
            for item in browser.get_received()
            if item.get("name") == "system_status"
        )
        assert status["memory"] == {"messages": 2, "facts": 1, "sessions": 1, "pending_tasks": 0}
        with server._connection_lock:
            assert any(
                role == "browser" and server._connection_privileged.get(sid)
                for sid, role in server._connection_roles.items()
            )

        browser.emit("get_memory_stats")
        assert _wait_for_payload(browser, "memory_stats") == {
            "messages": 2,
            "facts": 1,
            "sessions": 1,
            "pending_tasks": 0,
        }
        browser.emit("forget_memory", {"query": "name", "confirm": True})
        assert _wait_for_payload(browser, "memory_action") == {
            "success": True,
            "removed": 1,
            "message": "Forgot 1 matching fact(s).",
        }
    finally:
        browser.disconnect()


def test_unauthenticated_browser_does_not_receive_local_private_replies(monkeypatch):
    monkeypatch.setattr(server, "VOICE_LOCAL_TOKEN", "local-secret")
    browser = server.socketio.test_client(server.app, auth=None)
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "private-route", "token": "local-secret"},
    )
    try:
        with server._connection_lock:
            local_sid = next(sid for sid, role in server._connection_roles.items() if role == "local_voice")
        _payloads(browser, "system_status")
        _payloads(local, "system_status")
        server._route_event(
            "jarvis_reply",
            {"text": "private memory reply", "request_id": "private-route"},
            local_sid,
            local_voice=True,
        )
        assert _payloads(browser, "jarvis_reply") == []
    finally:
        local.disconnect()
        browser.disconnect()


@pytest.mark.parametrize("text", [
    "hello there", "what do you remember about me", "forget my name",
    "confirm forget", "create a file named audit.txt",
])
def test_unauthenticated_speech_is_presentation_only(text):
    browser = server.socketio.test_client(server.app, auth=None)
    try:
        browser.emit("user_speech", {"text": text, "request_id": "guest"})
        assert _payloads(browser, "authorization_error") == [{"error": "pairing_required"}]
        server.provider_manager.stream_generate.assert_not_called()
        server.provider_manager.generate.assert_not_called()
        server.provider_manager.generate_ensemble.assert_not_called()
        server.memory.save_message.assert_not_called()
        server.memory.find_facts.assert_not_called()
        server.engine_orchestrator.receive.assert_not_called()
        server.prompt_defense.analyze.assert_not_called()
        server.request_coordinator.session.assert_not_called()
    finally:
        browser.disconnect()


def test_valid_local_voice_token_can_use_memory_handlers(monkeypatch):
    monkeypatch.setattr(server, "VOICE_LOCAL_TOKEN", "local-secret")
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "memory-test", "token": "local-secret"},
    )
    original_get_stats = server.memory.get_stats
    original_find_facts = server.memory.find_facts
    original_forget_fact = server.memory.forget_fact
    monkeypatch.setattr(server.memory, "get_stats", lambda: {"messages": 4, "facts": 2})
    monkeypatch.setattr(server.memory, "find_facts", lambda *args, **kwargs: [{"fact": "Hammad"}])
    monkeypatch.setattr(server.memory, "forget_fact", lambda *args, **kwargs: 1)
    try:
        assert local.is_connected()
        _payloads(local, "system_status")
        local.emit("get_memory_stats")
        assert _wait_for_payload(local, "memory_stats") == {"messages": 4, "facts": 2}

        local.emit("forget_memory", {"query": "name", "confirm": True})
        assert _wait_for_payload(local, "memory_action") == {
            "success": True,
            "removed": 1,
            "message": "Forgot 1 matching fact(s).",
        }
    finally:
        server.memory.get_stats = original_get_stats
        server.memory.find_facts = original_find_facts
        server.memory.forget_fact = original_forget_fact
        local.disconnect()


def test_invalid_local_voice_credentials_are_rejected(monkeypatch):
    monkeypatch.setattr(server, "VOICE_LOCAL_TOKEN", "local-secret")
    wrong = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "invalid-test", "token": "wrong"},
    )
    missing = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "missing-test"},
    )
    try:
        assert not wrong.is_connected()
        assert not missing.is_connected()
    finally:
        if wrong.is_connected():
            wrong.disconnect()
        if missing.is_connected():
            missing.disconnect()


def test_local_voice_without_configured_token_keeps_voice_connection_but_has_no_privilege(monkeypatch):
    monkeypatch.setattr(server, "VOICE_LOCAL_TOKEN", "")
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "unprivileged-voice"},
    )
    original_get_stats = server.memory.get_stats
    monkeypatch.setattr(server.memory, "get_stats", lambda: (_ for _ in ()).throw(AssertionError("memory read was authorized")))
    try:
        assert local.is_connected()
        _payloads(local, "system_status")
        local.emit("get_memory_stats")
        assert _wait_for_payload(local, "memory_stats") == {
            "authorized": False,
            "error": "forbidden",
        }
    finally:
        server.memory.get_stats = original_get_stats
        local.disconnect()


def test_disconnect_clears_privilege_and_pending_memory_confirmation(monkeypatch):
    monkeypatch.setattr(server, "VOICE_LOCAL_TOKEN", "local-secret")
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "disconnect-test", "token": "local-secret"},
    )
    with server._connection_lock:
        sid = next(sid for sid, role in server._connection_roles.items() if role == "local_voice")
    with server.pending_forget_lock:
        server.pending_forget[sid] = {"query": "name", "expires": 60}
    try:
        assert server._is_privileged_sid(sid)
        assert sid in server.pending_forget
    finally:
        local.disconnect()

    with server._connection_lock:
        assert sid not in server._connection_roles
        assert sid not in server._connection_privileged
        assert sid not in server._connection_capabilities
        assert sid not in server._connection_sessions
    with server.pending_forget_lock:
        assert sid not in server.pending_forget

    browser = server.socketio.test_client(server.app, auth=None)
    try:
        assert not server._is_privileged_sid(sid)
    finally:
        browser.disconnect()
