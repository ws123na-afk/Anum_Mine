# Runbooks

Operational procedures for ANUM. Each alert in `infra/observability/prometheus/alerts.yaml` links a section here ([Observability](observability.md#alerts)). No production environment exists yet (the cloud is not chosen, see the [Production plan](production-plan.md)), so commands use the local compose stack and placeholders such as `<api>`; replace them with the deployment's equivalents when it exists. Everything that needs an owner decision is marked **Proposal**.

Ground rules for every procedure:

- Tenant context stays explicit. Never disable RLS, connect the API as a superuser, or grant `BYPASSRLS` to fix an incident. Read tenant data only through the API or as `anum_app` with `anum.tenant_id`/`anum.workspace_id` set. The only roles that see across tenants are the outbox relay (unpublished events only) and the backup role.
- Never paste prompts, model replies, memory or file contents, tokens or keys into tickets, chat or incident notes. Use ids: correlation id, trace id, tenant id, task id, run id, event id.
- Change configuration through the deployment's secret store and redeploy; do not edit running containers.

## Incident Response

1. **Declare.** Anyone who sees user impact or a `page` alert opens an incident channel and names an incident lead (the on-call engineer by default). Severity: **SEV1** data exposure across tenants, data loss, or the API down for everyone; **SEV2** a core flow broken (sign-in, tasks, approvals, runs) or a single tenant down; **SEV3** degraded but working.
2. **Stabilise before diagnosing.** Prefer reversible actions: roll back the last deploy, scale API replicas or workers, turn off an integration (`ANUM_EXTERNAL_WEBHOOK_URL` unset), switch a workspace to the mock model. For a suspected cross-tenant leak or compromised credential, contain first: revoke sessions in Keycloak, rotate the credential (sections below), block the route at the load balancer.
3. **Find the request.** Users see an error envelope with `correlation_id` and the `X-Correlation-ID` header. Search logs (Loki: `{service_name="anum-api"} |= "<correlation id>"`) to get the `trace_id`, then open the trace in Tempo. Traces carry tenant and workspace ids, never content.
4. **Communicate** every 30 minutes for SEV1/SEV2 to the owner and, for SEV1 data exposure, prepare the tenant notification (legal requirements depend on jurisdiction and are an owner decision).
5. **Close** when the alert is resolved and the fix is deployed or the mitigation is durable. Write a blameless review within five working days: timeline, impact (tenants, duration), root cause, what detected it, and action items with owners. Store it with the release evidence.

## On-Call

**Proposal** until a team and a paging provider exist:

- One primary and one secondary, weekly rotation, handover on a fixed weekday with open incidents and silenced alerts reviewed.
- `severity: page` alerts page the primary at any hour (acknowledge within 15 minutes, escalate to the secondary after 30). `severity: ticket` alerts open a ticket handled in working hours.
- Alertmanager routing to the paging tool is not configured yet; today alerts are visible only in Prometheus and Grafana ([Observability](observability.md#alerts)).
- The on-call engineer needs: read access to Grafana, Tempo and Loki; the deploy and rollback workflow; Temporal UI; read-only database access as `anum_app`; break-glass access to the secret store and Keycloak admin, used only during a declared incident and logged.

## API Error Rate or Latency

Alerts: `AnumApiHighErrorRate`, `AnumApiHighLatencyP95`, `AnumRateLimitRejectionsHigh`.

1. Open the **ANUM API: errors and latency** dashboard. Find which routes and status codes moved and when; compare with the deploy history.
2. 5xx on every route: check API health (`/health`), recent deploys, PostgreSQL (connections, locks), and the startup log for `InsecureConfigurationError` or migration errors. Roll back if a deploy correlates.
3. 5xx on run, resume or approval routes: check `503`s from run coordination (Valkey) or the durable runtime (Temporal) in the **queue depth** dashboard, then the sections below.
4. Latency: open slow traces in Tempo for the route (`{ span.http.route = "<route>" && duration > 1s }`) and look at which child span dominates: SQL, model call (`generate_text ...`), JWKS fetch or NATS publish. Model latency belongs to the provider; consider lowering `ANUM_MODEL_TIMEOUT_SECONDS` or moving the workspace to another model.
5. 429s: a single client is usually the cause (rate limits are per client IP). Check that `FORWARDED_ALLOW_IPS` matches the load balancer, otherwise every user shares the balancer's IP. Raise `ANUM_RATE_LIMIT_REQUESTS_PER_MINUTE` only with a reason; use `ANUM_RATE_LIMIT_BACKEND=valkey` with more than one replica.

## Outbox Backlog

Alerts: `AnumOutboxBacklogStale`, `AnumOutboxBacklogLarge`, `AnumOutboxParkedEvents`. Committed events are rows in `domain_events` with `published_at is null` until NATS acknowledges them ([Events](events.md)). A backlog delays realtime updates; nothing is lost.

1. **Is NATS up and reachable?** The relay does not try while the bus is disconnected (`NATS disconnected` warnings in API logs). Check the NATS server (`:8222/healthz`, JetStream storage space). Once NATS is back the relay drains by itself.
2. **Is a relay running?** Every API process with `ANUM_REPOSITORY_BACKEND=postgresql` and `ANUM_EVENT_BUS=nats` runs one. Look for `Outbox relay pass failed` in logs: a database permission error usually means the relay login lost the `anum_outbox_relay` role (`ANUM_OUTBOX_DATABASE_URL`).
3. **Is it publishing but slow?** `anum_outbox_published_total` rising with a growing backlog: raise `ANUM_OUTBOX_BATCH_SIZE` (max 1000) or add API replicas (relays split work with `SKIP LOCKED`).
4. **Failures** (`anum_outbox_publish_failures_total`): rows back off exponentially up to 30 s; the reason is in `publish_last_error`. Inspect as the relay role, which sees only unpublished rows and no payload columns you need:

   ```sql
   set role anum_outbox_relay;
   select id, tenant_id, type, publish_attempts, publish_next_attempt_at, publish_last_error
   from domain_events where published_at is null order by created_at limit 50;
   ```

5. **Parked events** (`publish_next_attempt_at = 'infinity'`) can never be published as they are (for example an event type that is not a valid subject token). They need a code fix. After deploying it, release them for another attempt with a reviewed statement run as the relay role: `update domain_events set publish_next_attempt_at = now() where id = '<event id>' and published_at is null;`. Never delete events: they are the audit trail clients replay from.

## Temporal Stuck or Failing Runs

Alerts: `AnumTemporalActivityFailures`, `AnumRunCoordinationUnavailable`. See [Agent runtime](agent-runtime.md#durable-execution) for the checkpoint phases.

1. Open the **queue depth** dashboard: activity outcomes by type. `locked` is normal under contention; `not_visible_yet` is normal right after a start; `error`, `not_found` and `coordination_unavailable` are not.
2. `coordination_unavailable` or the Valkey alert: Valkey is down or unreachable. API run/resume/approve answer `503`; workers retry with backoff. Restore Valkey; no data is lost (locks expire by TTL, `ANUM_RUN_LOCK_TTL_SECONDS`).
3. Workers: check the worker processes are up and polling `ANUM_TEMPORAL_TASK_QUEUE` (Temporal UI, task queue view shows pollers). No pollers means no progress: restart or scale workers. Workers need the same `ANUM_*` configuration as the API.
4. A single stuck run: find the workflow `anum-run/<tenant>/<workspace>/<task>` in the Temporal UI. Its run state is in PostgreSQL (`agent_runs.checkpoint`). Typical cases:
   - `waiting_approval`: by design; the run waits for a person (re-read at least hourly if a signal was lost).
   - `tool_ready` with no live workflow: `POST /api/v1/agent-runs/{id}/resume` restarts it.
   - Failed with "outcome unknown": a worker died while a non-idempotent or approved high-risk tool ran. The runtime never repeats it; a person must check the external system and decide. Do not force a re-run.
5. Activity errors from the model provider show as `error` with a model `error.type` in the trace; see [Model cost spike](#model-cost-spike) for provider problems.
6. Never terminate workflows in bulk or edit `agent_runs` rows by hand; cancel through the API (`POST /api/v1/tasks/{id}/cancel`) so the cancellation is evented and audited.

## Model Cost Spike

Alerts: `AnumModelCostSpike`, `AnumModelHourlySpendHigh`, `AnumModelErrorRate`.

1. Open **ANUM model gateway: cost and usage**: which provider and model, input or output tokens, since when.
2. Find the source: request traces with `generate_text` spans in the window, grouped by `anum.tenant_id`, show which workspace drives it. Per-tenant cost metrics do not exist yet.
3. Contain: switch the workspace to a cheaper model or the mock provider (`PUT /api/v1/model-config` as its owner, or with the owner's agreement), or pause runs for that workspace. For a runaway loop, cancel its tasks. Revoke the provider key at the provider if it may have leaked.
4. Costs are estimates from the price table (`ANUM_MODEL_PRICES`); reconcile with the provider's billing and fix stale prices.
5. Error rate: provider outage, quota, or a revoked key (`HTTPStatusError` with 401/403 or 429 in spans). The gateway already retries timeouts, 429 and 5xx with backoff.

## Telemetry Pipeline

Alert: `AnumTelemetryPipelineDown`. All other alerts are blind while it fires. Check the collector process and its logs (export errors to Tempo or Loki, memory limiter refusals), then Prometheus' target page. The API keeps working without telemetry: exporters drop data when the collector is unreachable.

## Backup and Restore

Tool: `infra/backup/anum_backup.py` (Python 3.11+, psycopg 3, PostgreSQL client tools at least as new as the server). It prints JSON reports and never prints connection strings; it passes credentials to `pg_dump`/`pg_restore` through `PG*` environment variables.

### Backups

```bash
python infra/backup/anum_backup.py backup --database-url "$ANUM_BACKUP_DATABASE_URL" --out /secure/backups
```

- One `pg_dump --format=custom` of the whole database plus `anum-<UTC>.manifest.json`: Alembic revision, extensions, RLS policies, per-table row counts, RLS and FORCE RLS flags, per-tenant row counts and the dump's SHA-256. The manifest's counts come from the same exported snapshot as the dump, so they match it exactly.
- The backup login must bypass RLS (superuser or a dedicated `anum_backup` role with `BYPASSRLS` and `pg_read_all_data`). The tool refuses a role subject to RLS, which would otherwise produce a silently partial backup. Keep that login out of the application's configuration.
- Dumps contain every tenant. Files are written `0600` in a `0700` directory; ship them to encrypted object storage with versioning and object lock, readable only by the backup and restore roles, in a second region. **Proposal:** keep 35 daily and 12 monthly backups.
- Object storage (workspace files) is not in the dump: enable bucket versioning and cross-region replication in the cloud account (Stage 4).

### Restore drill

```bash
python infra/backup/anum_backup.py drill \
  --database-url "$ANUM_BACKUP_DATABASE_URL" --admin-url "$ANUM_RESTORE_ADMIN_URL" --work-dir /secure/drill
```

The drill backs up, restores into a new `anum_restore_drill_*` database, verifies, and drops it (`--keep` keeps it for inspection). Verification fails the drill unless:

- the Alembic revision, extensions and policy list equal the manifest;
- every table exists with exactly the manifest's row count, per-tenant counts, and RLS/FORCE RLS flags, and every table with `tenant_id` has RLS enabled and forced;
- as a temporary role that is neither owner, superuser nor `BYPASSRLS` (granted `SELECT` on every table, dropped afterwards), every RLS table shows zero rows without a tenant context, zero rows of other tenants inside a tenant context, and the tenant's own rows are visible.

Run it **monthly** and before every schema migration that rewrites data (**Proposal**), against a copy of production in an isolated environment, and record the JSON report with the release evidence. `services/api/tests/test_backup_restore.py` runs the drill and a tampered-restore negative test in the PostgreSQL CI job.

Measured locally (PostgreSQL 16.15 with pgvector, synthetic data: 3 tenants, 60,010 rows in 13 tables, 1.1 MB dump): backup 0.39 s, restore 0.68 s, verification including the RLS probe 0.12 s. A restore of a cluster without the ANUM roles recreated `anum_outbox_relay` as `NOLOGIN` automatically, and the restored database passed verification there too.

### Restoring for real

1. Declare an incident. Decide the target time and get the owner's approval: a restore loses everything after the backup.
2. Restore the chosen dump into a new database: `anum_backup.py restore --admin-url ... --dump <file> --manifest <file> --target anum_restore_<date>`. The tool checks the dump's checksum, refuses to overwrite any existing database, recreates missing cluster roles as `NOLOGIN`, and keeps owners, grants, policies and FORCE RLS.
3. Verify it: `anum_backup.py verify --database-url <restored> --manifest <file>`.
4. **Whole-database recovery:** stop API and workers, point `ANUM_DATABASE_URL` and `ANUM_OUTBOX_DATABASE_URL` at the restored database (or rename databases), grant the application and relay logins as before, start, and check the authenticated journey. Events committed after the backup are gone; clients resync from the event history.
5. **One tenant's data:** never restore a partial dump over production. Copy that tenant's rows from the scratch database into production with a reviewed script that filters every statement on `tenant_id = '<tenant>'`, runs as a role subject to RLS with that tenant's context set, and is reviewed by a second person. Then drop the scratch database.

### Recovery objectives

**Proposal**, to be confirmed by the owner and re-measured on production-sized data:

| Objective | Target | How |
|---|---|---|
| RPO (data loss) | 24 hours with nightly logical dumps today; **5 minutes** once the managed database has point-in-time recovery (continuous WAL archiving) | PITR from the provider; logical dumps stay as the second, provider-independent copy |
| RTO (time to restore service) | **2 hours** for a full restore | Restore plus verification measured 0.8 s for 60,000 rows locally; scale by data volume in the drill and keep the remainder for deploy, DNS and checks |
| Restore drill | monthly, and before data-rewriting migrations | `anum_backup.py drill`, report kept as evidence |

## Rotating ANUM_SECRETS_KEY

`ANUM_SECRETS_KEY` holds comma-separated Fernet keys: the first encrypts, all decrypt (`anum_api/secret_box.py`). It protects workspace model provider keys (`workspace_model_configs.api_key_ciphertext`).

1. Generate a key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Store it only in the secret store.
2. Deploy `ANUM_SECRETS_KEY=<new>,<old>` to API and workers together. New and updated configs are encrypted with the new key; old ciphertexts still decrypt.
3. Re-encrypt existing rows. There is no re-encryption command yet (gap): until there is, have each workspace owner re-save the model key (`PUT /api/v1/model-config`), or run a reviewed one-off that, per workspace and as `anum_app` with that tenant's context, decrypts with the old list and encrypts with the new key. Count `api_key_ciphertext is not null` rows before and after.
4. When no ciphertext needs the old key, deploy `ANUM_SECRETS_KEY=<new>` alone. A row that still needs the old key fails with `SecretDecryptionError` (the workspace's model calls fail; nothing else does).
5. Suspected key leak: rotate immediately and also ask workspace owners to rotate their provider keys, since the ciphertexts may have been copied.

## Rotating Keycloak Signing Keys (JWKS)

The API validates tokens against the realm's JWKS, cached for `ANUM_OIDC_JWKS_CACHE_SECONDS` (300). A token with an unknown `kid` forces one refresh, at most every `ANUM_OIDC_JWKS_MIN_REFRESH_SECONDS` (30) ([Identity](identity.md)).

1. In the realm's Keys providers, add a new RS256 key provider with a higher priority than the current one. Keycloak signs new tokens with it; the old key stays published, so existing tokens still validate.
2. Wait at least the longest token lifetime (access token lifespan, and the refresh token / SSO session max if refresh tokens should survive), plus the JWKS cache time.
3. Set the old provider to passive (still published, not used for signing), wait again, then disable and delete it. Tokens signed with it now fail with `401`, so clients sign in again.
4. Emergency (signing key compromised): add the new key, delete the compromised provider at once, and revoke sessions (Realm settings, Sessions, sign out all). Every user signs in again; the API picks up the new key on the first unknown `kid`.
5. Client secrets: the realm's clients are public PKCE clients without secrets. If a confidential client is added later, rotate its secret in Keycloak and the secret store together.
6. Realm changes go through `infra/keycloak/anum-realm.json` (realm as code); do not leave production-only changes undocumented. Keycloak's `admin/admin` in compose is for development only.

## Other Credentials

Database logins (`ANUM_DATABASE_URL`, `ANUM_OUTBOX_DATABASE_URL`, backup login), S3 keys, the external webhook key and model provider keys rotate the same way: create the new credential, deploy it, confirm traffic, revoke the old one. The API refuses to start outside `local` with the compose defaults ([Security](security.md#startup-policy)).
