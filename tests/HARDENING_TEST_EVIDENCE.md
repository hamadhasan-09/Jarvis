# JARVIS backend hardening test evidence

Run date: 2026-09-25

## Focused hardening suite

Command:

```text
venv\Scripts\python.exe tests\test_autonomous_engine_hardening.py
```

Result:

```text
Ran 7 tests in 0.092s
OK
```

Coverage includes:

- original execution exception and model preservation
- sandbox routing and absence of `shell=True`
- fail-closed approval behavior and explicit risk policy
- dependency ordering
- unknown dependency rejection
- failed-prerequisite blocking of dependents
- existing SOP profile and planner/executor compatibility

## Existing multi-LLM and knowledge suite

Command:

```text
venv\Scripts\python.exe tests\test_multillm_knowledge_upgrade.py
```

Result:

```text
ALL_UPGRADE_TESTS_PASS 11
```

This suite covered provider fallback and timeouts, deterministic routing,
current-information retrieval, memory safety, document ingestion and bounded
retrieval, verification contracts, optional-provider startup, empty web-result
policy, HTTP/Socket.IO routing, explicit memory save/recall/forget, and
insufficient-knowledge behavior.

## Live local smoke test

The updated server returned HTTP 200 and the sequential Socket.IO smoke test
received two replies from the configured Groq provider. The client used polling
transport because `websocket-client` is not installed in the environment.

The smoke harness now waits for the first `jarvis_reply` before sending the
second request, so the test is genuinely sequential.

## Stabilization regression suite

Command:

```text
venv\Scripts\python.exe tests\test_stabilization_regressions.py
```

Result:

```text
ALL_STABILIZATION_REGRESSIONS_PASS 11
```

This suite covers HUD JavaScript parsing, sandbox escape primitives, scoped
strict approval, fail-closed verification, stream reset/fallback isolation,
live PromptDefense and rate limiting, multilingual current-information
routing, privacy redaction, knowledge replacement/deletion lifecycle, startup
contracts, and live security guards before provider execution.
