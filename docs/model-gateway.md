# Model Gateway

The model gateway abstracts model providers from the rest of ANUM. Agent code should ask for capabilities, policies, and budgets rather than depend directly on one provider SDK.

## Responsibilities

The gateway should handle provider adapters, model selection, request normalization, streaming, retries, timeouts, cost accounting, safety metadata, and provider-specific feature differences. It should also support mock models for tests and local development.

## Adapter Shape

Each adapter should expose a common interface for text generation, structured output, tool-call compatible responses, embeddings, and eventually audio or vision. The gateway should record provider, model, prompt metadata, token counts, latency, error class, and estimated cost for each call.

## Routing Policy

Routing should consider task type, tenant policy, model availability, data sensitivity, latency, cost, and required modality. Early routing can be static configuration. Later routing can become policy-driven and observable, with failover and per-tenant limits.

## Data Handling

Prompts may contain private tenant data. The gateway should support redaction hooks, no-training provider settings when available, tenant-level provider allowlists, and clear logging boundaries. Raw prompts should not appear in normal application logs.

## Now

Build one production provider adapter, one mock adapter, typed request and response objects, streaming support, structured-output validation, usage tracking, and model call audit metadata.

The OpenAI-compatible adapter (also used for Ollama) implements the following today.

### Timeouts and retries

Each HTTP attempt times out after `ANUM_MODEL_TIMEOUT_SECONDS` (default 60; Ollama never less than 120). Failed attempts are retried up to `ANUM_MODEL_MAX_ATTEMPTS` total attempts (default 3) with exponential backoff and full jitter: the wait before retry `n` is a random value between 0 and `min(ANUM_MODEL_RETRY_MAX_SECONDS, ANUM_MODEL_RETRY_BASE_SECONDS * 2^(n-1))` (defaults 8 s and 0.5 s).

- Retried: timeouts, connection and network errors, HTTP 429 and any HTTP 5xx.
- Not retried: every other 4xx (bad request, auth, not found, validation). The provider error is raised immediately.
- `Retry-After` (seconds or HTTP date) on a 429 or 5xx sets the minimum wait. If it asks for longer than `ANUM_MODEL_RETRY_AFTER_MAX_SECONDS` (default 30) the gateway gives up rather than waiting or retrying early.
- Streaming is retried only until the first chunk reaches the caller; after that a retry would duplicate text, so the error is raised.
- When attempts run out, the last error (`httpx.HTTPStatusError` or the transport error) is raised, so callers such as `POST /api/v1/model-config/test` still produce actionable messages.

Chat-completion requests are not idempotent, so a retry after a timeout may be billed twice by the provider. Keep `ANUM_MODEL_MAX_ATTEMPTS` low for expensive models. `ModelCallMetadata.attempts` reports how many attempts a call took.

### Usage and cost accounting

Token counts come from the provider's `usage` object (`prompt_tokens`, `completion_tokens`; a missing or null `usage` counts as zero). `ModelUsage.estimated_cost_usd` is computed from a price table in USD per million tokens:

- Mock and Ollama calls always cost `0`.
- Hosted models are looked up by exact name, then by the longest table entry that prefixes the returned model name at a `-` boundary, so `gpt-4.1-mini-2025-04-14` uses the `gpt-4.1-mini` price.
- Unknown hosted models report `null` rather than guessing.
- The built-in table covers `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-4o` and `gpt-4o-mini` at published list prices. Override it with `ANUM_MODEL_PRICES`, a JSON object such as `{"gpt-4.1-mini": {"input_per_million": 0.4, "output_per_million": 1.6}}`. Setting the variable replaces the whole table.

The runtime already stores `usage` (including the estimate) in each run step's metadata. Estimates are for budgeting and dashboards, not invoicing.

### Redacted logging

Every call emits one `model_call` record on the `anum.model_gateway` logger. Successful calls log at INFO and failures at WARNING. Each retry also logs a `model_call_retry` WARNING. The fields are also attached as structured `extra` (`anum_model_call`, `anum_model_retry`):

