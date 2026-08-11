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

## Guardrails

The runtime must mediate every tool call. It should reject tools outside scope, redact sensitive context where possible, cap costs, time out long operations, and keep a clear audit trail. Prompt injection should be handled by isolating untrusted content from instructions and by enforcing tool policy outside the model.

## Now

Implement one agent runner with structured steps, persisted runs, model gateway calls, tool mediation, approval pause/resume, and event emission.

## Later

Add multi-agent delegation, planner/executor separation, skill marketplaces, specialized workers, local desktop tools, richer evaluation, and policy-aware self-reflection.