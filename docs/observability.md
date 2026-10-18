# Observability

ANUM needs strong observability because agent systems fail in ways that are hard to diagnose from logs alone. Observability covers application behavior, model calls, tool execution, workflows, events, costs, and user-visible task state.

## OpenTelemetry

OpenTelemetry is the instrumentation standard for traces, metrics, and logs. Every request, task, agent run, workflow, model call, tool call, approval, and integration event should carry correlation identifiers.

## Signals

- Logs: structured, redacted, and correlated.
- Metrics: latency, error rates, queue depth, workflow retries, model cost, token usage, tool failures, approval wait time, and tenant quotas.
- Traces: request paths across API, runtime, model gateway, tools, events, and Temporal workflows.
- Audit records: security and compliance events stored separately from operational logs.

## Redaction

Prompts, memory contents, file contents, credentials, and personal data must not appear in routine logs or telemetry. Debug payload capture must be opt-in, scoped, time-limited, and tenant-aware (not built).

## Implementation

`services/api/anum_api/telemetry.py` wires the OpenTelemetry SDK into the API and the Temporal worker. Packages are pinned in `services/api/pyproject.toml` (`opentelemetry-api`/`-sdk`/`-exporter-otlp-proto-http` 1.45.1, `opentelemetry-instrumentation-fastapi`/`-httpx`/`-sqlalchemy` 0.66b1).

### Turning export on

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP/HTTP base URL, e.g. `http://otel-collector:4318` in compose. Unset means no export: the instruments are no-ops. `/v1/traces`, `/v1/metrics` and `/v1/logs` are appended. |
| `OTEL_SDK_DISABLED=true` | unset | Turns export off even when an endpoint is set. |
| `ANUM_OTEL_SERVICE_NAME` | `anum-api` / `anum-worker` | `service.name` resource attribute. |
| `ANUM_OTEL_METRIC_EXPORT_INTERVAL_SECONDS` | 15 | Metric push interval. |
| `ANUM_OTEL_LOGS_ENABLED` | true | Also ship Python log records over OTLP (stdout logging is unchanged). |
| `ANUM_OTEL_TRACES_SAMPLER_RATIO` | 1.0 | Parent-based head sampling ratio for new traces. |

The resource carries `service.name`, `service.namespace=anum`, `service.version` and `deployment.environment.name`; nothing tenant-specific. Compose's `api` and `worker` services set the endpoint to the collector.

### Traces

