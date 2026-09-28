# JARVIS Voice Replacement Audit

Date: 2026-09-26

## Scope and current selection

`start.bat` launches `ui/server.py`. This audit recorded the state before the replacement implementation. Current startup is now:

- `JARVIS_VOICE_ENGINE=1` or `JARVIS_LOCAL_VOICE=1` starts the new `voice/` service.
- The browser HUD avoids local speech synthesis while `HUD_CONFIG.localVoiceMode` is true.
- The previous `voice_engine/` and `voice/local_listener.py` implementations remain present for rollback and review. They are not started by the replacement path.

The active `.env` values previously inspected select the legacy local listener, not the standalone engine. Secret values were not read for this audit.

## Voice files and related dependencies

| Area | Current files | Current role / disposition after replacement |
|---|---|---|
| Previous local listener | `voice/local_listener.py` | sounddevice capture, energy threshold segmentation, Groq Whisper STT with optional Google SpeechRecognition fallback, Socket.IO `user_speech`, Windows System.Speech output. Retained for rollback; not selected by the replacement startup path. |
| Server switch | `ui/server.py` | Chooses the local voice process; also owns the existing brain and Socket.IO boundary. Keep brain/tool behavior; change only voice startup and the Urdu response adapter boundary. |
| Browser voice | `ui/hud/voice.js`, `ui/hud/tts.js`, `ui/hud/audio.js`, `ui/hud/conversation.js`, `ui/hud/preferences.js`, `ui/hud/app.js`, `ui/hud/config.js` | Optional Web Speech / browser speech synthesis and event presentation. Must be disabled when the new local voice service owns the mic and speaker to prevent duplicate capture/playback. |
| Previous standalone engine | `voice_engine/**` | Separate sounddevice/energy-or-WebRTC VAD/Faster-Whisper/Rumik-or-Windows implementation. Retained for rollback and older tests; do not run alongside the new service. |
| Other active/legacy voice code | `core/listener.py`, `core/voice_engine.py`, `test_voice.py`, `test_hd_voices.py`, `scripts/install_local_voice.py` | Older sounddevice/Groq and Edge-TTS implementations or installer/test scripts. Keep until migration validation; then remove or clearly archive only if no remaining imports/references. |
| Archived implementations | `archive/voice_engine.py`, `archive/jarvis_v5.py`, `archive/app_gui.py`, `archive/` backups | Historical voice code. Not selected by `start.bat`; retain as archive rather than delete as part of active replacement. |
| Diagnostics and contract | `tools/diagnose_voice_capture.py`, `backend/event_contract.md`, `ui/UI_EVENTS.md`, `voice_engine/README.md` | Capture diagnostic, event contract, and architecture/setup docs. Update docs for the new voice service and ensure stale setup instructions are clearly marked. |

Current direct dependencies include `sounddevice`, `SpeechRecognition`, `edge-tts`, NumPy, Flask-SocketIO, python-socketio, and Groq. Faster-Whisper, WebRTC VAD, and websockets are optional/commented in `requirements.txt`. The requested `soxr`, `silero-vad`, `openwakeword`, and `parler-tts` dependencies are not currently declared as active requirements. Browser Web Speech and Windows System.Speech are platform-provided APIs, not Python packages.

## Brain integration contract

The existing integration point is Socket.IO, not a callable LLM function in the voice package:

1. Voice sends `user_speech` with `text`, `request_id`, and optional `language_hint` to `ui/server.py`.
2. `ui/server.py::on_user_speech` performs the existing safety, memory, task-routing, tool/action, retrieval, and provider work.
3. The server streams `jarvis_reply_token` and ends with `jarvis_reply`, correlated by `request_id`/`turn_id`.
4. Approvals use the existing `approval_request` / `approval_response` contract and must remain correlated.

The new voice service should preserve this contract. It can pass a language hint/instruction through an adapter, but must not move tool execution or risk policy into Qalb. Urdu hybrid mode needs a narrow post-brain spoken-reply adapter: let the existing brain finish its decision/actions, then local Qalb rewrites the result for speech. Direct Urdu mode is an explicit configured bypass and must not invoke tools. English remains on the current brain unchanged.

## Replacement boundaries

- Add the new `voice/` service modules and single `voice_config.yaml`; keep `voice/local_listener.py` until all requested tests and an available-runtime self-test pass.
- Add `brain/router.py` as a language/output adapter only; leave existing provider, tool, memory, safety, and action implementations intact.
- Configure Qalb with the user's exact GGUF path and explicit llama.cpp/Ollama choice. Do not download or substitute any model.
- No old voice files or dependencies are to be deleted before test evidence is reviewed. Historical `archive/` content is not part of the active deletion set.

## Implementation and validation status

- New `voice/` capture, VAD, wake-word gate, STT, text-cleaning, Parler TTS, playback, service, logging, Socket.IO bridge, and manual self-test modules are in place.
- Urdu routing supports direct/hybrid modes and Qalb validation/fallback. `voice_config.yaml` points to the exact user-provided file `C:\Users\Hammad\Desktop\Nexus\models\qalb\Qalb-1.0-8B-Instruct.Q4_K_M.gguf`, enables llama.cpp auto-start, and sets `gpu_layers: 0`. The 4,920,735,200-byte GGUF exists at that location; no model was downloaded or substituted.
- GGUF metadata inspection confirms architecture `llama`, `tokenizer.ggml.model=gpt2`, `tokenizer.ggml.pre=llama-bpe`, BOS 128000, EOS 128001, and Llama 3 EOT token 128009. The file does not include `tokenizer.chat_template`, so llama.cpp is explicitly configured with its `llama3` template. Requests include EOS/EOT stops, and leaked control/template tokens or a fake continuation are rejected.
- `llama-server.exe` is not installed or discoverable on PATH, so live Qalb inference could not be run for `السلام علیکم، آپ کون ہیں؟`. The launcher command was tested with a mocked process to verify the exact `-m` path, `-ngl 0`, and `--chat-template llama3`. The OpenAI-compatible request stop strings were also asserted, as was rejection of a simulated leaked multi-turn completion. Auto-start failure returns to the existing Urdu fallback.
- Focused replacement/routing suite: 32 passed, 2 skipped (real speech fixture files are not present). Tests cover the configured `large-v3` primary, capable-GPU selection, insufficient-VRAM turbo CPU fallback, configurable model names, `force_primary`, exact STT startup output, and Qalb template/stop/validation behavior. Compilation of `voice/`, `brain/`, and `ui/server.py` passed.
- Legacy-inclusive suite: one existing Rumik adapter test fails because an environment-provided language value is sent in its payload while the test expects only text; 39 other tests passed and 2 real-audio tests skipped. The new replacement path does not use Rumik.
- Live microphone/model acceptance has not run: current Python 3.14 environment lacks the required speech stack, and the machine has a 2 GB Quadro K620. See runtime constraints above and `README_VOICE.md`.

## Runtime constraints to resolve before live-model acceptance

Current hardware inspection finds a Quadro K620 with 2 GB VRAM (about 1.45 GB free), compute capability 5.0. The active project venv is Python 3.14.7 and does not contain PyTorch, Faster-Whisper, soxr, Silero VAD, Transformers, or Parler-TTS. The GPU cannot hold Whisper large-v3, Parler, and Qalb together; default config therefore selects CPU when less than the configured free-memory threshold is available and sets llama.cpp GPU layers to 0. Live model acceptance requires a compatible Python/PyTorch/CUDA runtime and required speech model files. Unit tests can still validate config, language routing, validation, fallback, and event contracts with mocked models/server.
