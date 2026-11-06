# JARVIS / Nexus

An experimental personal AI assistant project for Windows. The goal is a conversational assistant with voice interaction, context-aware responses, memory, and carefully controlled desktop tools.

## Repository snapshot

The `main` branch currently contains the **browser HUD baseline**, not a complete production-ready desktop assistant:

- `ui/index.html` — browser-based HUD.
- `ui/server.py` — Flask + Socket.IO application entry point.
- `core/`, `memory/`, `knowledge/`, and `tools/` — implementation modules and supporting code.
- `config.yaml` — configuration defaults.
- `tests/` — test scripts.

Native Qt desktop integration and local voice work are separate development efforts; do not assume every feature exists or works in this branch.

## Getting started (Windows)

1. Create a Python virtual environment in the repository root:
   ```powershell
   py -m venv venv
   ```
2. Install the dependencies:
   ```powershell
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Configure any required provider credentials locally. Do not commit `.env` or API keys.
4. Run `start.bat`, which starts `ui/server.py`. The server is configured to serve the HUD on `http://localhost:5000`.

The exact runtime behavior depends on your environment and provider configuration. These are setup instructions for the existing code, **not a verified fresh-install test**.

## Current engineering priorities

1. Reconcile the active development checkouts and establish a reproducible Windows build.
2. Validate local speech-to-text, model inference, and text-to-speech as a complete conversation.
3. Keep memory writes, cancellation, external actions, and tool execution behind explicit safety checks.
4. Integrate and refine the native interface once the runtime is stable.

## Development and security notes

- Keep secrets, generated databases, and local runtime artifacts out of Git.
- Test changes before calling a component complete.
- Require user approval for destructive or external actions.
- Treat tool outputs and retrieved content as untrusted input.

This README documents the repository's current baseline and development direction; it does not claim that the whole Nexus roadmap is implemented.
