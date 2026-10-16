from __future__ import annotations

from unittest.mock import Mock

import pytest


@pytest.fixture
def server_module(monkeypatch):
    """Import the server without opening project databases or starting workers."""
    import importlib
    import sys
    from types import SimpleNamespace

    # ui.server constructs these services at import time. Keep this test isolated
    # from the user's databases and from filesystem permissions in test runners.
    import core.memory_manager
    import engine.job_runtime
    import knowledge
    import security_v2.broker

    class MemoryStub:
        secret_detector = SimpleNamespace(redact=lambda text: text)

        def __init__(self, *args, **kwargs):
            pass

    class JobRuntimeStub:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def pending_announcements(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(core.memory_manager, "PersistentMemory", MemoryStub)
    monkeypatch.setattr(engine.job_runtime, "JobRuntime", JobRuntimeStub)
    monkeypatch.setattr(knowledge, "KnowledgeBase", lambda *args, **kwargs: None)
    # ProviderManager construction only needs credential lookup; its broker audit
    # writes into the protected project data directory, so isolate that write.
    monkeypatch.setattr(security_v2.broker.SecurityBroker, "_append_audit", lambda *args, **kwargs: None)

    if "ui.server" in sys.modules:
        return sys.modules["ui.server"]
    return importlib.import_module("ui.server")


class _ThreadStub:
    def __init__(self, target, **kwargs):
        self.target = target
        self.kwargs = kwargs

    def start(self):
        _ThreadStub.started.append((self.target, self.kwargs))


_ThreadStub.started = []


def _install_safe_mode(monkeypatch, server):
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", True)
    monkeypatch.setattr(server, "VOICE_ENGINE_ENABLED", True)
    monkeypatch.setattr(server, "AUTO_OPEN_BROWSER", True)
    monkeypatch.setenv("JARVIS_LOCAL_VOICE", "true")
    _ThreadStub.started = []


def test_desktop_safe_mode_suppresses_voice_even_when_legacy_flags_enable_it(monkeypatch, server_module):
    server = server_module
    _install_safe_mode(monkeypatch, server)
    voice_initializer = Mock()

    server._start_pre_server_services(voice_initializer, thread_factory=_ThreadStub)

    voice_initializer.assert_not_called()
    assert _ThreadStub.started == []


def test_desktop_safe_mode_suppresses_browser_opening(monkeypatch, server_module):
    server = server_module
    _install_safe_mode(monkeypatch, server)
    webbrowser_open = Mock()
    monkeypatch.setattr(server.webbrowser, "open", webbrowser_open)

    server._start_post_banner_services(thread_factory=_ThreadStub)
    server.auto_open_browser()

    webbrowser_open.assert_not_called()
    assert _ThreadStub.started == []


def test_desktop_safe_mode_does_not_start_qalb(monkeypatch, server_module):
    server = server_module
    _install_safe_mode(monkeypatch, server)
    qalb_startup = Mock()
    monkeypatch.setattr(server.qalb_router, "startup", qalb_startup)

    server._start_pre_server_services(Mock(), thread_factory=_ThreadStub)

    qalb_startup.assert_not_called()


def test_desktop_safe_mode_does_not_start_job_worker(monkeypatch, server_module):
    server = server_module
    _install_safe_mode(monkeypatch, server)
    job_start = Mock()
    monkeypatch.setattr(server.job_runtime, "start", job_start)

    server._start_post_banner_services(thread_factory=_ThreadStub)

    job_start.assert_not_called()


def test_health_endpoint_reports_launch_identity_and_desktop_safe_mode(monkeypatch, server_module):
    server = server_module
    monkeypatch.setattr(server, "RUNTIME_LAUNCH_ID", "launch-test-123")
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", True)

    response = server.app.test_client().get(
        "/healthz?expected_launch_id=launch-test-123",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "runtime_status": "ready",
        "launch_id": "launch-test-123",
        "desktop_safe_mode": True,
    }


def test_health_endpoint_rejects_another_instance_identity(monkeypatch, server_module):
    server = server_module
    monkeypatch.setattr(server, "RUNTIME_LAUNCH_ID", "actual-process")
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", True)

    response = server.app.test_client().get(
        "/healthz?expected_launch_id=stale-process",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "runtime_status": "identity_mismatch",
        "launch_id": "actual-process",
        "desktop_safe_mode": True,
    }


def test_health_endpoint_is_loopback_only(server_module):
    response = server_module.app.test_client().get(
        "/healthz",
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"runtime_status": "forbidden"}


def test_desktop_shutdown_requires_loopback_identity_and_secret(monkeypatch, server_module):
    server = server_module
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", True)
    monkeypatch.setattr(server, "RUNTIME_LAUNCH_ID", "launch-shutdown-test")
    monkeypatch.setattr(server, "SHUTDOWN_SECRET", "test-only-unpredictable-secret")

    class ShutdownStub:
        called = False

        def shutdown(self):
            self.called = True

    shutdown_server = ShutdownStub()
    monkeypatch.setattr(server, "_desktop_safe_http_server", shutdown_server)
    client = server.app.test_client()
    payload = {"launch_id": "launch-shutdown-test"}

    unauthorized = client.post(
        "/shutdown", json=payload, environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    assert unauthorized.status_code == 403
    assert not shutdown_server.called

    wrong_identity = client.post(
        "/shutdown",
        json={"launch_id": "another-launch"},
        headers={"X-Nexus-Shutdown-Secret": "test-only-unpredictable-secret"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert wrong_identity.status_code == 409
    assert not shutdown_server.called

    non_loopback = client.post(
        "/shutdown",
        json=payload,
        headers={"X-Nexus-Shutdown-Secret": "test-only-unpredictable-secret"},
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )
    assert non_loopback.status_code == 403
    assert not shutdown_server.called

    accepted = client.post(
        "/shutdown",
        json=payload,
        headers={"X-Nexus-Shutdown-Secret": "test-only-unpredictable-secret"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert accepted.status_code == 202
    assert accepted.get_json() == {
        "runtime_status": "stopping",
        "launch_id": "launch-shutdown-test",
    }
    assert not shutdown_server.called
    assert "test-only-unpredictable-secret" not in accepted.get_data(as_text=True)
    accepted.close()
    assert shutdown_server.called


@pytest.mark.parametrize("request_count", [1, 2])
def test_real_http_shutdown_flushes_each_response_before_callback(monkeypatch, server_module, request_count):
    import http.client
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from werkzeug.serving import WSGIRequestHandler, make_server

    server = server_module
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", True)
    monkeypatch.setattr(server, "RUNTIME_LAUNCH_ID", "real-http-shutdown-test")
    monkeypatch.setattr(server, "SHUTDOWN_SECRET", "test-shutdown-secret")
    expected = {"runtime_status": "stopping", "launch_id": "real-http-shutdown-test"}
    flushed = {}
    callbacks = []
    all_closed = threading.Event()
    accepted = threading.Barrier(request_count)

    class TrackedWriter:
        def __init__(self, stream):
            self.stream = stream
            self.written = bytearray()

        def write(self, data):
            result = self.stream.write(data)
            self.written.extend(data)
            return result

        def flush(self):
            self.stream.flush()
            if b"\r\n\r\n" in self.written:
                body = bytes(self.written).split(b"\r\n\r\n", 1)[1]
                try:
                    flushed[threading.get_ident()] = json.loads(body) == expected
                except ValueError:
                    pass

        def __getattr__(self, name):
            return getattr(self.stream, name)

    class TrackedHandler(WSGIRequestHandler):
        def setup(self):
            super().setup()
            self.wfile = TrackedWriter(self.wfile)

        def log(self, *args):
            pass

    def simultaneous_requests(environ, start_response):
        # Both repeated requests must be accepted before either can stop the
        # serving loop. This synchronizes the test without timing/sleep delays.
        accepted.wait(timeout=5)
        return server.app(environ, start_response)

    http_server = make_server("127.0.0.1", 0, simultaneous_requests, threaded=True, request_handler=TrackedHandler)
    stop_server = http_server.shutdown

    def checked_shutdown():
        callbacks.append(flushed.get(threading.get_ident(), False))
        stop_server()
        if len(callbacks) == request_count:
            all_closed.set()

    monkeypatch.setattr(http_server, "shutdown", checked_shutdown)
    monkeypatch.setattr(server, "_desktop_safe_http_server", http_server)
    serving = threading.Thread(target=http_server.serve_forever)
    serving.start()

    def request_shutdown():
        connection = http.client.HTTPConnection("127.0.0.1", http_server.server_port, timeout=5)
        try:
            connection.request(
                "POST", "/shutdown", json.dumps({"launch_id": "real-http-shutdown-test"}),
                {"Content-Type": "application/json", "X-Nexus-Shutdown-Secret": "test-shutdown-secret"},
            )
            response = connection.getresponse()
            body = response.read()
            assert response.status == 202
            assert len(body) == int(response.getheader("Content-Length"))
            assert json.loads(body) == expected
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=request_count) as clients:
            list(clients.map(lambda _: request_shutdown(), range(request_count)))
        assert all_closed.wait(5)
        serving.join(5)
        assert not serving.is_alive()
        assert callbacks == [True] * request_count
    finally:
        if serving.is_alive():
            stop_server()
            serving.join(5)
        http_server.server_close()


def test_legacy_startup_still_starts_qalb_voice_browser_and_jobs(monkeypatch, server_module):
    server = server_module
    monkeypatch.setattr(server, "DESKTOP_SAFE_MODE", False)
    monkeypatch.setattr(server, "VOICE_ENGINE_ENABLED", True)
    monkeypatch.setattr(server, "AUTO_OPEN_BROWSER", True)
    monkeypatch.setenv("JARVIS_LOCAL_VOICE", "false")
    _ThreadStub.started = []
    qalb_startup = Mock()
    job_start = Mock()
    monkeypatch.setattr(server.qalb_router, "startup", qalb_startup)
    monkeypatch.setattr(server.job_runtime, "start", job_start)
    voice_initializer = Mock()

    server._start_pre_server_services(voice_initializer, thread_factory=_ThreadStub)
    server._start_post_banner_services(thread_factory=_ThreadStub)

    qalb_startup.assert_called_once_with()
    job_start.assert_called_once_with()
    assert [target for target, _kwargs in _ThreadStub.started] == [
        voice_initializer,
        server.auto_open_browser,
    ]
