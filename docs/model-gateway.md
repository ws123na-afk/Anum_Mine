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