from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from security_v2.browser_auth import PairingRegistry, COOKIE_NAME, SESSION_KEY

HEADERS = {"X-Nexus-Browser-Auth": "1"}


def pair(server, http, token=None, **kwargs):
    return http.post("/auth/pair", json={"token": token or server.browser_auth.issue_owner_pairing_token()},
                     headers=HEADERS, **kwargs)


def test_anonymous_get_never_mints_trust(isolated_auth_server):
    s = isolated_auth_server
    http = s.app.test_client()
    response = http.get("/")
    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers
    assert http.get("/auth/status").status_code == 401
    socket = s.socketio.test_client(s.app, flask_test_client=http, auth={"role": "owner"})
    assert not s._approval_client_sids()
    assert not s._authorized_presentation_sids()
    assert all(not v for v in s._connection_capabilities.values())
    socket.emit("user_speech", {"text": "hello", "request_id": "guest"})
    assert any(x["name"] == "authorization_error" for x in socket.get_received())
    s.memory.secret_detector.redact.assert_not_called()
    s.provider_manager.stream_generate.assert_not_called()
    s.task_router.classify.assert_not_called()
    s.engine_orchestrator.receive.assert_not_called()
    s.provider_manager.status.assert_not_called()
    socket.emit("get_metrics")
    assert any(p["name"] == "metrics" and p["args"][0].get("error") == "forbidden"
               for p in socket.get_received())


def test_pairing_cookie_status_and_replay(isolated_auth_server):
    s = isolated_auth_server
    http = s.app.test_client()
    token = s.browser_auth.issue_owner_pairing_token()
    response = pair(s, http, token)
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert COOKIE_NAME in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert token not in cookie and token not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert http.get("/auth/status").get_json() == {"authenticated": True}
    assert pair(s, s.app.test_client(), token).status_code == 401
    socket = s.socketio.test_client(s.app, flask_test_client=http)
    assert s._approval_client_sids()
    assert s._authorized_presentation_sids()
    assert socket.is_connected()


def test_token_expiry_and_unknown_token(isolated_auth_server):
    s = isolated_auth_server
    token = s.browser_auth.issue_owner_pairing_token()
    s.now[0] += 10
    assert pair(s, s.app.test_client(), token).status_code == 401
    assert pair(s, s.app.test_client(), "x" * 43).status_code == 401


def test_one_time_redemption_is_atomic():
    registry = PairingRegistry(schedule_expiry=False)
    token = registry.issue_owner_pairing_token()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda value: registry.exchange_pairing_token(value, is_loopback=True), [token] * 24))
    assert len([r for r in results if r is not None]) == 1
    registry.revoke_all()


def test_real_expiry_scheduler_notifies_idle_grant():
    registry = PairingRegistry(credential_ttl_seconds=0.05, schedule_expiry=True)
    invalidated = threading.Event()
    registry.register_invalidation_callback(lambda _cid, _reason: invalidated.set())
    credential = registry.exchange_pairing_token(registry.issue_owner_pairing_token(), is_loopback=True).credential_id
    try:
        assert invalidated.wait(2), "Idle credentials must expire without an incoming request"
        assert not registry.validate(credential)
    finally:
        registry.revoke_all()


@pytest.mark.parametrize("cause", ["revoke", "expiry"])
def test_active_socket_and_pending_approval_invalidated(isolated_auth_server, paired_browser, cause):
    s = isolated_auth_server
    http, socket = paired_browser
    sid = s._approval_client_sids()[0]
    with http.session_transaction() as cookie:
        credential = cookie[SESSION_KEY]
    control_cancelled = threading.Event()
    request_control = {"sid": sid, "cancel_event": control_cancelled, "memory_write": True,
                       "control_scope": s._connection_control_scopes[sid],
                       "control_turn_id": s.begin_turn(s._connection_control_scopes[sid])}
    s._voice_request_controls[(sid, "synthetic")] = request_control
    s.pending_forget[sid] = {"query": "synthetic"}
    pending_seen = threading.Event()
    original_emit = s.engine_gateway._emit

    def observe(name, payload, client_id):
        result = original_emit(name, payload, client_id)
        if name == "approval_request":
            pending_seen.set()
        return result

    s.engine_gateway._emit = observe
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.engine_gateway.request_approval, "execute_objective",
                             client_id=sid, task_id="synthetic-task")
        assert pending_seen.wait(2)
        if cause == "revoke":
            assert http.post("/auth/revoke", headers=HEADERS).status_code == 200
        else:
            s.now[0] += 30
            s.browser_auth.expire_and_notify()
        assert future.result(timeout=2) is False
    assert control_cancelled.is_set()
    assert not request_control["memory_write"]
    assert (sid, "synthetic") not in s._voice_request_controls
    assert not socket.is_connected()
    assert sid not in s.pending_forget
    assert not s._has_capability(sid, s.ENGINE_START_CAPABILITY)
    assert not s._has_capability(sid, s.MEMORY_READ_CAPABILITY)
    assert not s._approval_client_sids()
    assert not s.browser_auth.validate(credential)
    assert http.get("/auth/status").status_code == 401
    # Reusing the original cookie in a new connection must also fail closed.
    with http.session_transaction() as cookie:
        cookie[SESSION_KEY] = credential
    s.socketio.test_client(s.app, flask_test_client=http)
    assert not s._authorized_presentation_sids()