`provider`, `model`, `operation`, `status` (`ok` or `error`), `attempts`, `latency_ms`, `http_status`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `error_class`.

Prompts, response text, API keys, request headers, base URLs and exception messages are never logged. Errors are reduced to their class name because provider error bodies can echo the prompt. Tests capture all log records for successful, failed and streaming calls and assert that neither the prompt, the response, nor the key appears.

The same fields feed OpenTelemetry ([Observability](observability.md#metrics)): a client span per call (`generate_text <model>` and so on, with token counts, cost and attempts, never text), the `anum.model.call.duration` histogram, and `anum.model.tokens` and `anum.model.estimated_cost_usd` counters by provider and model. The mock provider reports its token counts at zero cost. Failed calls mark the span as an error with the exception class only; `tests/test_telemetry.py` asserts no prompt, reply or key reaches any exported span or metric.

## Per-workspace model configuration

`PUT /api/v1/model-config` saves a workspace's provider, model, base URL and (for hosted providers) API key. `GET` returns the configuration with `credential_configured` and a `credential_hint` (last four characters) only. The key is never returned. Every read and write is scoped by the request's explicit tenant and workspace context.

- **Local and tests (`ANUM_REPOSITORY_BACKEND=memory`):** an in-memory store, lost on restart.
- **PostgreSQL (`ANUM_REPOSITORY_BACKEND=postgresql`):** the `workspace_model_configs` table (migration `0005_workspace_model_configs`), keyed by `(tenant_id, workspace_id)` with a foreign key to `workspaces`.
  - Row level security is enabled and forced, using the same `anum.tenant_id` / `anum.workspace_id` policy as the other tenant tables. The store sets that context on its own transaction before every query.
  - Saving for a workspace that has not finished onboarding returns `409`.
  - A check constraint refuses an `openai_compatible` row without a stored credential.

**Encryption at rest.** The API key is stored only as Fernet ciphertext (`api_key_ciphertext`), plus the display hint. Display reads never decrypt; decryption happens only when building the workspace's gateway or running the connection test. `ANUM_SECRETS_KEY` holds one or more comma-separated Fernet keys. The first key encrypts and all keys decrypt, so a key can be rotated by prepending the new one and later re-saving configurations. Generate a key with:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The setting is required whenever `ANUM_ENVIRONMENT` is not `local` or `test`: the API refuses to start without a valid key, and the startup error never echoes setting values. In `local` or `test` without a key, a fixed development key is derived and a warning is logged. That key protects nothing, so never use local mode with real credentials you care about. Losing the key makes stored provider keys unrecoverable; re-enter them in Settings.

**Gateway cache.** `workspace_model_gateway` reads the workspace's configuration (without the secret) on each task run or voice request and reuses a cached gateway while the saved configuration is unchanged. The comparison uses provider, model, base URL, credential hint and `updated_at`, so a change saved by another API instance is picked up on the next request.

## Outbound guard (SSRF)

A workspace owner chooses the URL the API server calls, so outside `ANUM_ENVIRONMENT=local` and `test` every workspace-configured endpoint goes through `anum_api/model_egress.py` (threat model G1). The operator's own `ANUM_MODEL_BASE_URL` default is trusted configuration and is not guarded.

Refused, both when the configuration is saved (`PUT /api/v1/model-config`, `422`) and again on every request:

- any scheme other than `https`, and any user name or password in the URL (refused in every environment);
- any port other than 443;
- IP literals written in non-canonical forms that resolvers still accept (`2130706433`, `0177.0.0.1`, `0x7f.1`, `127.1`);
- hosts that resolve to a loopback, private (RFC 1918), shared (100.64.0.0/10), unique-local, link-local (including `169.254.169.254`, `fd00:ec2::254` and other cloud metadata addresses), multicast, unspecified, reserved, documentation or otherwise non-global address, including IPv4 embedded in IPv6 (`::ffff:127.0.0.1`, NAT64 `64:ff9b::/96`, 6to4). A host is refused when any one of its addresses is.

**No DNS rebinding.** For each request the guarded transport (`PinnedEgressTransport`) resolves the host once, checks every address, then connects to the first one while sending the original `Host` header and using the original name for TLS SNI and certificate verification. A different answer later cannot redirect that request, and the save-time check is never trusted on its own. Workspace model calls connect directly rather than through `HTTP(S)_PROXY`, so the pinned address is the one dialled.

**No redirects.** Model clients never follow redirects; a 3xx is an error.

**Generic errors.** Resolution and address failures share one message that never says what the host resolved to. Outside `local`/`test` the connection test (`POST /api/v1/model-config/test`) answers every failure, refused or unreachable or HTTP error, with the same `502`: `Could not connect to the model provider. Check the base URL, model name and API key.` Locally it keeps the actionable messages described under [Free local model (Ollama)](#free-local-model-ollama).

**Self-hosted models.** `ANUM_MODEL_ALLOWED_HOSTS` is the operator's explicit allow-list, comma-separated `host` or `host:port` entries (no scheme), for example `ANUM_MODEL_ALLOWED_HOSTS=ollama.internal:11434`. A listed host may use plain HTTP, resolve to private or loopback addresses, and use the port its entry names (a bare `host` entry means port 443 or 80). Link-local, metadata, multicast, unspecified and reserved addresses stay refused even for listed hosts, and the request-time pinning still applies. The API refuses to start when an entry is malformed. Only add hosts you operate: a listed host is reachable by every workspace owner.

Configurations saved before the guard existed are checked on their next call; a refused one fails with the generic error until the owner saves a public endpoint.

## Monthly budgets

Owners can cap model use per UTC calendar month (threat model G4), for the organization (`tenant` scope, summed over all its workspaces) and for each workspace. Each budget has an optional estimated-cost limit in USD (`monthly_cost_limit_usd`) and an optional token limit (`monthly_token_limit`, input plus output tokens). Null means no limit; `0` blocks every call.

- **Enforced before each call.** Task planning (inline and Temporal worker) and voice answers use `budgeted_model_gateway`, which refuses the call before it reaches the provider once usage has reached a limit. `POST /api/v1/tasks/{id}/run` answers `402` with error code `model_budget_exceeded` and a message naming the reset date, before the task changes state. A durable run whose budget runs out after it was queued is failed with that message instead of being retried. Voice answers `200` with a short spoken sentence ("This workspace has reached its model budget for the month...", Arabic for Arabic sessions) instead of an error.
- **Recorded after each call** from the gateway's usage metadata: input and output tokens and `estimated_cost_usd`. Calls whose model has no price estimate add tokens but no cost and are counted as `unpriced_calls`, so set a token limit too for unpriced hosted models. Mock and Ollama calls cost 0. Failed calls record nothing. A failure to write usage is logged (`model_budget_record_failed`, error class only) and never fails the user's call. The connection test and streamed calls are not metered (the gateway does not report stream usage yet; streams are still checked).
- **Soft limit.** The check does not reserve spend: calls admitted concurrently just under a limit can overshoot it by their own usage. Usage increments themselves are atomic (`insert ... on conflict do update`), so none are lost.
- **Thresholds.** When a call crosses 80% or 100% of a limit, the API logs `model_budget_threshold` (scope, kind, percent, tenant and workspace ids; `anum_model_budget` extra) and adds one to `anum.model.budget.thresholds`; refused calls log `model_budget_exceeded` and add to `anum.model.budget.rejections`. Metric labels are scope, kind and percent only ([Observability](observability.md#metrics)). Crossings of a workspace limit are exact; for the tenant total two concurrent calls on different workspaces may both report the same crossing.
- **API (owners only, `organization:manage`).** `GET /api/v1/model-budgets` returns `period_start`, `resets_on`, and for `tenant` and `workspace` the `budget`, this month's `usage`, `total_tokens` and `exceeded`. `PUT /api/v1/model-budgets/{tenant|workspace}` with `{"monthly_cost_limit_usd": 50, "monthly_token_limit": 5000000}` sets or (with nulls) clears a budget and returns the same overview. Every change writes an `audit_records` row (`model_budget.update`, target `model_budget:tenant:<id>` or `model_budget:workspace:<id>`, previous and new limits).
- **Storage.** `ANUM_REPOSITORY_BACKEND=memory` keeps budgets and usage in process memory. With PostgreSQL they live in `model_budgets` (one row per tenant with a null `workspace_id`, one per workspace) and `model_usage_monthly` (per workspace and month), migration `0009_model_budgets`. Both are under forced RLS: rows are readable tenant-wide (a tenant total needs every workspace's usage) but writable only for the current workspace and the tenant-level budget row.

## Later

Add provider failover, embeddings provider pools, tenant-specific model policies, cached completions where safe, batch processing, evaluation harnesses, and cost dashboards.
## Free local model (Ollama)

ANUM can use [Ollama](https://ollama.com) as a free model provider. Ollama runs open models on your own computer and serves an OpenAI-compatible API at `http://localhost:11434/v1`, so no API key or paid account is needed.

1. Install Ollama for your OS from ollama.com.
2. Download a model: `ollama pull llama3.2` (about 2 GB; `qwen2.5` is a good multilingual alternative, including Arabic).
3. Start the server if it is not already running: `ollama serve`.
4. Point the API at it, in `services/api/.env` or the root `.env`:

   ```
   ANUM_MODEL_PROVIDER=ollama
   ANUM_MODEL_NAME=llama3.2
   ANUM_MODEL_BASE_URL=http://localhost:11434/v1
   ```

   `ANUM_MODEL_API_KEY` can stay empty; the gateway sends no `Authorization` header when no key is set. If the base URL or model are left at the OpenAI defaults, the `ollama` provider falls back to `http://localhost:11434/v1` and `llama3.2`.

These environment settings are the server default, read once at API startup, so restart the API after changing them. A task's result is the model's answer (the `anum.respond` tool returns the generated text).

Clients can also record a per-workspace model with `PUT /api/v1/model-config` using `provider: "ollama"`; no `api_key` is required and the workspace counts as configured. `POST /api/v1/model-config/test` calls the configured endpoint for real and, in `local` and `test`, returns an actionable error, for example `Could not reach Ollama at http://localhost:11434/v1. Is it running? Try: ollama serve`, or `ollama pull <model>` when the model is missing. Once saved, that workspace's model runs its tasks (`AgentRuntime`) and answers its voice questions; workspaces without a saved model fall back to the environment default. Saving a new model replaces the cached gateway on the next request. Where configurations are stored is described under [Per-workspace model configuration](#per-workspace-model-configuration).

**Phones and emulators.** The ANUM API server calls the stored `base_url`, not the phone, so the mobile setup screen pre-fills `http://localhost:11434/v1`. That works whenever Ollama runs on the same computer as the API, whether you use an emulator or a real phone. Only use another address (such as `http://192.168.1.20:11434/v1`, with Ollama started as `OLLAMA_HOST=0.0.0.0 ollama serve`) when Ollama runs on a different machine from the API.

**Privacy.** Prompts and answers stay on your machine; nothing is sent to a third-party model provider.

**Limitations.** Answers are slower and less capable than large hosted models, especially on laptops without a GPU, and small models may ignore strict structured-output schemas. Requests time out after 120 seconds. Outside `local` and `test` the [outbound guard](#outbound-guard-ssrf) only calls public HTTPS endpoints, so a workspace can use a self-hosted Ollama there only when the operator lists its `host:port` in `ANUM_MODEL_ALLOWED_HOSTS`.
