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

Three adapters implement the following today: OpenAI-compatible (`ANUM_MODEL_PROVIDER=openai-compatible`), Ollama (`ollama`, the same adapter against Ollama's OpenAI-compatible `/v1`) and the native [Anthropic Messages API](#anthropic-messages-api) (`anthropic`, `anum_api/anthropic_gateway.py`, which adds prompt caching). Everything below applies to all three unless a section says otherwise.

### Timeouts and retries

Each HTTP attempt times out after `ANUM_MODEL_TIMEOUT_SECONDS` (default 60; Ollama never less than 120). Failed attempts are retried up to `ANUM_MODEL_MAX_ATTEMPTS` total attempts (default 3) with exponential backoff and full jitter: the wait before retry `n` is a random value between 0 and `min(ANUM_MODEL_RETRY_MAX_SECONDS, ANUM_MODEL_RETRY_BASE_SECONDS * 2^(n-1))` (defaults 8 s and 0.5 s).

- Retried: timeouts, connection and network errors, HTTP 429 and any HTTP 5xx.
- Not retried: every other 4xx (bad request, auth, not found, validation). The provider error is raised immediately.
- `Retry-After` (seconds or HTTP date) on a 429 or 5xx sets the minimum wait. If it asks for longer than `ANUM_MODEL_RETRY_AFTER_MAX_SECONDS` (default 30) the gateway gives up rather than waiting or retrying early.
- Streaming is retried only until the first chunk reaches the caller; after that a retry would duplicate text, so the error is raised (see [Streaming](#streaming) for its own time budgets).
- When attempts run out, the last error (`httpx.HTTPStatusError` or the transport error) is raised, so callers such as `POST /api/v1/model-config/test` still produce actionable messages.

Chat-completion requests are not idempotent, so a retry after a timeout may be billed twice by the provider. Keep `ANUM_MODEL_MAX_ATTEMPTS` low for expensive models. `ModelCallMetadata.attempts` reports how many attempts a call took.

### Output cap

Every text call sends `max_tokens` = `ANUM_MODEL_MAX_OUTPUT_TOKENS` (default 1024, 16 to 32768): `generate_text` and every stream (a caller may ask for less, as voice does with 400). An unbounded reply from a slow model is what made long questions feel hung. Structured (JSON schema) calls are not capped: a cut-off plan would be invalid JSON.

### Streaming

`stream_chat(messages, max_tokens=None, on_usage=None)` streams a chat completion: `messages` are `{"role": "system"|"user"|"assistant", "content": str}` (the conversation and voice history), the request is `stream: true` with `stream_options: {"include_usage": true}`, and the adapter yields each `delta.content` as the server-sent events arrive. Ollama streams the same way through its OpenAI-compatible `/v1` endpoint; Anthropic's OpenAI-compatible endpoint (`https://api.anthropic.com/v1`) does too, but cannot use prompt caching, so Claude should run on the native [`anthropic` provider](#anthropic-messages-api), which streams Messages API events under the same rules. `stream_text(prompt)` is a one-message `stream_chat`. `complete_chat(messages, max_tokens=None)` is the non-streaming form, used for small background calls such as [conversation memory](memory.md#from-conversations) extraction; through `budgeted_model_gateway` it is checked and metered like every other call (a gateway without it answers through `stream_chat`, collected).

- **Time to first token.** Each attempt must produce its first token within `ANUM_MODEL_FIRST_TOKEN_TIMEOUT_SECONDS` (default 20), counted from sending the request. Missing it counts as a timeout and follows the retry policy above.
- **Total.** The whole stream, retries and backoff included, ends after `ANUM_MODEL_STREAM_TIMEOUT_SECONDS` (default 120) with `ModelStreamTimeout`. A retry whose backoff would end past that budget is not attempted.
- **No retry after the first token.** Once text reached the caller any failure (network, timeout, an `error` event inside the stream) is raised, never retried.
- **Cancellation.** Closing the iterator (the client disconnected or pressed Stop) closes the provider response, which stops generation.
- **Usage.** `on_usage` receives the call's `ModelUsage` exactly once: the provider's figures from the final usage event, or, when the provider reports none or the stream failed or was closed after producing text, an estimate of about four characters per token. Streams log one `model_call` record with `operation=stream_text`, like every other call.

### Usage and cost accounting

Token counts come from the provider's `usage` object (`prompt_tokens`, `completion_tokens`; Anthropic's `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`; a missing or null `usage` counts as zero). `ModelUsage.input_tokens` is every prompt token the call processed, cached ones included; `cache_creation_input_tokens` and `cache_read_input_tokens` say how many of them were written to or read from the provider's prompt cache (0 for providers without one). `ModelUsage.estimated_cost_usd` is computed from a price table in USD per million tokens:

- Mock and Ollama calls always cost `0`.
- Hosted models are looked up by exact name, then by the longest table entry that prefixes the returned model name at a `-` boundary, so `gpt-4.1-mini-2025-04-14` uses the `gpt-4.1-mini` price.
- Unknown hosted models report `null` rather than guessing.
- Cached input is priced separately: cache writes at `cache_write_per_million` (default 1.25x the input price, the 5-minute cache) and cache reads at `cache_read_per_million` (default 0.1x). Uncached input pays the input price.
- The built-in table covers `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-4o` and `gpt-4o-mini`, and `claude-haiku-5-5` ($0.10 / $0.50), `claude-sonnet-5-5` ($2 / $10) and `claude-opus-5-5` ($4 / $20, cache reads $0.20) at published list prices. Haiku's list price applies to prompts up to 100K tokens; longer prompts cost more than the estimate. Override the table with `ANUM_MODEL_PRICES`, a JSON object such as `{"gpt-4.1-mini": {"input_per_million": 0.4, "output_per_million": 1.6}}` (optionally with `cache_write_per_million` and `cache_read_per_million`). Setting the variable replaces the whole table.

The runtime already stores `usage` (including the estimate) in each run step's metadata. Estimates are for budgeting and dashboards, not invoicing.

### Redacted logging

Every call emits one `model_call` record on the `anum.model_gateway` logger. Successful calls log at INFO and failures at WARNING. Each retry also logs a `model_call_retry` WARNING. The fields are also attached as structured `extra` (`anum_model_call`, `anum_model_retry`):

`provider`, `model`, `operation`, `status` (`ok` or `error`), `attempts`, `latency_ms`, `http_status`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `estimated_cost_usd`, `error_class`.

Prompts, response text, API keys, request headers, base URLs and exception messages are never logged. Errors are reduced to their class name because provider error bodies can echo the prompt. Tests capture all log records for successful, failed and streaming calls and assert that neither the prompt, the response, nor the key appears.

The same fields feed OpenTelemetry ([Observability](observability.md#metrics)): a client span per call (`generate_text <model>` and so on, with token counts, cost and attempts, never text), the `anum.model.call.duration` histogram, and `anum.model.tokens`, `anum.model.cache.tokens` (cached input by `anum.model.cache.operation` `write`/`read`, a breakdown of the input tokens, not in addition to them) and `anum.model.estimated_cost_usd` counters by provider and model. The mock provider reports its token counts at zero cost. Failed calls mark the span as an error with the exception class only; `tests/test_telemetry.py` asserts no prompt, reply or key reaches any exported span or metric.

## Per-workspace model configuration

`PUT /api/v1/model-config` saves a workspace's provider (`mock`, `openai_compatible`, `ollama` or `anthropic`), model, base URL and (for hosted providers) API key. `GET` returns the configuration with `credential_configured` and a `credential_hint` (last four characters) only. The key is never returned, logged or echoed in an error; it must be sent again with every save. Saving and the connection test need `organization:manage` (owners; others get `403`), reading needs `organization:read`. Every read and write is scoped by the request's explicit tenant and workspace context.

- **Anthropic (Claude).** `{"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "...", "fast_model": "claude-haiku-5-5", "capable_model": "claude-sonnet-5-5", "fast_effort": "low", "capable_effort": null}`. The key is required. `base_url` is optional (default `https://api.anthropic.com`; an OpenAI-style `.../v1` is stored without `/v1`) and goes through the [outbound guard](#outbound-guard-ssrf) like any workspace endpoint. `fast_model`, `capable_model`, `fast_effort` and `capable_effort` mirror `ANUM_MODEL_FAST_NAME`, `ANUM_MODEL_CAPABLE_NAME`, `ANUM_MODEL_FAST_EFFORT` and `ANUM_MODEL_CAPABLE_EFFORT` ([Model routing](#model-routing)); empty or null means not set (`model` serves that route, no effort is sent). They are refused (`422`) for the other providers, as is an unknown effort. The workspace's gateway is the native [Anthropic adapter](#anthropic-messages-api) (prompt caching included), with `capable_model` (or `model`) as its default model, behind the workspace's [budget](#monthly-budgets).
- **Local and tests (`ANUM_REPOSITORY_BACKEND=memory`):** an in-memory store, lost on restart.
- **PostgreSQL (`ANUM_REPOSITORY_BACKEND=postgresql`):** the `workspace_model_configs` table (migration `0005_workspace_model_configs`; routing columns and the `anthropic` provider from `0024_model_config_routes`, expand-only), keyed by `(tenant_id, workspace_id)` with a foreign key to `workspaces`.
  - Row level security is enabled and forced, using the same `anum.tenant_id` / `anum.workspace_id` policy as the other tenant tables. The store sets that context on its own transaction before every query.
  - Saving for a workspace that has not finished onboarding returns `409`.
  - Check constraints refuse an `openai_compatible` or `anthropic` row without a stored credential, and an effort outside `low`, `medium`, `high`, `xhigh`, `max`.
  - The previous release ignores the routing columns and already builds the `anthropic` gateway, so an `anthropic` row still answers there (with its one saved model) after a rollback.

**Connection test.** `POST /api/v1/model-config/test` calls the saved configuration for real and is not metered. For `anthropic` it sends one tiny request (`max_tokens` 16, no cache breakpoint, so no cache write is billed) to each distinct routed model with that route's effort, so a wrong model name or an effort a model refuses fails here rather than in a chat; `models` in the answer lists what was called. Locally its errors say what to fix (`Anthropic rejected the API key (HTTP 401) ...`, `Anthropic does not know model '...'`, an HTTP 400 naming the model and the effort); outside `local`/`test` every failure is the generic `502` described under [Outbound guard](#outbound-guard-ssrf).

**Clients.** Web Settings › Model gateway (`apps/web/src/ModelSettings.tsx`) and Flutter (first-run model step and Settings › Model connection, [Mobile](mobile.md#first-run)) offer "Anthropic (Claude)" with `claude-haiku-5-5` (low effort) for quick replies and `claude-sonnet-5-5` for planning prefilled, a write-only key field, the connection test, and the API's `403` explained to non-owners.

**Encryption at rest.** The API key is stored only as Fernet ciphertext (`api_key_ciphertext`), plus the display hint. Display reads never decrypt; decryption happens only when building the workspace's gateway or running the connection test. `ANUM_SECRETS_KEY` holds one or more comma-separated Fernet keys. The first key encrypts and all keys decrypt, so a key can be rotated by prepending the new one and later re-saving configurations. Generate a key with:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The setting is required whenever `ANUM_ENVIRONMENT` is not `local` or `test`: the API refuses to start without a valid key, and the startup error never echoes setting values. In `local` or `test` without a key, a fixed development key is derived and a warning is logged. That key protects nothing, so never use local mode with real credentials you care about. Losing the key makes stored provider keys unrecoverable; re-enter them in Settings.

**Gateway cache.** `workspace_model_gateway` reads the workspace's configuration (without the secret) on each task run or voice request and reuses a cached gateway while the saved configuration is unchanged. The comparison uses provider, model, base URL, routing, credential hint and `updated_at`, so a change saved by another API instance is picked up on the next request.

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

- **Enforced before each call.** Task planning (inline and Temporal worker), voice answers and [conversations](conversations.md) use `budgeted_model_gateway`, which refuses the call before it reaches the provider once usage has reached a limit. `POST /api/v1/tasks/{id}/run` answers `402` with error code `model_budget_exceeded` and a message naming the reset date, before the task changes state. A durable run whose budget runs out after it was queued is failed with that message instead of being retried. Voice answers `200` with a short spoken sentence ("This workspace has reached its model budget for the month...", Arabic for Arabic sessions) instead of an error.
- **Recorded after each call** from the gateway's usage metadata: input and output tokens and `estimated_cost_usd`. Input tokens include prompt-cache writes and reads (they were processed), while the cost counts them at the cheaper cache rates, so caching lowers a workspace's cost but not its token count. Calls whose model has no price estimate add tokens but no cost and are counted as `unpriced_calls`, so set a token limit too for unpriced hosted models. Mock and Ollama calls cost 0. Failed calls record nothing. A failure to write usage is logged (`model_budget_record_failed`, error class only) and never fails the user's call. Streamed calls (conversations, voice) are checked before they start and metered when they end, from the provider's reported usage or the estimate described under [Streaming](#streaming), including a stream the user stopped. The connection test is not metered.
- **Soft limit.** The check does not reserve spend: calls admitted concurrently just under a limit can overshoot it by their own usage. Usage increments themselves are atomic (`insert ... on conflict do update`), so none are lost.
- **Thresholds.** When a call crosses 80% or 100% of a limit, the API logs `model_budget_threshold` (scope, kind, percent, tenant and workspace ids; `anum_model_budget` extra) and adds one to `anum.model.budget.thresholds`; refused calls log `model_budget_exceeded` and add to `anum.model.budget.rejections`. Metric labels are scope, kind and percent only ([Observability](observability.md#metrics)). Crossings of a workspace limit are exact; for the tenant total two concurrent calls on different workspaces may both report the same crossing.
- **API.** `GET /api/v1/model-budgets` (workspace owners, `organization:manage`) returns `period_start`, `resets_on`, for `tenant` and `workspace` the `budget`, this month's `usage`, `total_tokens` and `exceeded`, and `can_manage_tenant_budget` (whether this caller may change the organization budget). `PUT /api/v1/model-budgets/workspace` needs the workspace owner; `PUT /api/v1/model-budgets/tenant` caps every workspace of the organization and needs the organization administrator role `org_admin` (`organization_policy:manage`, [Governance](governance-and-scale.md#who-may-change-what)): a workspace owner without it gets `403` with `Organization administrator role required: ...`, nothing is saved and nothing is audited. Either `PUT` with `{"monthly_cost_limit_usd": 50, "monthly_token_limit": 5000000}` sets or (with nulls) clears a budget and returns the same overview. Every change writes an `audit_records` row (`model_budget.update`, target `model_budget:tenant:<id>` or `model_budget:workspace:<id>`, previous and new limits).
- **Clients.** Web (`Budgets` in the navigation) and Flutter (Settings › Model budgets) show both scopes from `GET /api/v1/model-budgets`: estimated cost and tokens against each limit with a bar (amber from 80%, red at 100%, as the API's thresholds), calls, input and output tokens, unpriced calls (with a hint to set a token limit), who changed the budget last, and the UTC reset date. Owners edit this workspace's two limits (empty means no limit, `0` blocks every call) or remove them; the organization card is editable only when `can_manage_tenant_budget` is true, otherwise it shows the numbers read-only with a notice that only an organization administrator can change the organization budget (and how to get the role). A non-owner gets the API's `403` and an explanation instead of numbers. Where a task run is started (web Tasks, Flutter Tasks) a `402` shows "Monthly model budget reached" with the API's sentence (it names the reset date) and a way to the budgets, instead of a generic error; the task that was created stays listed. Voice shows the same message if a voice call ever answers `402`; today voice answers with the spoken sentence above.
- **Storage.** `ANUM_REPOSITORY_BACKEND=memory` keeps budgets and usage in process memory. With PostgreSQL they live in `model_budgets` (one row per tenant with a null `workspace_id`, one per workspace) and `model_usage_monthly` (per workspace and month), migration `0009_model_budgets`. Both are under forced RLS: rows are readable tenant-wide (a tenant total needs every workspace's usage) but writable only for the current workspace and the tenant-level budget row.

## Embeddings

`embed(texts, model=...)` on the gateway serves retrieval ([Memory](memory.md#embeddings)). The OpenAI-compatible adapter (and Ollama, which serves the same API under `/v1`) posts to `{base_url}/embeddings` with the same client, SSRF guard, retries, redacted logging (`operation=embed`) and metrics as text calls, at most 64 texts per call, and checks that one vector comes back per text with consistent dimensions. Usage counts input tokens only; `text-embedding-3-small` and `-large` have default prices. `BudgetedModelGateway.embed` checks the monthly budget before the call and records usage after it, exactly as for text. The mock gateway embeds with the deterministic local embedder (`anum-local-hash-v1`), so tests and `local` need no model server. Default embedding models: `nomic-embed-text` (Ollama; `ollama pull nomic-embed-text`) and `text-embedding-3-small` (otherwise), or `ANUM_EMBEDDING_MODEL`. Anthropic has no embeddings endpoint: with the `anthropic` provider retrieval uses the deterministic local embedder in process (no text leaves the API, cost 0), and a non-empty `ANUM_EMBEDDING_MODEL` is refused by `embed`. A workspace that saved `anthropic` while the server default is another provider embeds locally too: the server's `ANUM_EMBEDDING_MODEL` names a model of that other provider and is not passed to the workspace's Anthropic gateway (which would still refuse it).

## Anthropic Messages API

`ANUM_MODEL_PROVIDER=anthropic` calls `POST {base_url}/v1/messages` directly with httpx (no SDK dependency), sending `x-api-key` (`ANUM_MODEL_API_KEY`, the same secret as the other hosted providers) and `anthropic-version: 2023-06-01`. `ANUM_MODEL_BASE_URL` defaults to `https://api.anthropic.com` when it is left at the OpenAI default; a trailing `/v1` is accepted and stripped. Without a key the API refuses to start, as for `openai-compatible`.

- **Same contract as the other adapters.** Per-attempt timeout, bounded retries with backoff (HTTP 429, 529 overloaded and other 5xx, timeouts and connection errors; other 4xx are raised at once as `httpx.HTTPStatusError`, which the connection test reports like any provider error), `max_tokens` capped at `ANUM_MODEL_MAX_OUTPUT_TOKENS` (structured calls get a 16000 ceiling, since the API requires one and a cut-off plan is invalid JSON), the first-token and total stream budgets, no retry after the first token, redacted logging and metrics.
- **Requests.** System messages become the top-level `system` blocks; user and assistant turns keep their order. `generate_structured` sends `output_config.format` (`json_schema`). Only `text` blocks form the answer: thinking blocks and any other content are ignored. `stop_reason` (for example `refusal` or `max_tokens`) is reported as `ModelCallMetadata.finish_reason`.
- **Streaming.** `stream: true` server-sent events: `message_start` (model and input usage), `content_block_delta` with `text_delta` (yielded; `thinking_delta` is not), `message_delta` (stop reason and cumulative usage), `message_stop` (end). `ping` and block start/stop events are ignored. An `error` event raises `ModelStreamError` and is never retried. Usage comes from `message_start` plus `message_delta`; a stream that ends before `message_delta` is metered with the four-characters-per-token estimate, as for other providers.
- **Prompt caching.** Each chat request (conversations, voice) carries `cache_control: {"type": "ephemeral"}` breakpoints, at most three of the four the API allows, in render order: the last tool definition (when the gateway is built with stable `tools`), the last system block, and the last message. The system prompt is cached on its own, and each turn reads the previous turn's prefix (system plus the conversation so far) and writes its own for the next turn, which keeps a 5-minute cache warm for a live conversation. One-off prompts (`generate_text`, `generate_structured`: planning, the voice fallback, the connection test) send no breakpoint unless the gateway has tools, because a cache write costs 1.25x and nothing would read it. Prefixes shorter than the model's minimum cacheable length (512 tokens on the current models) are not cached and cost nothing extra. ANUM callers send no native tool definitions today (tool selection stays with the runtime's mediated registry), so the tools breakpoint is unused until they do.
- **Accounting.** `cache_creation_input_tokens` and `cache_read_input_tokens` are recorded on `ModelUsage`, in the `model_call` log record and on spans (`gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.cache_read.input_tokens`), and priced as described under [Usage and cost accounting](#usage-and-cost-accounting); [budgets](#monthly-budgets) add up that cost. Check that caching works with `sum by (anum_model_cache_operation) (increase(anum_model_cache_tokens_total[1h]))`: reads near zero across a busy hour mean something in the prefix changes on every request.
- **Embeddings.** None; see [Embeddings](#embeddings).
- **Workspace configurations.** A workspace can choose `anthropic` in Settings with its own key and routing ([Per-workspace model configuration](#per-workspace-model-configuration)); it then uses this adapter, prompt caching included.

## Model routing

Every caller states what a call is for (`ModelRoute`, applied with `route_gateway`): conversation replies, streamed voice answers and the voice fallback answer are `fast`; task planning (inline and Temporal worker runs) is `capable`. The routed gateway shares the original's client and settings and stays behind the workspace's budget.

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_MODEL_FAST_NAME` | empty | Model for quick replies. |
| `ANUM_MODEL_CAPABLE_NAME` | empty | Model for planning and agent runs; also the gateway's default model. |
| `ANUM_MODEL_FAST_EFFORT` | `low` | `anthropic` only: `output_config.effort` for quick replies (`low`, `medium`, `high`, `xhigh`, `max`, or empty for the model's default). |
| `ANUM_MODEL_CAPABLE_EFFORT` | empty | `anthropic` only: the same for planning. |

- **Backwards compatible.** With both names empty every call uses `ANUM_MODEL_NAME`, exactly as before, for `openai-compatible` and `ollama`. Setting only one route changes only that route.
- **`anthropic` defaults.** With `ANUM_MODEL_NAME` left at its default, fast is `claude-haiku-5-5` and capable is `claude-sonnet-5-5`. Setting `ANUM_MODEL_NAME` (for example `claude-opus-5-5`) makes it serve both routes unless a route name is also set.
- **Effort.** The current Haiku and Sonnet models think adaptively by default; `low` effort keeps short replies (voice is capped at 400 tokens) quick and leaves the output budget for the answer. Clear `ANUM_MODEL_FAST_EFFORT` for a model that does not accept `effort` (for example `claude-haiku-4-5`). An invalid value stops the API at startup, and the Helm chart refuses it at `helm template`.
- **Workspace models** saved in Settings with `openai_compatible`, `ollama` or `mock` have one model and no routes: they answer both kinds of call, and the connection test uses that model. An `anthropic` workspace routes like the server: `fast_model` (or `model`) with `fast_effort` for conversation replies, voice and [conversation memory](memory.md#from-conversations) extraction, `capable_model` (or `model`) with `capable_effort` for planning; the connection test calls each distinct model once.

## Later

Add provider failover, embeddings provider pools, tenant-specific model policies, per-workspace routes for the OpenAI-compatible and Ollama providers, keeping a saved key when only the routing changes, prompt-cache layouts for planning prompts (stable instructions before the task), batch processing and evaluation harnesses.
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
