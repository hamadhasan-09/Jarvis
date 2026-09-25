# JARVIS Procedural Core Renderer Plan

Status: design/specification only. No renderer integration is included in this plan commit.
Date: 2026-09-25
Branch: feature/procedural-core-renderer
Base: main at 3529fe57e3dc6c22ddfb6afaa2a1f1a156995213
Repository: hamadhasan-09/Jarvis

## 1. Product requirement

Replace the production PNG-based HUD core with a reusable procedural energy-core component that preserves the approved minimal reference composition:

- dark atmospheric space
- cyan/aqua outer shell and orbital geometry
- concentrated amber inner energy
- axial beam and reflective floor
- restrained particles, filaments, and satellite nodes
- voice-first interaction with no microphone control inside the core
- state communication through deliberate motion, glow, scale, and energy

The supplied reference image is a visual-analysis target only. It must not remain the production renderer.

## 2. Current repository state

- main is at 3529fe5. The local checkout has unrelated uncommitted stabilization/UI work.
- ui/index.html is still the active runtime and currently renders ui/assets/jarvis-reference-core.png plus inline state, voice, Socket.IO, approval, and TTS logic.
- A local, untracked ui/core/ scaffold already exists. It contains a Canvas2D renderer, state mapper, animation controller, orbital system, particle system, audio analyser hook, visual tokens, and a public createJarvisCore() API.
- A local, untracked ui/hud/session.js also exists and appears to duplicate part of the inline HUD session logic. It must be reconciled before integration; do not maintain two session implementations.
- backend/event_contract.md and ui/UI_EVENTS.md define the current browser/backend event boundary.

The existing local scaffold is evidence to review, not automatically approved production code. It is not included in this branch plan commit.

## 3. Technical design

### 3.1 Rendering boundary

Use Canvas2D for the first production implementation because the current scaffold already supports it, the composition is primarily 2D layered light/geometry, and it avoids adding a Three.js/WebGL dependency before measuring need.

CoreRenderer receives only:
- viewport dimensions and device-pixel ratio
- normalized core pose
- normalized audio levels
- orbital state
- particle state
- reduced-motion preference

CoreRenderer must not import or call Socket.IO, LLM/provider code, memory or RAG, tools or sandbox, approval logic, browser speech recognition, or server APIs.

### 3.2 Module responsibilities

Keep one active implementation under ui/core/:
- visualSpec.js: color tokens, layout tokens, easing, state poses, particle/orbit budgets.
- states.js: pure backend/voice-state to core-state mapping.
- math.js: clamp, interpolation, damping, and color helpers.
- CoreRenderer.js: deterministic Canvas2D drawing only.
- AnimationController.js: pose interpolation and transition timing.
- OrbitalSystem.js: reusable orbital geometry and motion state.
- ParticleSystem.js: deterministic particles and sparks with bounded counts.
- AudioReactiveController.js: microphone AudioContext/AnalyserNode input plus a documented future TTS-energy adapter.
- createJarvisCore.js: lifecycle, resize, visibility pause/resume, and public renderer API.
- ui/hud/session.js: the single owner for Socket.IO, speech input/output, permission, approval, and request correlation after duplication with index.html is removed.

### 3.3 Core states

The renderer must support these exact product states:
- IDLE
- LISTENING
- USER_SPEAKING
- THINKING
- JARVIS_SPEAKING
- INTERRUPTED
- ERROR

Each state gets explicit values for scale, glow, shell opacity, orbital speed, ring speed, particle speed, filament energy, core heat, audio response, transition duration, and easing. Values remain centralized in visualSpec.js.

Backend labels such as generating, retrieving, verifying, executing, waiting_confirmation, completed, cancelled, and failed are mapped by states.js; they must not leak into renderer business logic.

### 3.4 Audio behavior

Microphone path:
Microphone stream → AudioContext → AnalyserNode → normalized RMS/speech/bass levels → AnimationController/CoreRenderer.

TTS path:
The browser SpeechSynthesis API does not expose reliable PCM data. Until a TTS implementation provides analyzable audio, JARVIS-speaking motion must be described as state-driven, not falsely called audio-reactive. Keep setTtsEnergy(rms) as an explicit adapter for a future audio-producing TTS implementation.

Permission UI remains outside the core, appears only when needed, and disappears after initialization.

### 3.5 Runtime integration

The migration must preserve the existing event contract:
- status updates backend state.
- jarvis_reply_token updates generation state and request-local stream text.
- jarvis_reply completes the request and drives TTS.
- approval_request opens the existing accessible approval modal.
- connect and disconnect preserve voice-channel recovery.

