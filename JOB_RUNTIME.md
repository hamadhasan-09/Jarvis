# Durable background jobs

`engine.job_runtime.JobRuntime` provides a single-worker SQLite queue stored at
`data/jobs.sqlite3`. Jobs and routines survive process restarts. The worker
leases due jobs atomically, retries ordinary failures up to the configured
attempt limit, and reclaims expired leases after a crash. Routine occurrences
are created and advanced in one transaction, so a restart cannot duplicate a
single due occurrence. Interval schedules use `interval:<seconds>`; daily
`cron_like` schedules use `daily:HH:MM` in UTC.

The built-in handlers are `run_prompt` (`{"prompt": "..."}`) and
`run_orchestrator_objective` (`{"objective": "..."}`). Tool actions from an
orchestrator job continue through `Executor` and `Toolchain`; the broker adds
the stable job ID to every authorization audit context made in that worker
scope.

When the browser approval gateway has a connected browser, a job requiring
confirmation uses the existing approval surface and persists
`awaiting_approval` before emitting the request. On restart, pending approvals
are never re-issued automatically. They remain waiting until the configured
stale interval (default five minutes) elapses, then fail with a stale-approval
reason. If confirmation is required with no connected browser, the job fails
closed immediately with a clear error. This avoids silently skipping approval
or hanging indefinitely. The existing synchronous `SYSTEM_ACTION` route is
unchanged.

Completed jobs are announced through the existing `jarvis_reply` event when a
Socket.IO user session next connects. Routine management is currently through
the Python API; no UI controls or external integrations are included.
