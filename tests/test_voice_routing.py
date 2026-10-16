"""Focused contract tests for authenticated local voice routing.

Run from the Nexus project root:
    venv\\Scripts\\python.exe tests\\test_voice_routing.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(autouse=True)
def isolated_server(isolated_auth_server):
    global server
    server = isolated_auth_server
    server.VOICE_LOCAL_TOKEN = "synthetic-legacy-voice-token"


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
    found = []
    while time.monotonic() < deadline:
        found.extend(_payloads(client, event_name))
        if found:
            return found[0]
        time.sleep(0.01)
    return None


def _registered_sid(role: str) -> str:
    with server._connection_lock:
        matches = [sid for sid, value in server._connection_roles.items() if value == role]
    assert len(matches) == 1, matches
    return matches[0]


def _authenticated_browser():
    http_client = server.app.test_client()
    token = server.browser_auth.issue_owner_pairing_token()
    assert http_client.post("/auth/pair", json={"token": token}, headers={"X-Nexus-Browser-Auth": "1"}).status_code == 200
    return server.socketio.test_client(
        server.app,
        auth=None,
        flask_test_client=http_client,
    )


def test_local_reply_routes_to_owner_and_browser_presentation_only():
    browser = _authenticated_browser()
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "routing-test", "token": server.VOICE_LOCAL_TOKEN},
    )
    try:
        _payloads(browser, "system_status")
        _payloads(local, "system_status")
        local_sid = _registered_sid("local_voice")
        browser_sid = _registered_sid("browser")
        assert server._is_local_voice_sid(local_sid)
        assert not server._is_local_voice_sid(browser_sid)

        server._route_event(
            "jarvis_reply",
            {"request_id": "routing-request", "text": "targeted"},
            local_sid,
            local_voice=True,
        )
        local_replies = _payloads(local, "jarvis_reply")
        browser_replies = _payloads(browser, "jarvis_reply")
        assert local_replies == [{"request_id": "routing-request", "text": "targeted"}]
        assert browser_replies == [{"request_id": "routing-request", "text": "targeted"}]

        # A browser client cannot become local voice by claiming source in data;
        # only the authenticated connection role is authoritative.
        server._route_event(
            "jarvis_reply",
            {"request_id": "browser-request", "text": "browser-only"},
            browser_sid,
            local_voice=False,
        )
        assert _payloads(local, "jarvis_reply") == []
        assert _payloads(browser, "jarvis_reply") == [
            {"request_id": "browser-request", "text": "browser-only"}
        ]
    finally:
        local.disconnect()
        browser.disconnect()


def test_local_user_speech_round_trip_preserves_correlation():
    browser = _authenticated_browser()
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "roundtrip-test", "token": server.VOICE_LOCAL_TOKEN},
    )
    original_memory_command = server.handle_memory_command
    original_save_message = server.memory.save_message
    server.handle_memory_command = lambda text, socket_id: "safe routing test reply"
    server.memory.save_message = lambda *args, **kwargs: None
    try:
        _payloads(browser, "system_status")
        _payloads(local, "system_status")
        local.emit(
            "user_speech",
            {
                "text": "safe routing test input",
                "request_id": "roundtrip-request",
                "turn_id": "roundtrip-turn",
                "session_id": "spoofed-session-value",
                "source": "local_voice",
            },
        )
        local_reply = _wait_for_payload(local, "jarvis_reply")
        browser_reply = _wait_for_payload(browser, "jarvis_reply")
        assert local_reply is not None
        assert browser_reply is not None
        assert local_reply["request_id"] == "roundtrip-request"
        assert local_reply["turn_id"] == "roundtrip-turn"
        assert local_reply["session_id"] == "roundtrip-test"
        assert local_reply["text"] == "safe routing test reply"
        assert browser_reply == local_reply
    finally:
        server.handle_memory_command = original_memory_command
        server.memory.save_message = original_save_message
        local.disconnect()
        browser.disconnect()


def test_local_voice_approval_requires_exact_owner_and_task_id():
    browser = _authenticated_browser()
    local = server.socketio.test_client(
        server.app,
        auth={"role": "local_voice", "session_id": "approval-test", "token": server.VOICE_LOCAL_TOKEN},
    )
    outcome = {}
    local_sid = _registered_sid("local_voice")
    browser_sid = _registered_sid("browser")

    def request_approval():
        with server.engine_gateway.scoped(client_id=local_sid, task_id="voice-task-1"):
            outcome["approved"] = server.engine_gateway.request_approval(
                "delete_file",
                {"target": "voice-test.txt"},
                "Delete the test file",
            )

    thread = threading.Thread(target=request_approval)
    thread.start()
    try:
        payload = _wait_for_payload(local, "approval_request")
        assert payload is not None
        assert _payloads(browser, "approval_request") == []
        assert payload["task_id"] == "voice-task-1"

        # Wrong owner and wrong task are rejected by the existing gateway.
        assert server.engine_gateway.respond(
            payload["id"], True, client_id=browser_sid, task_id="voice-task-1"
        ) is False
        assert server.engine_gateway.respond(
            payload["id"], True, client_id=local_sid, task_id="wrong-task"
        ) is False

        local.emit(
            "approval_response",
            {"id": payload["id"], "task_id": payload["task_id"], "result": True},
        )
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert outcome["approved"] is True
    finally:
        if thread.is_alive():
            server.engine_gateway.deny_pending(client_id=local_sid, task_id="voice-task-1")
            thread.join(timeout=2)
        local.disconnect()
        browser.disconnect()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
