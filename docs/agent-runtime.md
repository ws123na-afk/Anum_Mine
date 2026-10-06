# Agent Runtime

The ANUM agent runtime is a custom orchestration layer responsible for turning user intent into safe, observable work. It is not just a wrapper around a model API. It owns task state, planning, memory retrieval, tool mediation, approvals, event emission, and recovery.

## Core Concepts

- Task: the user-facing unit of work.
- Agent run: one execution attempt for a task.
- Step: a model call, tool proposal, tool execution, memory operation, approval wait, or final response.
- Skill: reusable capability package that teaches the agent how to perform a class of work.
- Tool: controlled function or integration endpoint invoked through the runtime.
- Policy: rules that decide what context, memory, tools, and actions are allowed.

## Execution Loop

A run should load tenant policy, user context, task history, relevant memory, available skills, and tool definitions. The model gateway produces reasoning artifacts or structured actions. The runtime validates actions, executes safe tools, pauses for approvals when needed, writes durable state, emits events, and streams progress.

## State and Recovery

Agent state should be durable enough to resume after worker restarts. Temporal should manage long-running execution, retries, timers, and waits. PostgreSQL should store canonical run state and audit records. Valkey can hold ephemeral locks, short-lived caches, and live coordination data.

## Durable Execution

`ANUM_RUNTIME_BACKEND` picks where a run executes:

| Value | Behaviour |
|---|---|
| `inline` (default) | `POST /api/v1/tasks/{id}/run` plans and executes inside the request, as before. |
| `temporal` | The request saves the task as `queued` with a run at the `planning` checkpoint (its first step, `queued`, records the Temporal workflow id) and starts `AgentRunWorkflow`. A worker (`python -m anum_api.worker`) executes it. The response returns the queued run; progress arrives through events. |

The workflow (`anum_api/temporal_workflow.py`) does no I/O. It repeatedly calls one activity, `anum.advance_run` (`anum_api/durable_runs.py`), which opens a tenant-scoped unit of work (RLS tenant and workspace set, events published only after commit), loads the run's persisted checkpoint and moves it one step:

| Checkpoint phase | Step |
|---|---|
| `planning` | Plan (model call, tool policy) and commit `tool_ready`, `waiting_approval` or `failed`. A crash before the commit just plans again. |
| `tool_ready`, or `waiting_approval` with a decided approval | Re-check policy and commit `executing`; then, in a second transaction, run the tool and commit `completed` (or `failed` for a rejected or newly blocked action). |
| `executing` at the start of a call | A previous worker stopped mid-tool. A tool declared `idempotent` that policy still allows without approval runs again (at-least-once). Anything else, including every approved high-risk action, is never repeated: the run fails with an "outcome unknown" step so a person decides. |
| `waiting_approval` (pending), terminal, or cancelled | No change. |

Workflow ids are `anum-run/<tenant>/<workspace>/<task>`, so a task never has two live workflows; starting an existing one reuses it. Every activity input carries the tenant, workspace, user and roles explicitly. Approve/reject in `temporal` mode commits the decision and signals `approval_decided`; the worker applies it. If the signal is lost the workflow re-reads the approval after `approval_poll_seconds` (one hour). Cancel signals `cancel` and the next step settles the run as `cancelled`. `POST /api/v1/agent-runs/{id}/resume` (re)starts the workflow for a stranded `tool_ready` run instead of executing inline. Activities retry with exponential backoff (1 s to 60 s); `RunNotFound` (the run is not in the caller's workspace) is not retried.

Because a step always starts from what the repository holds, a run survives a worker restart: Temporal retries the activity on another worker, which continues from the checkpoint without re-planning. The worker needs `ANUM_REPOSITORY_BACKEND=postgresql` outside local (the in-memory repository is per process) and the same `ANUM_*` configuration as the API; it refuses the same insecure configurations at startup. Settings: `ANUM_TEMPORAL_TARGET` (`localhost:7233`), `ANUM_TEMPORAL_NAMESPACE` (`default`), `ANUM_TEMPORAL_TASK_QUEUE` (`anum-agent-runs`).

### Run Locks

`ANUM_RUN_LOCK_BACKEND=valkey` adds a distributed lock per task (`anum:lock:tenants:<tenant>:workspaces:<workspace>:tasks:<task>`) around run, resume and approval decisions in the API and around every worker step. A busy task answers `409`, an unreachable Valkey `503`; workers retry. Locks are `SET NX PX` with a random token; release and extend are compare-and-act scripts, so a holder whose TTL (`ANUM_RUN_LOCK_TTL_SECONDS`, 300) lapsed cannot remove someone else's lock. `ANUM_RUN_LOCK_WAIT_SECONDS` (0) waits for a holder instead of failing fast. The default `none` keeps the database row lock as the only guard. See `anum_api/valkey.py`.

### Tests

`tests/test_durable_runs.py` drives the activity without a server, including a simulated worker crash mid-tool and the high-risk never-repeat rule. `tests/test_temporal_worker.py` (marker `temporal`) runs the workflow on a real server (`ANUM_TEST_TEMPORAL_TARGET`, or a dev server the SDK starts with `WorkflowEnvironment.start_local`, downloading the Temporal CLI or using `ANUM_TEST_TEMPORAL_CLI`) and stops a worker while its tool call is in flight, then asserts a second worker completes the run without re-planning. `tests/test_valkey_integration.py` (marker `valkey`, `ANUM_TEST_VALKEY_URL`) includes a lock contention test across threads and connections.

## Guardrails

The runtime must mediate every tool call. It should reject tools outside scope, redact sensitive context where possible, cap costs, time out long operations, and keep a clear audit trail. Prompt injection should be handled by isolating untrusted content from instructions and by enforcing tool policy outside the model.

## Now

One agent runner with structured steps, persisted runs, model gateway calls, tool mediation, approval pause/resume and event emission, executable inline or as a durable Temporal workflow with optional Valkey run locks.

## Later

Add multi-agent delegation, planner/executor separation, skill marketplaces, specialized workers, local desktop tools, richer evaluation, and policy-aware self-reflection.