| Span | Source | Notable attributes |
|---|---|---|
| `GET /api/v1/tasks/{task_id}` (server) | FastAPI instrumentation; `/health` excluded | `http.route` template, status, `anum.tenant_id`, `anum.workspace_id` (set by the `tenant_context` dependency) |
| `generate_text <model>`, `generate_structured <model>`, `stream_text <model>` (client) | `model_gateway.py` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.usage.cache_creation.input_tokens`/`cache_read.input_tokens` (when non-zero), `anum.model.estimated_cost_usd`, `anum.model.attempts`, `error.type` |
| `POST` outbound HTTP (client) | httpx instrumentation (model calls, JWKS, webhooks) | URL without query string |
| SQL statements | SQLAlchemy instrumentation (API session engine, outbox relay engine) | statement text with bind placeholders, never values |
| `publish ANUM_EVENTS` (producer) | `NatsJetStreamBus.publish` | `messaging.system=nats`, stream, `messaging.message.id`; `traceparent` is injected into the NATS headers. The subject (it embeds tenant tokens) is not recorded. |
| `StartWorkflow`, `RunWorkflow`, `RunActivity:anum.advance_run` | Temporal `TracingInterceptor` on the API dispatcher's and the worker's client | trace continues from the API request into the worker; the activity span gets `anum.tenant_id`/`anum.workspace_id` |

Temporal spans also carry the workflow id (`anum-run/<tenant>/<workspace>/<task>`), which holds opaque ids only.

### Metrics

OpenTelemetry names and their Prometheus names after the collector's Prometheus exporter (unit suffix, `_total` for counters, dots to underscores). Metric attributes never include tenant, workspace, user, task or client identifiers (cardinality and privacy); per-tenant cost views are a later item.

| OpenTelemetry instrument | Prometheus series | Attributes |
|---|---|---|
| `anum.http.server.request.duration` (histogram, s) | `anum_http_server_request_duration_seconds_{bucket,count,sum}` | `http.request.method`, `http.route` (template, `unmatched` when nothing matched), `http.response.status_code`. Recorded by `HttpMetricsMiddleware`, the outermost middleware, so 413/429 answers count too. Count is the request rate; 5xx are errors. |
| `anum.rate_limit.rejections` | `anum_rate_limit_rejections_total` | `anum.rate_limit.backend` (`memory`, `valkey`) |
| `anum.model.call.duration` (histogram, s) | `anum_model_call_duration_seconds_*` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.operation.name`, `anum.model.status` (`ok`/`error`), `error.type` |
| `anum.model.tokens` | `anum_model_tokens_total` | provider, model, `gen_ai.token.type` (`input`/`output`) |
| `anum.model.cache.tokens` | `anum_model_cache_tokens_total` | provider, model, `anum.model.cache.operation` (`write`/`read`). Prompt-cache input tokens ([Anthropic](model-gateway.md#anthropic-messages-api)); already counted in `input` above. |
| `anum.model.estimated_cost_usd` | `anum_model_estimated_cost_usd_total` | provider, model. Estimate from the price table ([Model gateway](model-gateway.md)); mock and Ollama cost 0. |
| `anum.model.budget.thresholds` | `anum_model_budget_thresholds_total` | `anum.budget.scope` (`tenant`, `workspace`), `anum.budget.kind` (`cost`, `tokens`), `anum.budget.percent` (`80`, `100`). A model call crossed that share of a monthly budget ([Model gateway](model-gateway.md#monthly-budgets)); the matching `model_budget_threshold` log line names the tenant and workspace. |
| `anum.model.budget.rejections` | `anum_model_budget_rejections_total` | `anum.budget.scope`, `anum.budget.kind`. Model calls refused because a budget was used up. |
| `anum.outbox.backlog` (gauge) | `anum_outbox_backlog` | `anum.outbox` (`postgresql` relay or in-process `memory` outbox). Committed, unpublished, not parked events. |
| `anum.outbox.oldest_unpublished_age` (gauge, s) | `anum_outbox_oldest_unpublished_age_seconds` | `anum.outbox` |
| `anum.outbox.parked` (gauge) | `anum_outbox_parked` | `anum.outbox`. Events marked unpublishable (`publish_next_attempt_at = 'infinity'`). |
| `anum.outbox.published`, `.publish_failures`, `.rejected` | `anum_outbox_published_total` etc. | `anum.outbox` |
| `anum.run_lock.contention` | `anum_run_lock_contention_total` | `anum.lock.outcome` (`busy`, `unavailable`), `anum.lock.source` (`api`, `worker`) |
| `anum.temporal.activity.outcomes` | `anum_temporal_activity_outcomes_total` | `temporal.activity.type`, `anum.activity.outcome` (`advanced`, `locked`, `not_visible_yet`, `not_found`, `coordination_unavailable`, `cancelled`, `application_error`, `error`) |
| `anum.temporal.activity.duration` (histogram, s) | `anum_temporal_activity_duration_seconds_*` | as above |

The PostgreSQL backlog gauges come from one query as the `anum_outbox_relay` role (it only sees unpublished rows), run from the relay loop at most every 15 seconds, also while NATS is down; the metric export thread never touches the database. Every API replica and worker reports the same backlog, so dashboards and alerts take `max`. The httpx and SQLAlchemy instrumentations add their standard client metrics (`http.client.*`, `db.client.connections.usage`).

### Logs and correlation

`install_log_correlation()` (always on, API and worker) adds `anum_trace_id`, `anum_span_id` and `anum_correlation_id` (the request's `X-Correlation-ID`) to every `LogRecord`, `-` when absent. The `anum_` prefix avoids clashing with `extra=` keys callers already pass. The worker's log format prints them (`telemetry.LOG_FORMAT`).

With export on, `OtlpLogHandler` on the root logger ships records with a fixed attribute set: logger name, function, line, correlation id, and for exceptions the type and stack frames only. It never copies `extra=` fields or exception messages, which can echo request text. Loki links each line to its trace through the `trace_id` structured metadata.

### Redaction rules

`RedactingSpanExporter` wraps the span exporter and rewrites every finished span, whatever library produced it: `http.url`, `url.full` and `http.target` lose query string, fragment and userinfo (memory search text arrives as `?query=`; webhook URLs can carry keys); `url.query`, client and peer addresses and any credential-like header attribute become `[redacted]`; exception events keep `exception.type` and stack frames but not the message; error statuses keep the code and drop the description (it is `str(exception)`). Redaction runs at export because instrumentations set most attributes after the span starts. The collector repeats the URL, address and header rules (`transform/redact`, `attributes/redact` in `infra/docker/otel-collector.yaml`) for any other emitter.

ANUM's own spans and metrics are built from metadata only. Tests in `services/api/tests/test_telemetry.py` drive real requests, model calls (with a prompt and reply marked `PRIVATE`), NATS publishes, outbox passes, lock contention and activities through in-memory exporters and assert both what is recorded and that no prompt, reply, query string, key, user id or client address is exported; one test runs the API in a subprocess against a local OTLP/HTTP endpoint and checks the protobuf payloads on the wire.

### Error reporting

`services/api/anum_api/error_reporting.py` optionally sends unhandled errors, and log records at `ERROR` and above, from the API and the Temporal worker to a Sentry-compatible server: GlitchTip on the [Oracle VM](monitoring-oracle.md#glitchtip), or any hosted Sentry-compatible endpoint. It uses `sentry-sdk` 2.71.0 (pinned in `pyproject.toml`). Tracing stays with OpenTelemetry: the SDK runs without performance tracing, sessions or log shipping.

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_SENTRY_DSN` | empty (off) | `https://<key>@<host>/<project id>`. Outside `local`, plain `http` is allowed only to a private service name without dots, for example `http://<key>@glitchtip:8000/1` on the compose network. The startup policy, and the Helm chart through `infra/helm/ci/lint.sh`, refuse anything else. A malformed DSN is refused at startup in every environment, and the error never echoes the key. The DSN carries the project's ingest key: on Kubernetes, put it in the `anum-app` Secret (`secrets.app.optional` lists it, so the chart injects it into the API and the worker) rather than in `config`, which renders into a ConfigMap. The chart cannot read a Secret, so a DSN there is checked only by the API's startup policy; a DSN in `config` or `extraEnv` is refused by `helm template` as well. |

An event keeps:

- the exception type and stack frames (file, function, line);
- the route, the request method and the URL path;
- log message templates;
- `environment` and `server_name` (`anum-api`/`anum-worker`);
- two tags: `anum.tenant_id` (set per API request by the tenant dependency) and `anum.correlation_id` (matches the log lines).

Removed before sending, whatever the integration that produced the event:

- exception messages, which can echo request text;
- local variables and source lines;
- request bodies, query strings, cookies, and every header except `Content-Type` and `Content-Length`;
- client addresses and user identity (`send_default_pii=False`, and `user` is dropped);
- log arguments, formatted log messages and `extra=` fields;
- contexts other than runtime, OS and trace;
- breadcrumb data other than method, status code and query-less URL.

The worker sends no tenant tag, because its activities share one SDK scope. `tests/test_error_reporting.py` runs the real SDK against a FastAPI route and checks that none of these values leave the process.

#### Browser errors (web app)

The web app can report its own unhandled errors and unhandled promise rejections to a Sentry-compatible project (GlitchTip on the [Oracle VM](monitoring-oracle.md#web-errors)). It is off unless the bundle is built with `VITE_SENTRY_DSN`. A build without it contains no reporting code: the branch and the SDK chunk are removed at build time, and nothing is loaded or sent. With a DSN, `@sentry/browser` 11.8.0 (pinned exactly in `apps/web/package.json`) is loaded in its own chunk after the first render. Errors thrown before it loads are not reported.

| Build setting | Default | Meaning |
|---|---|---|
| `VITE_SENTRY_DSN` | empty (off) | The browser project's public DSN, `https://<public key>@<host>/<project id>`. Plain `http` is accepted only for `localhost`. A malformed value fails `vite build` (`apps/web/vite.config.ts`), and the error never echoes the key. The key is public by design: it is in the bundle every visitor downloads, and it can only submit events. |
| `VITE_SENTRY_ENVIRONMENT` | `production` | The event's `environment` (the Oracle compose passes `ANUM_ENVIRONMENT`). |

Every deploy path that builds the web image passes the DSN as an optional build argument; leaving it unset keeps reporting off:

| Deploy path | Where the DSN comes from | Environment |
|---|---|---|
| Oracle VM | `ANUM_WEB_SENTRY_DSN` in `.env` ([monitoring](monitoring-oracle.md#web-errors)) | `ANUM_ENVIRONMENT` |
| Render | `anum-web`'s `VITE_SENTRY_DSN` (`render.yaml`, `sync: false`; [Render](deploy-render.md)) | `staging` |
| Helm staging and production (`deploy-staging.yml`, `deploy-production.yml`) | repository variables `STAGING_WEB_SENTRY_DSN`, `PRODUCTION_WEB_SENTRY_DSN` ([deployment](deployment.md)) | `staging`, `production` |

The DSN is public (it ships in the bundle), so it is a variable, not a secret. Changing it needs a rebuild of the web image. `services/api/tests/test_render_blueprint.py` checks the Render and workflow wiring.

The SDK reports errors only: no tracing, sessions, replay or user feedback. Console and DOM breadcrumbs are off, because console text and clicked element names can hold what someone typed. `apps/web/src/lib/errorReporting.ts` scrubs every event and breadcrumb (`beforeSend`, `beforeBreadcrumb`), with the same rules as the API. An event keeps:

- the exception type and stack frames (file without query, function, line, column);
- the page URL without its query string or fragment;
- `environment`;
- one tag, `anum.tenant_id`.

Removed before sending:

- exception messages, which can echo what someone typed or an API answer;
- user identity, headers (including `User-Agent` and `Referer`), cookies and request data;
- query strings and fragments, which hold the OIDC `code` and `state` after sign-in and invitation tokens;
- source lines and local variables;
- `extra` data, every other tag, and contexts other than browser, OS and trace;
- breadcrumb messages, and breadcrumb data other than method, status code and query-less URL.

A breadcrumb whose URL carries a token (`token`, `code=`, `state=`, `invitation` and similar, including calls to Keycloak's token endpoint) is dropped whole.

**CSP.** The web image's Content-Security-Policy adds the DSN's origin to `connect-src` only when `VITE_SENTRY_DSN` was set. It changes no other directive. `apps/web/Dockerfile` derives the origin at build time with `apps/web/scripts/sentry-csp-origin.ts`, the same validation as the bundle, and writes it into the nginx template. The runtime `ANUM_CSP_CONNECT_SRC` stays the API and Keycloak. Tests: `apps/web/test/errorReporting.test.ts` (scrubbing and DSN validation), `apps/web/e2e/error-reporting.spec.ts` (a build with a DSN sends a scrubbed event with the tenant tag only; the default build loads no reporter and sends nothing), and `tests/test_oracle_compose.py` (build argument, CSP placeholder, origin script).

### Local stack

`infra/docker/compose.yaml` keeps the `otel-collector` service in the default stack (debug exporter only) and adds an `observability` profile:

```bash
ANUM_OTELCOL_OVERLAY=observability docker compose -f infra/docker/compose.yaml --profile observability up
```

`ANUM_OTELCOL_OVERLAY=observability` merges `infra/observability/otel-collector/observability.yaml` over the base collector config, which sends traces to Tempo (`grafana/tempo:3.1.0` in monolithic mode, port 3200; retention runs as backend-worker jobs, and search leaves out the last 30 seconds), exposes metrics on `:8889` for Prometheus (`prom/prometheus:v3.5.5`, port 9090, rules in `infra/observability/prometheus/alerts.yaml`), and sends logs to Loki (`grafana/loki:3.7.8`, port 3100). Grafana (`grafana/grafana:13.2.3`, http://localhost:3000, anonymous viewer; the Prometheus, Tempo and Loki plugins ship bundled in the image, so `GF_PLUGINS_PREINSTALL_DISABLED` does not affect them) is provisioned from `infra/observability/grafana/provisioning` with Prometheus, Tempo and Loki data sources (trace to logs and log to trace links) and the dashboards in `infra/observability/grafana/dashboards`. Without the variable the profile still starts, but the collector keeps printing to its own log and Prometheus has nothing to scrape (`AnumTelemetryPipelineDown`). Storage is local disk with short retention: development only.

Validated locally: `otelcol-contrib validate` (0.110.0) for both overlays, `promtool check config`/`check rules`/`test rules`, `loki -verify-config`, Tempo started with its config, `docker compose --profile observability config`, and the whole profile up with the API exporting to it (dashboards provisioned, every dashboard query accepted by Prometheus, traces searchable in Tempo, logs queryable in Loki). The Tempo 3.1.0 upgrade was checked without Docker: `tempo -config.verify` passes, and the image's binary run with this config accepted an OTLP trace and returned it by ID and by TraceQL search; the full profile has not been started on 3.1.0 yet. Grafana 13.2.3 was checked the same way: its binary, run with the compose environment and this provisioning, loaded the three data sources with their bundled plugins and the three dashboards in the ANUM folder (anonymous viewers can read, not write).

The compose collector is now `otel/opentelemetry-collector-contrib:0.162.0` (October 2026 dependency batch). The configs keep the `otlp` and `otlphttp` exporter names, which collector 0.144 and later still accept as deprecated aliases of `otlp_grpc` and `otlp_http`; re-run `otelcol-contrib validate` with 0.162.0 before relying on the overlay.

### Dashboards

| Dashboard (uid) | Panels |
|---|---|
| ANUM API: errors and latency (`anum-api`) | 5xx ratio, requests/s, 429s, p95; 5xx and 4xx by route and status; requests by route; p50/p95/p99; p95 by route. Streaming routes are excluded from latency. |
| ANUM events and runs: queue depth (`anum-queues`) | outbox backlog, oldest unpublished age, parked, publish outcomes; Temporal activity outcomes and p95; run lock contention. |
| ANUM model gateway: cost and usage (`anum-model-cost`) | spend last hour and day, spend per hour by model, last hour versus the previous day's hourly average, tokens by model and type, p95 by model, calls by status. |

They are generated JSON (schema 39) and read-only in Grafana: change them in the repository.

### Alerts

Rules in `infra/observability/prometheus/alerts.yaml`, unit-tested by `alerts.test.yaml` (`promtool test rules`). Thresholds are proposals until real traffic exists. Each links its section of the [Runbooks](runbooks.md).

| Alert | Condition | Severity |
|---|---|---|
| `AnumApiHighErrorRate` | 5xx above 5% of requests for 10 minutes (and over 0.1 req/s) | page |
| `AnumApiHighLatencyP95` | p95 above 1 s for 10 minutes, streaming routes excluded | ticket |
| `AnumRateLimitRejectionsHigh` | over 1 rejection/s for 15 minutes | ticket |
| `AnumOutboxBacklogStale` | oldest unpublished event older than 5 minutes for 5 minutes | page |
| `AnumOutboxBacklogLarge` | over 1,000 waiting events for 15 minutes | ticket |
| `AnumOutboxParkedEvents` | any parked event for 15 minutes | ticket |
| `AnumTemporalActivityFailures` | over 10% of activities end in error/not found/unavailable for 15 minutes | ticket |
| `AnumRunCoordinationUnavailable` | run locks cannot reach Valkey for 5 minutes | page |
| `AnumModelCostSpike` | last hour's spend over 3x the previous day's hourly average and over $1 | ticket |
| `AnumModelHourlySpendHigh` | over $10 in an hour (placeholder budget) | page |
| `AnumModelErrorRate` | over 20% of a provider's calls fail for 10 minutes | ticket |
| `AnumTelemetryPipelineDown` | Prometheus cannot scrape the collector for 5 minutes | ticket |

No Alertmanager is configured: alerts show in Prometheus and Grafana only until a paging provider is chosen.

## Now

In place: request, model, outbox, coordination and Temporal metrics; traces across API, model gateway, outbound HTTP, SQL, NATS publish and Temporal; correlated and redacted logs; optional scrubbed error reporting to GlitchTip or another Sentry-compatible server ([Error reporting](#error-reporting)); the local backend stack with dashboards and tested alert rules; [Runbooks](runbooks.md) for each alert.

## Later

Needs a cloud choice: a managed or self-hosted production backend (the collector config is the switch point), Alertmanager routing to an on-call tool, retention and access control on telemetry stores, SLOs and error budgets. Also open: NATS consumer spans (the `traceparent` header is already sent), tool-call and approval-wait metrics, per-tenant cost views with bounded cardinality, sampling for production volume, opt-in debug payload capture, anomaly detection and evaluation metrics.
