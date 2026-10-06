# Runbooks

Operational procedures for ANUM. Each alert in `infra/observability/prometheus/alerts.yaml` links a section here ([Observability](observability.md#alerts)). No production environment exists yet (the cloud is not chosen, see the [Production plan](production-plan.md)). Shared environments will run the Helm chart in `infra/helm/anum` on Kubernetes ([Deployment](deployment.md)), so procedures give `kubectl`/`helm` commands for release `anum` in namespace `<ns>` where they differ from the local compose stack; `<api>` stands for the API's public URL. Everything that needs an owner decision is marked **Proposal**.

Ground rules for every procedure:

- Tenant context stays explicit. Never disable RLS, connect the API as a superuser, or grant `BYPASSRLS` to fix an incident. Read tenant data only through the API or as `anum_app` with `anum.tenant_id`/`anum.workspace_id` set. The only roles that see across tenants are the outbox relay (unpublished events only), `anum_maintenance` (ids of rows that need maintenance, no content, no writes; [Multi-tenancy](multi-tenancy.md#maintenance-role)) and the backup role.
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

## Deploy and Rollback

Deploys go through the workflows only: `deploy-staging.yml` on every push to `main`, `deploy-production.yml` by hand with the commit staging deployed and the `production` approval ([Deployment](deployment.md)).

1. **Before a production deploy:** the commit is green in CI and healthy in staging; note the current revision (`helm history anum -n <ns>`; the workflow writes it to the run summary).
2. **During:** the migration hook runs first (`kubectl -n <ns> logs job/anum-migrate`). If it fails, the release fails and Helm rolls back to the previous release; old pods were never replaced. Read the Job log, fix forward, deploy again.
3. **After:** `helm test anum -n <ns> --logs`, the health check, and the API dashboard for five minutes.
4. **Roll back** when a deploy correlates with errors: run `deploy-production.yml` with `rollback_to_revision=<previous revision>` (or `helm rollback anum <revision> -n <ns> --wait` with break-glass access), then `helm test`. A rollback restores images, configuration and manifests, not the schema: migrations stay applied, which is safe because they are written expand/contract. A migration that must be undone needs a reviewed down-migration release or a restore ([Restoring for real](#restoring-for-real)).
5. **Stuck rollout** (`helm upgrade` timed out): `kubectl -n <ns> get pods -l app.kubernetes.io/instance=anum`, then `describe` the failing pod. `CreateContainerConfigError` means a Secret or key is missing ([Deployment](deployment.md#secrets)); `CrashLoopBackOff` with `InsecureConfigurationError` or `AuthConfigurationError` means configuration the API refuses ([Security](security.md#startup-policy)).

## A Pod Was Refused by Admission

Symptom: `helm upgrade` (or `kubectl apply`, a CronJob run, a scale-up) fails with `admission webhook "policy.sigstore.dev" denied the request`, or a ReplicaSet shows `FailedCreate` events naming the policy. The release namespace is labelled `policy.sigstore.dev/include=true`, so only images signed and SBOM-attested by this repository's deploy workflows on `main` run there ([Admission policy](deployment.md#admission-policy)). With `--rollback-on-failure`, the previous release keeps serving.

1. **Read the reason.** `kubectl -n <ns> get events --field-selector reason=FailedCreate` or the Helm error. The message names the failing policy and the image:
   - `failed policy: anum-<image>-signature` with `no signatures found` or `no matching signatures`: the digest is unsigned, or another identity signed it (another workflow, branch or repository, or the wrong environment's workflow for the web image). Check by hand: `cosign verify <repo>@<digest> --certificate-identity https://github.com/<owner>/<repo>/.github/workflows/<deploy-staging.yml|deploy-production.yml>@refs/heads/main --certificate-oidc-issuer https://token.actions.githubusercontent.com`.
   - `failed policy: anum-<image>-sbom`: the signature is fine but the CycloneDX attestation is missing. Check with `cosign verify-attestation --type cyclonedx` and the same identity.
   - `no matching policies`: an image that no ANUM policy covers (a third-party image, a typo in the repository, an uppercase owner) is in a labelled namespace. Third-party services belong in their own unlabelled namespace.
   - `failed calling webhook` or `connection refused`: the controller is down, and with `failurePolicy: Fail` nothing new starts in labelled namespaces. Go to step 3.
2. **Fix forward, never by turning enforcement off.** Re-run `deploy-staging.yml` for the commit (it builds, signs and attests again), or promote a digest staging signed. A `helm rollback` to a revision whose images predate signing is refused by design: deploy a signed commit instead. If the identity is wrong because the repository was renamed or moved, update `github.repository` in the `anum-admission` release as a reviewed change.
3. **Controller down.** `kubectl -n cosign-system get pods` and `kubectl -n cosign-system logs deployment/policy-controller-webhook`. Common causes: no route to GHCR, Rekor or the Sigstore TUF mirror (check egress), or an expired webhook certificate (restart the deployment). Reinstall with `bash infra/helm/anum-admission/controller/install.sh`. Running pods are not affected; only new pods wait.
4. **Break glass (SEV1 only, incident lead approves, recorded in the incident channel).** To start an urgent fix that cannot be signed in time, a cluster admin may set the policy to warn (`helm upgrade anum-admission infra/helm/anum-admission --reuse-values --set mode=warn`) or remove the namespace label. Restore `mode=enforce` or the label as soon as a signed release is deployed, and list the window in the post-incident review. The deploy identity cannot do this, by design.

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
3. Workers: check the worker processes are up and polling `ANUM_TEMPORAL_TASK_QUEUE` (Temporal UI, task queue view shows pollers; on Kubernetes `kubectl -n <ns> logs deployment/anum-worker | grep "ANUM worker polling"`). No pollers means no progress: restart (`kubectl -n <ns> rollout restart deployment/anum-worker`; workers shut down cleanly on SIGTERM and Temporal retries their in-flight activities) or scale (`worker.replicas`). Workers need the same `ANUM_*` configuration as the API; the chart gives both the same ConfigMap and Secret.
4. A single stuck run: find the workflow `anum-run/<tenant>/<workspace>/<task>` in the Temporal UI. Its run state is in PostgreSQL (`agent_runs.checkpoint`). Typical cases:
   - `waiting_approval`: by design; the run waits for a person (re-read at least hourly if a signal was lost).
   - `tool_ready` with no live workflow: `POST /api/v1/agent-runs/{id}/resume` restarts it.
   - Failed with "outcome unknown": a worker died while a non-idempotent or approved high-risk tool ran. The runtime never repeats it; a person must check the external system and decide. Do not force a re-run.
5. Activity errors from the model provider show as `error` with a model `error.type` in the trace; see [Model cost spike](#model-cost-spike) for provider problems.
6. Never terminate workflows in bulk or edit `agent_runs` rows by hand; cancel through the API (`POST /api/v1/tasks/{id}/cancel`) so the cancellation is evented and audited.

## Model Cost Spike

Alerts: `AnumModelCostSpike`, `AnumModelHourlySpendHigh`, `AnumModelErrorRate`.

1. Open **ANUM model gateway: cost and usage**: which provider and model, input or output tokens, since when.
2. Find the source: request traces with `generate_text` spans in the window, grouped by `anum.tenant_id`, show which workspace drives it. `GET /api/v1/model-budgets` (as an owner of the workspace) shows this month's usage for the workspace and its organization, and `model_budget_threshold` log lines name tenants that crossed 80% or 100% of a budget. Per-tenant cost metrics do not exist (labels stay bounded).
3. Contain: switch the workspace to a cheaper model or the mock provider (`PUT /api/v1/model-config` as its owner, or with the owner's agreement), or pause runs for that workspace. With the owner's agreement, set a monthly budget (`PUT /api/v1/model-budgets/workspace` or `/tenant`, audited; `{"monthly_token_limit": 0}` stops all model calls until it is raised). For a runaway loop, cancel its tasks. Revoke the provider key at the provider if it may have leaked.
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

- On Kubernetes, the chart's optional backup CronJob (`backup.enabled`, image `infra/backup/Dockerfile`) runs this daily into the volume claim `backup.persistentVolumeClaim` with the `secrets.backup` login; run one now with `kubectl -n <ns> create job --from=cronjob/anum-backup anum-backup-manual` ([Deployment](deployment.md)). Shipping the files from that volume to the encrypted, versioned bucket below is still open.
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

1. **Generate a key:** `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Store it only in the secret store.
2. **Deploy both keys:** `ANUM_SECRETS_KEY=<new>,<old>` to API and workers together. New and updated configs are encrypted with the new key; old ciphertexts still decrypt.
3. **Dry run.** From a host with the API image and the same `ANUM_*` configuration (`ANUM_REPOSITORY_BACKEND=postgresql`, `ANUM_DATABASE_URL`, the two-key `ANUM_SECRETS_KEY`), run:

   ```bash
   python -m anum_api.rotate_secrets --dry-run
   ```

   - Use an operator login with the application login's table grants, subject to RLS like it, and a member of `anum_maintenance`. Never use a superuser or a `BYPASSRLS` login.
   - It prints a JSON report: `scanned`, `rotated` (would rotate), `already_current`, `failed`, `failures` (tenant and workspace ids only) and a `correlation_id`. It writes nothing.
4. **Re-encrypt:** `python -m anum_api.rotate_secrets`.
   - For each workspace it decrypts with any configured key, encrypts with the first key, and writes the ciphertext.
   - It writes an `audit_records` row (`action` `secrets.rotated`, `actor` `system:rotate-secrets`, the report's `correlation_id`) in the same transaction, inside that tenant's RLS context ([Multi-tenancy](multi-tenancy.md#maintenance-role)).
   - It leaves `updated_at` alone, because the owner's configuration did not change.
   - It is idempotent: a re-run after an interruption only handles what is left, and a fully rotated database reports `already_current` for every row.
   - A workspace whose owner re-saves the key at the same moment keeps the owner's value (`changed_concurrently`).
   - Exit status: `0` when nothing failed, `1` when a ciphertext could not be decrypted with any configured key (each one is audited with outcome `failed`), `2` on a configuration error.
5. **Check:** run it again. Expect `rotated: 0`, `failed: 0` and `already_current` equal to `scanned`.
6. **Drop the old key:** deploy `ANUM_SECRETS_KEY=<new>` alone. Do this only after step 5 is clean. A row that still needs the old key fails with `SecretDecryptionError` (the workspace's model calls fail; nothing else does).
7. **Failed rows:** the key that encrypted them is not in the list. Restore that key to the list and re-run, or ask the workspace owner to re-save the provider key (`PUT /api/v1/model-config`).
8. **Suspected key leak:** rotate immediately and also ask workspace owners to rotate their provider keys, since the ciphertexts may have been copied.

## Voice Transcript Retention

`30_days` voice transcripts are unreadable from their `expires_at` on. `python -m anum_api.voice_retention` deletes the rows ([Voice](voice.md#storage-and-retention)).

- **Schedule:** run it at least daily with the API's configuration. On Kubernetes the chart's `anum-voice-retention` CronJob does (03:17 UTC, `voiceRetention.schedule`); run it now with `kubectl -n <ns> create job --from=cronjob/anum-voice-retention anum-voice-retention-manual`. The login must be a member of `anum_maintenance` (`infra/helm/bootstrap-database.sql` grants it to `anum_app`).
- **Dry run:** `--dry-run` prints how many sessions and segments would be deleted, as counts only.
- **Safe to repeat:** an interrupted run is finished by the next one.
- **Never** delete transcript rows by hand or as a superuser. If the job fails with a permission error, the login lost `anum_maintenance`.

## Automation Scheduler

The scheduler is described in [Automation](automation.md#scheduler).

- **Schedules do not fire:**
  - Check `ANUM_AUTOMATION_SCHEDULER_ENABLED=true` on at least one API replica.
  - Check that the API login is a member of `anum_maintenance`. Without it, `Automation scheduler pass failed` with a permission error appears in the logs.
  - A schedule's `next_run_at` (API or `automation_schedules`) shows when it will fire. It is null while the schedule is disabled.
- **One schedule keeps failing:** `Automation schedule <id> could not be fired` is logged every pass and the schedule stays due. Fix the cause, or disable the schedule through the API.
- **Two runs for one schedule** always have different `idempotency_key` values, that is, different fire times. Replicas racing cannot produce two runs for one fire time, because the key is unique per workspace.

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
