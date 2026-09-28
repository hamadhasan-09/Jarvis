from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
HUD = UI / "hud"

MODULES = [
    "session.js",
    "app.js",
    "config.js",
    "dom.js",
    "lifecycle.js",
    "accessibility.js",
    "core-renderer.js",
    "state.js",
    "voice-preferences.js",
    "tts.js",
    "socket-bridge.js",
    "audio.js",
    "voice-commands.js",
    "voice.js",
    "conversation.js",
    "approval.js",
]


def test_index_has_one_application_entry_and_no_inline_session_script():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/ui/hud/session.js"></script>' in html
    assert html.count("<script") == 2  # Socket.IO CDN plus the module entry
    assert "(() => {" not in html
    assert "function setupSpeechRecognition" not in html
    assert "onclick=" not in html


def test_required_frontend_modules_exist_and_parse():
    for name in MODULES:
        path = HUD / name
        assert path.exists(), name
        result = subprocess.run(
            ["node", "--check", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_frontend_has_one_session_owner_and_no_backend_imports():
    session = (HUD / "session.js").read_text(encoding="utf-8")
    assert "createHudApp" in session
    assert "socket.on" not in session
    assert "SpeechRecognition" not in session
    for path in HUD.glob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "/backend/" not in text
        assert "memory_manager" not in text
        assert "groq" not in text.lower()


def test_dom_contract_and_state_contract_are_explicit():
    html = (UI / "index.html").read_text(encoding="utf-8")
    dom = (HUD / "dom.js").read_text(encoding="utf-8")
    states = (UI / "core" / "states.js").read_text(encoding="utf-8")
    for element_id in (
        "coreScene",
        "approvalApproveButton",
        "approvalDenyButton",
        "approvalCancelButton",
        "voicePermissionButton",
        "jarvisLive",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in dom
    for state in ("IDLE", "LISTENING", "USER_SPEAKING", "THINKING", "JARVIS_SPEAKING", "INTERRUPTED", "ERROR"):
        assert re.search(rf'\b{state}\b', states)


def test_existing_socket_event_contract_names_remain_referenced():
    html_contract = (UI / "UI_EVENTS.md").read_text(encoding="utf-8")
    source = "\n".join(path.read_text(encoding="utf-8") for path in HUD.glob("*.js"))
    for event_name in (
        "system_status",
        "status",
        "approval_request",
        "jarvis_reply_token",
        "jarvis_reply",
        "user_speech",
        "approval_response",
    ):
        assert event_name in html_contract
        assert event_name in source
