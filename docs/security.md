# Security

ANUM should be designed as a security-sensitive system from the beginning because agents can read private context, call external tools, and act on behalf of users. Security must be part of the product model, not an afterthought added around an autonomous core.

## Identity

Keycloak is the identity provider. ANUM uses OIDC for sign-in, token issuance, session management, MFA policy, and federation. Application services validate tokens, map external identities to ANUM users, and avoid embedding identity assumptions in client code.

The realm is code (`infra/keycloak/anum-realm.json`): public clients use authorization code with PKCE only, tokens carry `aud: anum-api` and an admin-managed `tenant_id` claim, and the API validates issuer, audience, expiry, and signature against a cached, rotation-aware JWKS. The persisted workspace membership, not the token's realm roles, decides the caller's workspace role. Development authentication (header-asserted context and `anum_local_*` sessions) is refused at startup outside `local`/`test`. The full contract is in [Identity and sign-in](identity.md).

## Authorization

ANUM authorization should combine application-level policy with PostgreSQL row-level security. The backend decides whether a user, service, or agent may perform an action. The database enforces tenant and workspace isolation so accidental query mistakes do not leak data across boundaries. The only cross-tenant reader is the event outbox relay, which runs as the narrowly privileged `anum_outbox_relay` role: it can read unpublished `domain_events` rows and update their publication columns, nothing else, and is not `BYPASSRLS` ([Events](events.md#relay-role-and-rls)).

## Secrets

Secrets must be stored outside source control. Provider keys, integration tokens, signing keys, and storage credentials should be delivered through environment-specific secret stores. Local development may use `.env` files, but sample files must contain placeholders only.

## HTTP Hardening

`services/api/anum_api/hardening.py` adds pure ASGI middlewares (they never buffer SSE streams). For a request the order is CORS, security headers, rate limit, body size limit, then the application.

| Control | Behaviour | Settings |
|---|---|---|
| Body size limit | `413 payload_too_large` when `Content-Length` or the streamed body exceeds the limit. `POST /api/v1/files` uploads get the larger upload limit. | `ANUM_MAX_REQUEST_BODY_BYTES` (1 MiB), `ANUM_MAX_UPLOAD_BODY_BYTES` (25 MiB) |
| Rate limit | Token bucket per client IP; `429 rate_limited` with `Retry-After`. `/health` and CORS preflights are exempt. At most 10,000 client keys are tracked (least recently used dropped). `Retry-After` is exposed to browsers through CORS. | `ANUM_RATE_LIMIT_ENABLED` (true), `ANUM_RATE_LIMIT_REQUESTS_PER_MINUTE` (600), `ANUM_RATE_LIMIT_BURST` (120) |
| Security headers | Every response: `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy`. `Strict-Transport-Security` is added outside `local`. | `ANUM_ENVIRONMENT` |
| Interactive docs | `/docs` and `/redoc` are served only in `local` (and are exempt from the API CSP there). | `ANUM_ENVIRONMENT` |

Rate-limit state is in memory per process by default. `ANUM_RATE_LIMIT_BACKEND=valkey` shares it across API replicas: the token bucket runs as one atomic Valkey script on the server clock under `anum:ratelimit:<client>` keys that expire once the bucket would be full again. The call runs in a worker thread with a short timeout (`ANUM_VALKEY_TIMEOUT_SECONDS`, 0.5); if Valkey is unreachable the request is limited by a per-process bucket instead of failing or going unlimited. Client IPs come from uvicorn's proxy-header handling, which only trusts `FORWARDED_ALLOW_IPS`; see [Infrastructure](infrastructure.md).

### Startup policy

Outside `ANUM_ENVIRONMENT=local` the API refuses to start (`InsecureConfigurationError`, listing every problem) when:

- `ANUM_CORS_ORIGINS` contains `*`, a localhost or loopback origin, or any non-`https` origin;
- `ANUM_DATABASE_URL` uses the compose credentials (`anum:anum`);
- the object-storage secret, webhook key or model key is a compose default (`anum-local-secret`, `anum`, `admin`);
- rate limiting is disabled or a limit is not positive.

The API container defaults to `ANUM_ENVIRONMENT=production`, so these checks apply unless a deployment deliberately opts into `local`. Header-mode authentication outside `local` is tracked separately in Stage 2 of the [Production plan](production-plan.md).

### Web container

The web image's nginx sends a CSP of `default-src 'self'` with these exceptions: `script-src 'wasm-unsafe-eval'` and `cdn.jsdelivr.net` plus `connect-src` to `huggingface.co` for the in-browser Kokoro voice (ONNX runtime and model weights); `connect-src` also lists `ANUM_CSP_CONNECT_SRC` (the API origin); `style-src 'unsafe-inline'` for React style attributes; `frame-ancestors 'none'`, `object-src 'none'`. It also sends HSTS, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY` and a `Permissions-Policy` that allows only the microphone (voice) for the page's own origin.

## Scanning in CI

The **Security scans** job in `.github/workflows/ci.yml` fails the build on:

- `pip-audit --strict services/api`: known vulnerabilities in the API's resolved dependencies.
- `pnpm audit --prod --audit-level high`: high or critical advisories in production Node dependencies.
- `bandit -r services/api/anum_api services/api/migrations`: Python SAST.
- `gitleaks` (pinned release, checksum verified) over the full history of the commit under test, with `.gitleaks.toml`.

`codeql.yml` runs CodeQL `security-extended` queries for Python and JavaScript/TypeScript. Dart and Rust SAST, and container image scanning, are not yet in CI.

### Reviewed exceptions

Each exception is scoped as narrowly as the tool allows. Add new ones only with a written reason here.

| Tool | Exception | Reason |
|---|---|---|
| bandit | `# nosec B608` on the `select` in `LocalAutomationEngine._list` (`anum_api/automation.py`) | The interpolated table name must pass the `_TABLES` allow-list first; all values are bound parameters. |
| gitleaks | `docs/figma-design-state.json` `fileKey` | A public Figma file identifier, not a credential. Only that exact key shape in that file is allowed. |
| gitleaks | `docs/infrastructure.md` prose where "Keycloak" is followed by the S3 storage name | The generic API-key rule reads the word "key" inside "Keycloak" as a key name. The text stays in history. |
| gitleaks | `services/api/tests/test_postgres_model_configs.py` value `sk-live-PERSISTED-secret-4321` | A made-up provider key used to test encryption at rest. Only that exact value in that file is allowed; it stays in history. |
| pnpm audit | Moderate `sprintf-js` advisory (GHSA-hp3w-g68c-fv3c) reached through `kokoro-js` → `@huggingface/transformers` → `onnxruntime-node` → `global-agent` | No patched release exists. The package belongs to the Node.js runtime and is not in the browser bundle. The job fails on high and critical only. |

Fixed findings when the scans were added: PyJWT 2.9.0 → 2.15.1 and Starlette 0.38.6 → 1.7.0 (via FastAPI 0.115.0 → 0.135.4), and `sharp` forced to `^0.35.4` through a pnpm override (it is pulled in by `@huggingface/transformers` but unused by the browser bundle).

## Agent Safety

Agents must not receive raw unrestricted access to user accounts, files, or integrations. Each tool call should be mediated by the runtime, checked against policy, logged, and paused for approval when risk requires it. Prompt injection must be treated as an expected attack class, especially when agents read external content. The [Threat model](threat-model.md) maps the trust boundaries, what an injected instruction can and cannot do today, and the open gaps (SSRF through workspace model base URLs, approval content and binding, per-tenant budgets).

## Data Protection

Tenant data should be encrypted in transit and at rest by infrastructure defaults. Sensitive fields should be minimized, redacted in logs, and excluded from analytics payloads. Telemetry follows the redaction rules in [Observability](observability.md#redaction-rules): spans carry tenant and workspace ids only, never prompts, replies, query strings, credentials or client addresses. Backups contain every tenant and are handled as described in [Runbooks](runbooks.md#backup-and-restore). Memory records should carry provenance, scope, and retention metadata.

## Auditability

Security-relevant events should be recorded: login, token refresh failures, tenant membership changes, role grants, integration consent, tool execution, approval decisions, memory deletion, policy changes, and administrative exports. Invitation and membership changes are recorded today in the append-only `audit_records` table (RLS allows select and insert only); governance changes still use the in-memory audit recorder.

## Now

OIDC validation with membership resolution is implemented (`ANUM_AUTH_MODE=oidc`). Keep tenant isolation, RLS, minimal roles, audit tables, secure defaults, and approval gates in place before real external actions. Multi-user workspaces use single-use, expiring, hash-stored invitations and owner-managed memberships with last-owner protection; self-service membership only bootstraps an empty workspace (see [Identity and sign-in](identity.md#invitations-and-membership-management)). Request size limits, rate limiting, security headers, startup configuration checks, and dependency, secret and static analysis scanning in CI are in place.

## Later

Add per-tenant quotas, container image scanning, Dart and Rust SAST, policy simulation, organization compliance exports, anomaly detection, device trust, per-integration token vaulting, customer-managed keys, and formal security review workflows.
