# ADR-0002: Custom Agent Runtime

## Status

Accepted for documentation foundation.

## Context

ANUM requires agents that can use memory, call tools, coordinate approvals, respect tenant policy, stream progress, and resume long-running work. Existing agent frameworks can be useful references, but the core runtime must enforce ANUM's own authorization, audit, risk, and product semantics.

## Decision

ANUM will build a custom agent runtime. The runtime will own task state, run steps, model gateway calls, skill selection, memory retrieval, tool mediation, approval pause/resume, event emission, and audit records. External frameworks may be used selectively for narrow utilities, but they must not become the source of truth for authorization, state, or policy.

## Rationale

A custom runtime allows ANUM to make safety and governance non-optional. Tool calls can be checked outside the model. Memory can be filtered by tenant and permissions. Approval waits can be durable. Model provider details can stay behind the model gateway. Task traces can be shaped for the product rather than inherited from a generic framework.

## Consequences

The custom runtime increases implementation responsibility. ANUM must build tests, structured state, clear interfaces, worker behavior, failure handling, and observability. This is acceptable because these capabilities are central to the product.

## Now

Start with a small runtime: create a task, run one agent loop, call a model adapter, mediate a tool, pause for approval, resume, persist steps, and emit events.

## Later

Add multi-agent collaboration, planner and executor separation, local desktop tool execution, skill distribution, evaluation harnesses, and advanced policy simulation.