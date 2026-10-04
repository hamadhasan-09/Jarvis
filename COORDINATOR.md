# Coordinator

Set `JARVIS_COORDINATOR_ENABLED=true` to coordinate replies for task types
listed in `JARVIS_COORDINATOR_TASK_TYPES` (default: `REASONING,RESEARCH,CODING`).
The flag defaults to off. Candidate models are discovered from the configured,
enabled adapters in `ProviderManager.providers`, with at most three candidates
by default (`JARVIS_COORDINATOR_MAX_CANDIDATES` controls the cap).
When enough larger models are registered, smaller/fast models are reserved for
the final synthesis pass.

Candidate calls run concurrently through `ProviderManager.generate()`. Each
successful response is stored as a `model_response` artifact. With two or more
successes, a configured small/fast model synthesizes a single answer; model
identities are kept out of the synthesis prompt. The final response and
candidate producer names are returned in `ProviderResponse.metadata`.

Artifacts live in the in-memory `ArtifactBus`. Background jobs can call the same
coordinator using the `run_coordinator_task` job type. The job runtime persists
the final result; intermediate artifacts are regenerated if a running job must
retry after restart. There is no UI for artifact inspection yet.