def test_status_or_capability_check_enforces_expiry(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, socket = paired_browser
    sid = s._approval_client_sids()[0]
    s.now[0] += 31
    assert not s._has_capability(sid, s.MEMORY_DELETE_CAPABILITY)
    assert not socket.is_connected()
    assert http.get("/auth/status").status_code == 401


def test_old_cookie_and_forged_registry_credential_rejected(isolated_auth_server):
    s = isolated_auth_server
    http = s.app.test_client()
    legacy = s.app.session_interface.get_signing_serializer(s.app).dumps(
        {"nexus_browser_capability": "previous-public-session"})
    http.set_cookie("session", legacy)
    assert http.get("/auth/status").status_code == 401
    # Even a validly signed new cookie needs a live server-side grant.
    with http.session_transaction() as cookie:
        cookie["nexus_browser_capability"] = "legacy-value"
        cookie[SESSION_KEY] = "unregistered-credential-value" * 2
    s.socketio.test_client(s.app, flask_test_client=http)
    assert not s._authorized_presentation_sids()


@pytest.mark.parametrize("override", [
    {"environ_overrides": {"REMOTE_ADDR": "192.0.2.10"}},
    {"base_url": "http://evil.invalid:5000"},
])
def test_remote_and_rebinding_rejected_without_consuming_token(isolated_auth_server, override):
    s = isolated_auth_server
    token = s.browser_auth.issue_owner_pairing_token()
    assert pair(s, s.app.test_client(), token, **override).status_code == 403
    assert pair(s, s.app.test_client(), token).status_code == 200


def test_non_loopback_bind_disables_privileged_pairing(isolated_auth_server):
    s = isolated_auth_server
    s.JARVIS_HOST = "0.0.0.0"
    assert pair(s, s.app.test_client()).status_code == 403


def test_remote_socket_cannot_reuse_owner_cookie(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, owner = paired_browser
    http.environ_base["REMOTE_ADDR"] = "192.0.2.20"
    remote = s.socketio.test_client(s.app, flask_test_client=http)
    # Only the original local browser retains capabilities.
    assert len(s._approval_client_sids()) == 1
    remote.emit("user_speech", {"text": "hello"})
    assert any(p["name"] == "authorization_error" for p in remote.get_received())
    assert owner.is_connected()


def test_auth_endpoints_require_csrf_header_and_matching_origin(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, socket = paired_browser
    token = s.browser_auth.issue_owner_pairing_token()
    assert http.post("/auth/pair", json={"token": token}).status_code == 403
    assert http.post("/auth/revoke").status_code == 403
    assert http.post("/auth/revoke", headers={**HEADERS, "Origin": "https://evil.invalid"}).status_code == 403
    assert http.get("/auth/status").status_code == 200
    assert socket.is_connected()
    # No owner-token generation route is exposed.
    assert http.post("/auth/browser/token", headers=HEADERS).status_code == 404


def test_pairing_rotation_revokes_previous_socket(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, socket = paired_browser
    assert pair(s, http).status_code == 200
    assert not socket.is_connected()
    s.socketio.test_client(s.app, flask_test_client=http)
    assert len(s._approval_client_sids()) == 1


def test_registry_restart_invalidates_cookie(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, _ = paired_browser
    with http.session_transaction() as cookie:
        credential = cookie[SESSION_KEY]
    other_process = PairingRegistry(schedule_expiry=False)
    assert not other_process.validate(credential)


def test_approval_authorizer_denies_stale_or_invalid_client(isolated_auth_server):
    s = isolated_auth_server
    assert not s.engine_gateway.request_approval("execute_objective", client_id="stale", task_id="x")
    assert not s.engine_gateway.respond("unknown", True, client_id="stale", task_id="x")
    assert not s.engine_gateway.pending


def test_revocation_affects_shared_browser_grant_but_preserves_voice(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, first = paired_browser
    second = s.socketio.test_client(s.app, flask_test_client=http)
    s.VOICE_LOCAL_TOKEN = "synthetic-voice-token"
    voice = s.socketio.test_client(s.app, auth={"role": "local_voice", "token": s.VOICE_LOCAL_TOKEN})
    voice_sid = next(sid for sid, role in s._connection_roles.items() if role == "local_voice")
    assert http.post("/auth/revoke", headers=HEADERS).status_code == 200
    assert not first.is_connected() and not second.is_connected()
    assert voice.is_connected()
    assert s._has_capability(voice_sid, s.MEMORY_READ_CAPABILITY)
    assert s._has_capability(voice_sid, s.CONVERSATION_CAPABILITY)
    voice.emit("get_memory_stats")
    assert any(p["name"] == "memory_stats" for p in voice.get_received())


def test_revoked_approval_cannot_complete_after_positive_response(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, _ = paired_browser
    sid = s._approval_client_sids()[0]
    with http.session_transaction() as cookie:
        credential = cookie[SESSION_KEY]
    ready = threading.Event()
    original_emit = s.engine_gateway._emit
    approval_id = []

    def observe(name, payload, client_id):
        result = original_emit(name, payload, client_id)
        if name == "approval_request":
            approval_id.append(payload["id"])
            ready.set()
        return result

    s.engine_gateway._emit = observe

    def state_changed(state):
        if state == "running":
            s.browser_auth.revoke(credential)

    def request():
        with s.engine_gateway.scoped(sid, "synthetic-task", on_approval_state=state_changed):
            return s.engine_gateway.request_approval("execute_objective")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request)
        assert ready.wait(2)
        assert s.engine_gateway.respond(approval_id[0], True, client_id=sid, task_id="synthetic-task")
        assert future.result(timeout=2) is False


@pytest.mark.parametrize("safe,bind,terminal,issued", [
    (True, "127.0.0.1", True, False),
    (False, "0.0.0.0", True, False),
    (False, "127.0.0.1", False, False),
    (False, "127.0.0.1", True, True),
])
def test_owner_console_bootstrap_requires_explicit_trusted_terminal(isolated_auth_server, safe, bind, terminal, issued):
    s = isolated_auth_server
    s.DESKTOP_SAFE_MODE, s.JARVIS_HOST = safe, bind
    s.sys = SimpleNamespace(argv=["server.py", "--pair-browser"],
                            stdin=SimpleNamespace(isatty=lambda: terminal),
                            stdout=SimpleNamespace(isatty=lambda: terminal))
    s.browser_auth.issue_owner_pairing_token = Mock(wraps=s.browser_auth.issue_owner_pairing_token)
    s._offer_owner_pairing()
    assert s.browser_auth.issue_owner_pairing_token.called is issued
    s.browser_auth.issue_owner_pairing_token.reset_mock()
    s.sys.argv = ["server.py"]
    s._offer_owner_pairing()
    s.browser_auth.issue_owner_pairing_token.assert_not_called()


def test_guest_cannot_enter_turn_or_playback_paths(isolated_auth_server):
    s = isolated_auth_server
    socket = s.socketio.test_client(s.app)
    sid = next(iter(s._connection_roles))
    cancelled = threading.Event()
    s._voice_request_controls[(sid, "synthetic")] = {"sid": sid, "cancel_event": cancelled,
                                              "memory_write": True}
    socket.emit("user_speech", {"text": "hello"})
    socket.emit("voice_cancel", {"request_id": "synthetic"})
    socket.emit("voice_playback_done", {"request_id": "synthetic"})
    assert not cancelled.is_set()
    s.request_coordinator.session.assert_not_called()
    s.memory.save_message.assert_not_called()


def test_legacy_console_token_pairs_existing_browser(isolated_auth_server):
    s = isolated_auth_server
    s.DESKTOP_SAFE_MODE = False
    s.sys = SimpleNamespace(argv=["server.py", "--pair-browser"],
                            stdin=SimpleNamespace(isatty=lambda: True),
                            stdout=SimpleNamespace(isatty=lambda: True))
    s.print = Mock()  # Trusted console only; keep the test token out of logs.
    s._offer_owner_pairing()
    token = s.print.call_args.args[1]
    http = s.app.test_client()
    assert pair(s, http, token).status_code == 200
    socket = s.socketio.test_client(s.app, flask_test_client=http)
    socket.emit("get_memory_stats")
    assert s._approval_client_sids()
    assert any(p["name"] == "memory_stats" and "error" not in p["args"][0]
               for p in socket.get_received())


def test_previous_stage1_endpoint_aliases_share_same_registry(isolated_auth_server):
    s = isolated_auth_server
    http = s.app.test_client()
    token = s.browser_auth.issue_owner_pairing_token()
    assert http.post("/auth/browser/pair", json={"token": token}, headers=HEADERS).status_code == 200
    assert http.get("/auth/status").status_code == 200
    assert http.get("/auth/browser/status").status_code == 200
    assert http.post("/auth/browser/revoke", headers=HEADERS).status_code == 200
    assert http.get("/auth/status").status_code == 401


def test_revoke_all_invalidates_browser_sockets(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    _, socket = paired_browser
    unused_token = s.browser_auth.issue_owner_pairing_token()
    assert s.browser_auth.revoke_all() == 1
    assert not socket.is_connected()
    assert not s._approval_client_sids()
    assert s.browser_auth.exchange_pairing_token(unused_token, is_loopback=True) is None


@pytest.mark.parametrize("regression", [
    "test_desktop_safe_mode_suppresses_voice_even_when_legacy_flags_enable_it",
    "test_desktop_safe_mode_suppresses_browser_opening",
    "test_desktop_safe_mode_does_not_start_qalb",
    "test_desktop_safe_mode_does_not_start_job_worker",
    "test_health_endpoint_reports_launch_identity_and_desktop_safe_mode",
    "test_health_endpoint_rejects_another_instance_identity",
    "test_health_endpoint_is_loopback_only",
    "test_desktop_shutdown_requires_loopback_identity_and_secret",
    "test_legacy_startup_still_starts_qalb_voice_browser_and_jobs",
])
def test_existing_startup_shutdown_regressions_with_synthetic_services(isolated_auth_server, monkeypatch, regression):
    import inspect
    import test_desktop_safe_startup as existing

    # Run the existing regression bodies, bypassing their production-import
    # fixture. Startup services and persistent stores remain synthetic.
    function = getattr(existing, regression)
    arguments = {"server_module": isolated_auth_server, "monkeypatch": monkeypatch}
    function(**{name: arguments[name] for name in inspect.signature(function).parameters})


@pytest.mark.parametrize("request_count", [1, 2])
def test_existing_real_http_shutdown_regression_with_synthetic_services(isolated_auth_server, monkeypatch, request_count):
    import test_desktop_safe_startup as existing

    existing.test_real_http_shutdown_flushes_each_response_before_callback(
        monkeypatch, isolated_auth_server, request_count,
    )


@pytest.mark.parametrize("principal,intent", [
    ("browser", "GENERAL"), ("local_voice", "GENERAL"),
    ("browser", "SYSTEM_ACTION"), ("local_voice", "SYSTEM_ACTION"),
])
def test_authenticated_request_routing_with_mock_providers(isolated_auth_server, paired_browser, principal, intent):
    from contextlib import nullcontext

    s = isolated_auth_server
    _, browser = paired_browser
    s.memory.secret_detector.redact.side_effect = lambda value: value
    s.memory.remember_from_text.return_value = None
    s.prompt_defense.analyze.return_value = {"is_safe": True}
    s.rate_limiter.acquire.return_value = (True, "")
    s.request_coordinator.session.return_value = nullcontext()
    s.begin_turn = s.adopt_turn = Mock(return_value="synthetic-turn")
    s.is_turn_current = Mock(return_value=True)
    s.end_turn = Mock()
    s.ObservationEvent = Mock()
    s.detect_voice_language = Mock(return_value="en")
    s.VOICE_CONFIG = SimpleNamespace(section=lambda _: {"qalb": {"enabled": False}})
    s.qalb_router.mode = "hybrid"
    s.COORDINATOR_ENABLED = s.ENSEMBLE_ENABLED = False
    s.COORDINATOR_TASK_TYPES = s.ENSEMBLE_TASK_TYPES = frozenset()
    s.handle_memory_command = Mock(return_value=None)
    s.retrieve_knowledge = s.retrieve_web = Mock(return_value=("", []))
    s.build_messages = Mock(return_value=[{"role": "user", "content": "synthetic"}])
    s.task_router.classify.return_value = SimpleNamespace(
        task_type=intent, requires_knowledge=False, requires_web=False,
        requires_current_evidence=False,
    )
    s.task_router.route.return_value = SimpleNamespace(verify=False)
    s.provider_manager.stream_generate.return_value = iter([
        "Synthetic reply.", {"__done__": True, "provider": "synthetic",
                             "model": "mock", "full_text": "Synthetic reply."},
    ])
    s.engine_orchestrator.execute.return_value = {"success": True, "steps_completed": 1}
    client = browser
    if principal == "local_voice":
        s.VOICE_LOCAL_TOKEN = "synthetic-voice-token"
        client = s.socketio.test_client(s.app, auth={"role": principal, "token": s.VOICE_LOCAL_TOKEN})
    client.get_received()
    client.emit("user_speech", {"text": "synthetic request", "request_id": "synthetic-routing"})
    replies = [p["args"][0] for p in client.get_received() if p["name"] == "jarvis_reply"]
    assert replies
    if principal == "browser" and intent == "SYSTEM_ACTION":
        s.engine_orchestrator.receive.assert_called_once_with("synthetic request")
        s.engine_orchestrator.execute.assert_called_once()
        s.provider_manager.stream_generate.assert_not_called()
        assert replies[-1]["provider"] == "engine"
    else:
        s.engine_orchestrator.receive.assert_not_called()
        s.provider_manager.stream_generate.assert_called_once()
        assert replies[-1]["text"] == "Synthetic reply."
        assert replies[-1]["provider"] == "synthetic"


def test_callback_failure_does_not_suppress_other_invalidations():
    registry = PairingRegistry()
    reached = Mock()
    registry.register_invalidation_callback(Mock(side_effect=RuntimeError("synthetic callback failure")))
    registry.register_invalidation_callback(reached)
    record = registry.exchange_pairing_token(registry.issue_owner_pairing_token(), is_loopback=True)
    assert registry.revoke(record.credential_id)
    reached.assert_called_once_with(record.credential_id, "revoked")
    assert registry.validate(record.credential_id) is None


def test_cached_capabilities_cannot_replace_registry_validation(isolated_auth_server, paired_browser):
    s = isolated_auth_server
    http, socket = paired_browser
    sid = s._approval_client_sids()[0]
    with http.session_transaction() as cookie:
        credential_id = cookie[SESSION_KEY]
    # Simulate lost server-side state without the normal invalidation callback.
    s.browser_auth._credentials.pop(credential_id)
    assert s.ENGINE_START_CAPABILITY in s._connection_capabilities[sid]
    socket.emit("get_memory_stats")
    socket.emit("get_metrics")
    socket.emit("user_speech", {"text": "synthetic request"})
    assert not s._has_capability(sid, s.ENGINE_START_CAPABILITY)
    assert not s._is_privileged_sid(sid)
    s.provider_manager.stream_generate.assert_not_called()
    responses = {p["name"]: p["args"][0] for p in socket.get_received()}
    assert responses["memory_stats"]["error"] == "forbidden"
    assert responses["metrics"]["error"] == "forbidden"
    assert responses["authorization_error"]["error"] == "pairing_required"


@pytest.mark.parametrize("token", [None, "", [], {}, 123, "\ud800", "é" * 43, "x" * 1025])
def test_malformed_pairing_tokens_are_rejected(isolated_auth_server, token):
    s = isolated_auth_server
    response = s.app.test_client().post("/auth/pair", json={"token": token}, headers=HEADERS)
    assert response.status_code == 401
    assert response.get_json() == {"authenticated": False, "error": "invalid_pairing_token"}
