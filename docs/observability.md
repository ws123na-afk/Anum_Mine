# Observability

ANUM needs strong observability because agent systems fail in ways that are hard to diagnose from logs alone. Observability should cover application behavior, model calls, tool execution, workflows, events, costs, and user-visible task state.

## OpenTelemetry

OpenTelemetry should be the default instrumentation standard for traces, metrics, and structured logs. Every request, task, agent run, workflow, model call, tool call, approval, and integration event should carry correlation identifiers.

## Signals

- Logs: structured, redacted, and correlated.
- Metrics: latency, error rates, queue depth, workflow retries, model cost, token usage, tool failures, approval wait time, and tenant quotas.
- Traces: request paths across API, runtime, model gateway, tools, events, and Temporal workflows.
- Audit records: security and compliance events stored separately from operational logs.

## Redaction

Prompts, memory contents, file contents, credentials, and personal data should not appear in routine logs. Debug payload capture must be opt-in, scoped, time-limited, and tenant-aware.

## Now

Add trace IDs, request logging, model usage records, tool execution metrics, and task timeline visibility.

## Later

Add SLOs, alerting, dashboards, anomaly detection, tenant cost views, evaluation metrics, and incident runbooks.