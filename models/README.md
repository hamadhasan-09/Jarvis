# Legacy model package

The active runtime uses `llm/ProviderManager`, `llm/TaskRouter`, and the provider
implementations under `llm/`. The classes in this directory are retained only
for archived scripts and compatibility; `ui/server.py`, the engine, and tests
must not import them. New provider or routing work belongs in `llm/`.
