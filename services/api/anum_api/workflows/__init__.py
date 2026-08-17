"""Temporal-backed durable execution for task runs (see docs/automation.md, docs/agent-runtime.md).

Opt-in via `settings.temporal_address` (env `ANUM_TEMPORAL_ADDRESS`). When
unset, nothing in this package is imported by the request path and
POST /tasks/{id}/run behaves exactly as it always has - a synchronous
in-process call into AgentRuntime. When set, task runs become Temporal
workflows: the mock model call, the approval pause/resume, and cancellation
all happen as durable workflow activities/signals instead, so a run
survives a worker restart instead of being lost mid-flight.
"""
