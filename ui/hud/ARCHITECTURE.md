# JARVIS frontend architecture

This directory is the single active frontend architecture. `ui/index.html` owns the visual markup and stylesheet; `/ui/hud/session.js` is the only application entry point.

## Composition

```text
ui/index.html
  └── ui/hud/session.js                 composition root and lifecycle handle
      └── ui/hud/app.js                 wires the frontend modules
          ├── core-renderer.js          reference-core presentation adapter
          ├── state.js                   application state → canonical core state
          ├── audio.js                   microphone/TTS analyser boundary
          ├── conversation.js            request, stream, transcript, and reply state
          ├── socket-bridge.js            Socket.IO transport only
          ├── voice.js                   browser microphone and SpeechRecognition
          ├── tts.js                     browser speechSynthesis output
          ├── approval.js                approval modal presentation and response
          ├── accessibility.js           ARIA and focus behavior
          ├── voice-preferences.js       language/gender preference persistence
          ├── voice-commands.js           local voice-command parsing
          ├── dom.js                     required DOM registry
          ├── config.js                  frontend timing/configuration
          └── lifecycle.js               listener/resource cleanup
```

`ui/core/` remains the renderer/controller scaffold for the future procedural core. It is not activated by this architecture step. The live renderer is deliberately `ReferenceCoreRenderer`, which applies the existing `coreScene` state attributes and preserves the current reference-image/CSS appearance.

## State contract

The canonical renderer states are defined in `ui/core/states.js`:

```text
IDLE
LISTENING
USER_SPEAKING
THINKING
JARVIS_SPEAKING
INTERRUPTED
ERROR
```

Backend lifecycle states such as `generating`, `retrieving`, `verifying`, `executing`, and `waiting_confirmation` are normalized to `THINKING` or an appropriate presentation state. No LLM, memory, RAG, tool, approval-policy, or security decisions are made by the renderer.

## Event boundary

`SocketEventBridge` preserves the existing event names from `ui/UI_EVENTS.md` and `backend/event_contract.md`:

- Server → browser: `system_status`, `status`, `approval_request`, `jarvis_reply_token`, `jarvis_reply`, `metrics`, `memory_stats`, `memory_action`
- Browser → server: `user_speech`, `approval_response`, `get_metrics`, `get_memory_stats`, `forget_memory`

The bridge only transports events. `ConversationController` owns request IDs, stream quarantine, timeout cleanup, and fallback reset behavior. `ApprovalController` displays the backend-scoped approval request and emits a real boolean response; the backend remains authoritative.

## Voice/audio boundary

`VoiceInterface` owns browser permission, recognition startup, restart, interruption, and English/Urdu language hints. `TtsInterface` owns `speechSynthesis`, voice selection, and speech lifecycle events. `AudioInterface` exposes the analyser boundary for future audio-reactive rendering; it is disabled for the current reference-image presentation so this architecture change does not alter the visuals.

## Lifecycle

`createHudApp().destroy()` tears down Socket.IO listeners, speech output, recognition, microphone tracks, timers, accessibility state, and UI state. The `jarvisHud` global is only a deliberate lifecycle/debug handle; frontend modules communicate through the composition root rather than hidden globals.
