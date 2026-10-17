from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from engine.tools import Toolchain
from llm.provider_manager import ProviderManager
from security_v2.broker import SecurityBroker
from security_v2.key_manager import SecureKeyManager


class FakeGateway:
    def __init__(self, decisions=None):
        self.decisions = list(decisions or [True])
        self.calls = []

    def request_approval(self, action_type, context=None, preview=""):
        self.calls.append((action_type, context, preview))
        return self.decisions.pop(0) if self.decisions else True


class FakeKeyManager:
    def __init__(self, key):
        self.key = key
        self.calls = []

    def get_key(self, service):
        self.calls.append(service)
        return self.key


def make_broker(tmp_path, policy, *, gateway=None, key_manager=None):
    policy_path = tmp_path / "permission_policy.yaml"
    policy_path.write_text("capabilities:\n" + "".join(f"  {name}: {rule}\n" for name, rule in policy.items()), encoding="utf-8")
    return SecurityBroker(
        gateway=gateway or FakeGateway(),
        key_manager=key_manager or FakeKeyManager(None),
        policy_path=policy_path,
        audit_log_path=tmp_path / "logs" / "permission_audit.jsonl",
    )


def read_audit(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_deny_always_never_calls_gateway_and_is_audited(tmp_path):
    gateway = FakeGateway()
    broker = make_broker(tmp_path, {"system.always_human": "deny_always"}, gateway=gateway)

    result = broker.authorize("system.always_human", "change_password", {}, "change password")

    assert result["allowed"] is False
    assert result["rule_applied"] == "deny_always"
    assert gateway.calls == []
    assert len(read_audit(broker.audit_log_path)) == 1


def test_auto_allow_never_calls_gateway_and_is_audited(tmp_path):
    gateway = FakeGateway()
    broker = make_broker(tmp_path, {"fs.read": "auto_allow"}, gateway=gateway)

    result = broker.authorize("fs.read", "read_file", {"path": "notes.txt"}, "read notes")

    assert result["allowed"] is True
    assert gateway.calls == []
    assert read_audit(broker.audit_log_path)[0]["decision"] == "allowed"


def test_ask_every_time_calls_gateway_for_each_request(tmp_path):
    gateway = FakeGateway([True, False])
    broker = make_broker(tmp_path, {"shell.exec": "ask_every_time"}, gateway=gateway)

    first = broker.authorize("shell.exec", "run_command", {"command": "echo hi"}, "echo hi")
    second = broker.authorize("shell.exec", "run_command", {"command": "echo hi"}, "echo hi")

    assert first["allowed"] is True
    assert second["allowed"] is False
    assert len(gateway.calls) == 2
    assert len(read_audit(broker.audit_log_path)) == 2


def test_ask_once_per_session_reuses_approval_and_audits_each_call(tmp_path):
    gateway = FakeGateway([True])
    broker = make_broker(tmp_path, {"fs.write": "ask_once_per_session"}, gateway=gateway)

    results = [broker.authorize("fs.write", "write_file", {"path": f"{i}.txt"}, "write") for i in range(3)]

    assert all(result["allowed"] for result in results)
    assert len(gateway.calls) == 1
    entries = read_audit(broker.audit_log_path)
    assert len(entries) == 3
    assert [entry["reused"] for entry in entries] == [False, True, True]


def test_unknown_capability_requires_approval(tmp_path):
    gateway = FakeGateway([False])
    broker = make_broker(tmp_path, {"fs.read": "auto_allow", "future.capability": "auto_allow"}, gateway=gateway)

    result = broker.authorize("future.capability", "future_action", {}, "preview")

    assert result["allowed"] is False
    assert result["rule_applied"] == "ask_every_time"
    assert len(gateway.calls) == 1
    assert read_audit(broker.audit_log_path)[0]["rule_applied"] == "ask_every_time"


def test_audit_entries_are_json_and_redact_detected_secrets(tmp_path):
    broker = make_broker(tmp_path, {"net.send": "ask_every_time"})
    secret = "gsk_" + "A" * 32

    broker.authorize("net.send", "upload", {"details": secret}, f"send {secret}")

    lines = broker.audit_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert secret not in lines[0]
    entry = json.loads(lines[0])
    assert entry["timestamp"]
    assert entry["context_summary"]
    assert "REDACTED_GROQ_KEY" in entry["context_summary"]
    assert "REDACTED_GROQ_KEY" in entry["preview_summary"]


def test_get_credential_returns_key_and_audits_access(tmp_path):
    key_manager = FakeKeyManager("test-key-value")
    broker = make_broker(
        tmp_path,
        {"credential.read": "auto_allow"},
        key_manager=key_manager,
    )

    assert broker.get_credential("groq", "unit test") == "test-key-value"
    assert key_manager.calls == ["groq"]
    entries = read_audit(broker.audit_log_path)
    assert len(entries) == 1
    assert entries[0]["capability"] == "credential.read"
    assert entries[0]["decision"] == "allowed"
    assert "test-key-value" not in broker.audit_log_path.read_text(encoding="utf-8")


def test_toolchain_bash_deny_blocks_before_subprocess_or_sandbox(tmp_path, monkeypatch):
    broker = make_broker(tmp_path, {"shell.exec": "deny_always"})
    toolchain = Toolchain(tmp_path, broker=broker)
    monkeypatch.setattr(toolchain.sandbox, "execute", lambda *args, **kwargs: pytest.fail("sandbox ran after denial"))

    with patch.object(subprocess, "run") as run:
        result = toolchain.run_bash("echo hi")

    run.assert_not_called()
    assert result.startswith("🚫 Blocked by security policy:")


def test_toolchain_bash_auto_allow_preserves_sandbox_result(tmp_path):
    broker = make_broker(tmp_path, {"shell.exec": "auto_allow"})
    toolchain = Toolchain(tmp_path, broker=broker)

    result = toolchain.run_bash("echo hi")

    assert result == "Sandboxed command\nSuccess.\nStdout:\nhi\nStderr:"


def test_toolchain_python_deny_blocks_before_subprocess_or_file_execution(tmp_path, monkeypatch):
    broker = make_broker(tmp_path, {"python.exec": "deny_always"})
    toolchain = Toolchain(tmp_path, broker=broker)
    monkeypatch.setattr(toolchain.sandbox, "execute_python_file", lambda *args, **kwargs: pytest.fail("python executed after denial"))

    with patch.object(subprocess, "run") as run:
        result = toolchain.run_python("print('hi')")

    run.assert_not_called()
    assert result.startswith("🚫 Blocked by security policy:")


def test_toolchain_python_auto_allow_preserves_disabled_sandbox_result(tmp_path):
    broker = make_broker(tmp_path, {"python.exec": "auto_allow"})
    toolchain = Toolchain(tmp_path, broker=broker)

    result = toolchain.run_python("print('hi')")

    assert result == (
        "Sandboxed Python\nFailed.\nStdout:\n\nStderr:\n"
        "Generated Python execution is disabled by the sandbox policy"
    )


def test_provider_manager_resolves_all_provider_keys_through_broker(tmp_path, monkeypatch):
    key_manager = FakeKeyManager("provider-test-key")
    broker = make_broker(tmp_path, {"credential.read": "auto_allow"}, key_manager=key_manager)
    credential_calls = []
    original_get_credential = broker.get_credential

    def record_credential_call(service, purpose=""):
        credential_calls.append((service, purpose))
        return original_get_credential(service, purpose=purpose)

    broker.get_credential = record_credential_call

    def direct_key_access_is_a_bug(*args, **kwargs):
        pytest.fail("ProviderManager called SecureKeyManager.get_key directly")

    monkeypatch.setattr(SecureKeyManager, "get_key", direct_key_access_is_a_bug)
    monkeypatch.setenv("JARVIS_ENABLE_GEMINI", "true")
    monkeypatch.setenv("JARVIS_ENABLE_ANTHROPIC", "true")
    monkeypatch.setenv("JARVIS_ENABLE_EXPERIENTIAL", "true")

    # Verify credential wiring without initializing any real provider SDK.
    import llm.provider_manager as provider_module
    adapter_keys = []

    class SyntheticAdapter:
        def __init__(self, api_key, model, timeout, base_url=None):
            adapter_keys.append(api_key)
            self.model = model
            self.is_configured = api_key == key_manager.key

    for adapter_name in ("GroqProvider", "GeminiProvider", "AnthropicProvider", "ExperientialProvider"):
        monkeypatch.setattr(provider_module, adapter_name, SyntheticAdapter)

    manager = ProviderManager(project_root=tmp_path, broker=broker)
    assert adapter_keys and all(key == key_manager.key for key in adapter_keys)

    assert manager.has_primary is True
    assert set(key_manager.calls) == {"groq", "google", "anthropic", "experiential"}
    assert set(credential_calls) == {
        ("groq", "llm_provider_init"),
        ("google", "llm_provider_init"),
        ("anthropic", "llm_provider_init"),
        ("experiential", "llm_provider_init"),
    }
    entries = read_audit(broker.audit_log_path)
    assert len(entries) == 4
    assert all(entry["capability"] == "credential.read" for entry in entries)
    assert all(entry["decision"] == "allowed" for entry in entries)


def test_provider_credential_audit_never_contains_returned_secret_or_purpose(tmp_path):
    secret = "gsk_FAKEKEY1234567890fakefakefake"
    unstructured_secret = "RAW-PROVIDER-TOKEN-39e71c5f"
    key_manager = FakeKeyManager(secret)
    broker = make_broker(
        tmp_path,
        {"credential.read": "auto_allow"},
        key_manager=key_manager,
    )

    assert broker.get_credential("groq", purpose=secret) == secret
    key_manager.key = unstructured_secret
    assert broker.get_credential("experiential", purpose=unstructured_secret) == unstructured_secret
    broker.authorize(
        "credential.read",
        "credential_audit_redaction_check",
        {"purpose": secret, "credential": unstructured_secret},
        preview=f"Handle {secret} and {unstructured_secret}",
    )

    log_text = broker.audit_log_path.read_text(encoding="utf-8")
    assert secret not in log_text
    assert unstructured_secret not in log_text
    entries = read_audit(broker.audit_log_path)
    assert len(entries) == 3
    assert all(entry["capability"] == "credential.read" for entry in entries)
    assert all(entry["decision"] == "allowed" for entry in entries)
    assert "[REDACTED_CREDENTIAL]" in entries[-1]["context_summary"]
    assert "[REDACTED_CREDENTIAL]" in entries[-1]["preview_summary"]