The renderer receives normalized calls such as:
- setBackendState(name)
- setUserSpeaking(active)
- setJarvisSpeaking(active)
- notifyInterrupted()
- attachMicrophone(stream)
- setTtsEnergy(rms)

No backend orchestration, memory, RAG, security, approval, or provider architecture changes are in scope.

## 4. Migration sequence

### Phase 0 — Baseline and ownership

1. Work only on feature/procedural-core-renderer.
2. Reconcile the dirty local checkout before staging anything.
3. Identify the single active session module; remove or archive duplicate UI session logic only after runtime references are verified.
4. Keep unrelated backend, memory, RAG, and stabilization modifications out of the first procedural-core commit.

### Phase 1 — Design and module checks

1. Review ui/core/ against this specification.
2. Add pure unit tests for state mapping, pose completeness, damping, reduced-motion behavior, and lifecycle cleanup.
3. Add JavaScript module syntax/import checks.
4. Add an event-contract check against backend/event_contract.md and ui/UI_EVENTS.md.

### Phase 2 — Controlled integration

1. Add the procedural canvas to the HUD shell without changing voice/backend behavior.
2. Wire the existing session owner to createJarvisCore().
3. Route all existing backend and speech states through StateController.
4. Attach the microphone stream only after the existing permission flow succeeds.
5. Keep a temporary comparison mode during development only; do not ship a PNG renderer as the production path.

### Phase 3 — Visual/runtime acceptance

1. Launch the real server with start.bat.
2. Load http://127.0.0.1:5000 in the browser.
3. Inspect the rendered core at normal and narrow viewport sizes.
4. Exercise idle, listening, user speaking, thinking, JARVIS speaking, interrupted, error, approval, and recovery states.
5. Grant microphone permission once and verify analyser-driven microphone response.
6. Verify TTS state behavior without claiming raw-audio reactivity unless an analyser source exists.
7. Verify several consecutive voice turns and reconnect/recovery behavior.

### Phase 4 — Performance and cleanup

1. Measure frame time and memory over an extended idle run.
2. Cap device-pixel ratio and particle/orbit budgets.
3. Pause rendering while the tab is hidden.
4. Avoid per-frame allocation where profiling identifies it as material.
5. Confirm reduced-motion behavior and keyboard/screen-reader permission/approval accessibility.
6. Remove the runtime PNG path and stale duplicate session code only after all acceptance tests pass.

## 5. Automated validation

Required before acceptance:
- Node syntax/import checks for every ui/core module.
- Unit tests for state mapping and all required state poses.
- Unit tests for audio-level normalization and no-audio fallback behavior.
- Lifecycle tests for resize, visibility pause/resume, and destroy cleanup.
- UI event-contract tests for status, token, final reply, approval, connect, and disconnect events.
- Regression tests proving voice permission stays outside the core and no microphone button is rendered inside it.
- Existing backend test suites must remain green; no backend behavior should be changed for renderer work.
- Browser/live validation must include screenshots and real interaction, not only DOM or HTTP checks.

## 6. Visual acceptance criteria

The implementation is accepted only when:
- the runtime core is drawn procedurally on Canvas2D or a later measured renderer;
- no production image core is used;
- the reference image remains analysis/test material only;
- the central core visually dominates the page;
- cyan/blue energy surrounds a concentrated amber center;
- motion is restrained and state-specific;
- no dashboard panels, chat transcript, or microphone button appears inside the core;
- the core remains responsive at normal viewport sizes;
- reduced-motion mode remains usable;
- real microphone permission and state transitions work in the running browser;
- no completion claim is made until live runtime evidence is recorded.

## 7. Non-goals

This branch must not:
- redesign the backend;
- change provider routing, memory, RAG, web research, security, approval, or autonomous tools;
- start the 24/7 Windows Home runtime;
- add new employee skills;
- add dashboard panels or persistent transcript UI;
- claim browser SpeechSynthesis gender or TTS PCM support that does not exist;
- maintain a second renderer or second session/event architecture.

## 8. Commit plan

Use focused commits:
1. docs(ui): specify procedural core renderer and state contract
2. feat(ui): integrate procedural core renderer with existing HUD events
3. test(ui): add procedural core and live event regressions
4. perf(ui): profile and tune core rendering only if profiling produces a concrete change.

Review each diff, run automated checks, then run the actual browser before each commit is considered complete. Do not merge or modify main until the visual and runtime acceptance criteria pass.
