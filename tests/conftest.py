"""Opt-in fixtures that never import the production server or its services."""
from __future__ import annotations

import ast
from contextlib import nullcontext
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import uuid

from flask import Flask, jsonify, request, send_from_directory, session
from flask_socketio import SocketIO, emit
import pytest

from backend.approval_service import BrowserApprovalGateway
from core.contracts import ObservationEvent, begin_turn, adopt_turn, is_turn_current, end_turn
from security_v2.browser_auth import (
    PairingRegistry, COOKIE_NAME, SESSION_KEY, browser_auth_blueprint,
    local_browser_request, loopback_host,
)


@pytest.fixture
def isolated_auth_server(monkeypatch):
    """Execute actual server handlers/decorators, with only synthetic dependencies.

    Selecting definitions avoids every import-time production constructor. No
    copied handler implementation, database, listener, voice or provider exists.
    """
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(sqlite3, "connect", Mock(side_effect=AssertionError("Real database access in authentication fixture")))
    source = ast.parse((root / "ui/server.py").read_text(encoding="utf-8"))
    server = ModuleType("isolated_nexus_auth_handlers")
    server.__dict__.update(globals())
    server.__name__ = "isolated_nexus_auth_handlers"
    server.JARVIS_HOST, server.JARVIS_PORT = "127.0.0.1", 5000
    server.SESSION_ID = server.VOICE_ENGINE_SESSION_ID = "synthetic-owner"
    server.VOICE_LOCAL_TOKEN = ""
    server.DESKTOP_SAFE_MODE = True
    server.AUTO_OPEN_BROWSER = server.VOICE_ENGINE_ENABLED = True
    server.RUNTIME_LAUNCH_ID = "synthetic-launch"
    server.SHUTDOWN_SECRET = "synthetic-shutdown-secret"
    server._desktop_safe_http_server = None
    server.qalb_router = Mock()
    server.webbrowser = Mock()
    server.CURRENT_DIR = root / "ui"
    server.BROWSER_SESSION_KEY = SESSION_KEY
    server.now = [100.0]
    server.browser_auth = PairingRegistry(
        pairing_ttl_seconds=10, credential_ttl_seconds=30, clock=lambda: server.now[0], schedule_expiry=False,
    )
    server.app = Flask(server.__name__)
    server.app.config.update(SECRET_KEY=secrets.token_hex(32), TESTING=True,
                             SERVER_NAME="localhost:5000", SESSION_COOKIE_NAME=COOKIE_NAME,
                             SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")
    server.app.register_blueprint(browser_auth_blueprint(
        server.browser_auth, bind_host=lambda: server.JARVIS_HOST, port=lambda: server.JARVIS_PORT,
    ))
    server.socketio = SocketIO(server.app, async_mode="threading")
    for name in ("_connection_roles", "_connection_privileged", "_connection_capabilities",
                 "_connection_browser_credentials", "_browser_credential_sids", "_connection_sessions", "_connection_control_scopes", "_connection_request_ids", "pending_forget",
                 "_voice_request_controls"):
        setattr(server, name, {})
    for name in ("_connection_lock", "pending_forget_lock", "_voice_request_controls_lock"):
        setattr(server, name, threading.RLock())
    server._overlay_voice_state = {"state": "idle"}
    server.memory = Mock()
    server.memory.get_stats.return_value = {"messages": 0, "facts": 0, "sessions": 0}
    server.provider_manager = Mock()
    server.provider_manager.status.return_value = {"provider_label": "synthetic"}
    server.task_router = Mock(verification_enabled=False)
    server.job_runtime = Mock()
    server.job_runtime.pending_announcements.return_value = []
    server.engine_orchestrator = Mock()
    server.engine_gateway = BrowserApprovalGateway(server.socketio, policy=Mock())
    server.engine_gateway.policy.assess.return_value = {"status": "requires_approval", "level": "high", "reason": "synthetic"}
    server.knowledge = server.web_research = None
    server.prompt_defense = Mock()
    server.rate_limiter = Mock()
    server.request_coordinator = Mock()
    server.LOG = logging.getLogger(server.__name__)
    constants = {"CONVERSATION_CAPABILITY", "MEMORY_READ_CAPABILITY", "MEMORY_WRITE_CAPABILITY",
                 "MEMORY_DELETE_CAPABILITY", "ENGINE_START_CAPABILITY", "DIAGNOSTICS_READ_CAPABILITY",
                 "_UNAUTHENTICATED_CAPABILITIES", "_AUTHENTICATED_CAPABILITIES"}
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in source.body:
        if isinstance(node, ast.FunctionDef):
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            nodes.append(node)
    tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(tree, str(root / "ui/server.py"), "exec"), server.__dict__)
    server.browser_auth.register_invalidation_callback(server._invalidate_browser_credential)
    server.engine_gateway.client_authorizer = lambda sid: server._has_capability(sid, server.ENGINE_START_CAPABILITY)
    server.memory.secret_detector.redact.side_effect = lambda value: value
    server.memory.get_context.return_value = ""
    server.memory.get_recent.return_value = []
    server.memory.remember_from_text.return_value = None
    server.memory.find_facts.return_value = []
    server.prompt_defense.analyze.return_value = {"is_safe": True}
    server.rate_limiter.acquire.return_value = (True, "")
    server.request_coordinator.session.return_value = nullcontext()
    server.VOICE_CONFIG = SimpleNamespace(section=lambda _: {"qalb": {"enabled": False}})
    server.qalb_router.mode = "hybrid"
    server.detect_voice_language = Mock(return_value="en")
    server.MAX_CONTEXT_MESSAGES = 20
    server.BASE_SYSTEM_PROMPT = "Synthetic system prompt"
    server.KNOWLEDGE_ENABLED = server.WEB_ENABLED = False
    server.COORDINATOR_ENABLED = server.ENSEMBLE_ENABLED = False
    server.COORDINATOR_TASK_TYPES = server.ENSEMBLE_TASK_TYPES = frozenset()
    server.task_router.classify.return_value = SimpleNamespace(
        task_type="GENERAL", requires_knowledge=False, requires_web=False,
        requires_current_evidence=False,
    )
    server.task_router.route.return_value = SimpleNamespace(verify=False)
    server.provider_manager.stream_generate.side_effect = lambda *args, **kwargs: iter([
        "Synthetic reply.", {"__done__": True, "provider": "synthetic", "model": "mock",
                             "full_text": "Synthetic reply."},
    ])
    yield server
    server.browser_auth.revoke_all()
    for sid in list(server._connection_roles):
        server.socketio.server.disconnect(sid, namespace="/")


@pytest.fixture
def paired_browser(isolated_auth_server):
    server = isolated_auth_server
    http = server.app.test_client()
    token = server.browser_auth.issue_owner_pairing_token()
    assert http.post("/auth/pair", json={"token": token},
                     headers={"X-Nexus-Browser-Auth": "1"}).status_code == 200
    socket = server.socketio.test_client(server.app, flask_test_client=http)
    return http, socket
