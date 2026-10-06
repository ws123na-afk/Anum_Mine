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

Clients can also record a per-workspace model with `PUT /api/v1/model-config` using `provider: "ollama"`; no `api_key` is required and the workspace counts as configured. `POST /api/v1/model-config/test` calls the configured endpoint for real and returns an actionable error, for example `Could not reach Ollama at http://localhost:11434/v1. Is it running? Try: ollama serve`, or `ollama pull <model>` when the model is missing. Once saved, that workspace's model runs its tasks (`AgentRuntime`) and answers its voice questions; workspaces without a saved model fall back to the environment default. Saving a new model replaces the cached gateway on the next request. Stored configurations are in memory today, so they reset when the API restarts.

**Phones and emulators.** The ANUM API server calls the stored `base_url`, not the phone, so the mobile setup screen pre-fills `http://localhost:11434/v1`. That works whenever Ollama runs on the same computer as the API, whether you use an emulator or a real phone. Only use another address (such as `http://192.168.1.20:11434/v1`, with Ollama started as `OLLAMA_HOST=0.0.0.0 ollama serve`) when Ollama runs on a different machine from the API.

**Privacy.** Prompts and answers stay on your machine; nothing is sent to a third-party model provider.

**Limitations.** Answers are slower and less capable than large hosted models, especially on laptops without a GPU, and small models may ignore strict structured-output schemas. Requests time out after 120 seconds. Non-local environments require HTTPS base URLs, so a plain-HTTP Ollama endpoint is only accepted when `ANUM_ENVIRONMENT=local`.
