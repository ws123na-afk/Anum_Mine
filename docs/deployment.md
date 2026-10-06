# Deployment

ANUM deploys to any Kubernetes cluster with the Helm chart in `infra/helm/anum`. The chart is cloud-neutral: managed or self-hosted PostgreSQL, NATS, Temporal, Valkey, object storage and Keycloak are external dependencies, and every credential comes from a Kubernetes Secret the chart references by name only. Choosing a cloud (see [Production plan](production-plan.md)) means filling in values, Secrets and cluster access; the chart and workflows stay the same. Image builds and local compose are described in [Infrastructure](infrastructure.md).

## What the Chart Deploys

| Workload | Kind | Image and command | Notes |
|---|---|---|---|
| API | Deployment, Service, PodDisruptionBudget, HorizontalPodAutoscaler | API image, `uvicorn anum_api.main:app` on 8000 | Startup, liveness and readiness probes on `/health`; rolling update with `maxUnavailable: 0`. |
| Temporal worker | Deployment, PodDisruptionBudget | API image, `python -m anum_api.worker` | No port and no HTTP probe. On SIGTERM it stops polling, hands in-flight activities back to Temporal and exits 0; `terminationGracePeriodSeconds: 60`. |
| Web | Deployment, Service, PodDisruptionBudget | Web image, unprivileged nginx on 8080 | Probes on `/healthz`; `ANUM_CSP_CONNECT_SRC` from `web.cspConnectSrc`. |
| Ingress | Ingress | | One host for the web client, one for the API. TLS from an existing Secret or cert-manager (`ingress.tls.clusterIssuer`). |
| Migrations | Job, `pre-install,pre-upgrade` hook | API image, `python -m alembic upgrade head` | Runs with the migration login before any new pod starts. |
| Voice retention | CronJob, daily | API image, `python -m anum_api.voice_retention` | Also purges retrieval index rows of expired memories. [Runbooks](runbooks.md#voice-transcript-retention). |
| Backup (optional) | CronJob, daily | Backup image (`infra/backup/Dockerfile`), `anum_backup.py backup` | Off by default; [Runbooks](runbooks.md#backups). |
| Network policies | NetworkPolicy | | Default deny for every pod of the release, then only the traffic each workload needs. |
| `helm test` | Pod, `test` hook | API image | `/health`, the configured environment, CSP header, 401 without a token, `/docs` disabled, web `/healthz`. |

Every pod runs as a non-root user (API uid 10001, web uid 101) with a read-only root filesystem, all capabilities dropped, `seccompProfile: RuntimeDefault`, no privilege escalation and resource requests and limits. Writable paths are `emptyDir` volumes: `/tmp`, `/app/.anum` and `/app/.anum-data` for the API image, `/tmp` and `/etc/nginx/conf.d` for nginx. Each workload has its own ServiceAccount with `automountServiceAccountToken: false`; none of them calls the Kubernetes API. Annotate the ServiceAccounts (`serviceAccount.annotations`) to bind a cloud workload identity for object storage.

## Prerequisites

- Kubernetes 1.29 or newer with a CNI that enforces NetworkPolicy, an ingress controller (the overlays assume ingress-nginx) and optionally cert-manager.
- PostgreSQL 16 or newer with the `pgvector` extension available.
- NATS with JetStream, a Temporal cluster (namespace per environment), Valkey (or Redis), S3-compatible object storage with versioning, the Keycloak realm from `infra/keycloak/anum-realm.json` served over https, and optionally an OpenTelemetry collector ([Observability](observability.md)).
- The images from `deploy-staging.yml`: `ghcr.io/<owner>/anum-api:<sha>`, `anum-web:<sha>` (staging bundle) and `anum-backup:<sha>`. Production builds its own web image (`anum-web:<sha>-production`), because the API origin and issuer are compiled into the bundle.

## Database Roles

Run `infra/helm/bootstrap-database.sql` once per database as the cluster admin, with the passwords from the secret store passed as psql variables (the file header shows the command). It is idempotent. It creates:

| Role | Login | Used by | Privileges |
|---|---|---|---|
| `anum_migrator` | yes | the migration Job (`secrets.migration`) | Owns the database, so it owns the public schema and every table the migrations create. Never used by the API. |
| `anum_app` | yes | API, worker, voice retention (`ANUM_DATABASE_URL` in `secrets.app`) | `SELECT, INSERT, UPDATE, DELETE` on tables and usage on sequences through default privileges; subject to RLS (`FORCE ROW LEVEL SECURITY`); member of `anum_maintenance`, and of `anum_membership_reader` without inheritance. |
| `anum_relay` | yes | the outbox relay (`ANUM_OUTBOX_DATABASE_URL` in `secrets.app`) | Member of `anum_outbox_relay` only ([Events](events.md)). |
| `anum_outbox_relay` | no | granted to `anum_relay` | Column grants from migration `0007`. |
| `anum_maintenance` | no | granted to `anum_app` `WITH INHERIT FALSE, SET TRUE` | Discovery-only grants from migration `0011` ([Multi-tenancy](multi-tenancy.md#maintenance-role)). The scheduler and the retention job need it. Its policies admit rows across tenants, so an inheriting grant would widen the app role's own queries; re-run `bootstrap-database.sql` on existing databases to switch the grant to `INHERIT FALSE`. |
| `anum_membership_reader` | no | granted to `anum_app` `WITH INHERIT FALSE, SET TRUE` | Read-only grants from migration `0013` for `GET /api/v1/me/workspace-memberships` ([Multi-tenancy](multi-tenancy.md#membership-directory-role)). Without the grant that route fails; with an inheriting grant its policies would widen the app role's own reads. |
| `anum_backup` | yes | backup CronJob only (`secrets.backup`) | `BYPASSRLS` and `pg_read_all_data`; create it only where the backup job runs, and nowhere in the application's configuration. |

The script also creates the `vector` extension, which is not a trusted extension and needs the admin. No login is a superuser or has `BYPASSRLS` except the backup login, and nothing is `SECURITY DEFINER`. The NOLOGIN roles are pre-created so the migration login needs no `CREATEROLE`.

## Secrets

The chart never renders a Secret. Create these in the release namespace before installing, with External Secrets, Sealed Secrets, a CSI secret driver or `kubectl create secret`. Keys are named after the environment variables they feed, so an `ExternalSecret` maps remote keys straight onto them.

| Secret (`values` key) | Keys | Required |
|---|---|---|
| `secrets.app.name` (default `anum-app`) | `ANUM_SECRETS_KEY` (comma-separated Fernet keys, the first encrypts), `ANUM_DATABASE_URL` (`anum_app`) | yes |
| | `ANUM_OUTBOX_DATABASE_URL` (`anum_relay`), `ANUM_VALKEY_URL`, `ANUM_MODEL_API_KEY`, `ANUM_S3_ACCESS_KEY`, `ANUM_S3_SECRET_KEY`, `ANUM_EXTERNAL_WEBHOOK_URL`, `ANUM_EXTERNAL_WEBHOOK_API_KEY` | when used |
| `secrets.migration.name` (default `anum-migrate`) | `ANUM_DATABASE_URL` (`anum_migrator`) | when `migration.enabled` |
| `secrets.backup.name` (default `anum-backup`) | `ANUM_BACKUP_DATABASE_URL` (`anum_backup`) | when `backup.enabled` |
| `ingress.tls.secretName` | TLS certificate | unless cert-manager issues it |

Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and rotate it as in [Runbooks](runbooks.md#rotating-anum_secrets_key). When the cloud provides workload identity for object storage, leave the S3 keys out and annotate the ServiceAccounts instead.

## Values

`values.yaml` holds production-shaped defaults and documents every `ANUM_*` setting from `services/api/anum_api/settings.py` under `config` (rendered into one ConfigMap shared by the API, the worker and the jobs; a key in `secrets.app` wins over the same key in `config`). Overlays:

- `values-staging.yaml`: `ANUM_ENVIRONMENT=staging`, mock model allowed, smaller replica counts, placeholder `*.example` hosts.
- `values-production.yaml`: `ANUM_ENVIRONMENT=production`, more replicas, zone spread, backup CronJob on, NetworkPolicy egress narrowed to placeholder CIDRs.
- `ci/kind-values.yaml`: the CI test release only (see [Checks](#checks)).

Replace the placeholders with real hosts, endpoints and CIDRs in the overlay or in the `STAGING_HELM_VALUES` / `PRODUCTION_HELM_VALUES` repository variables (YAML, not secret). The chart fails to render, before anything reaches the cluster, when a value would make the API or worker refuse to start or would break a shared environment:

- `ANUM_ENVIRONMENT` empty, `local` or `test`; `ANUM_AUTH_MODE` other than `oidc`; an issuer that is not `https`.
- `ANUM_CORS_ORIGINS` empty, with a wildcard, a local host or a non-`https` origin.
- `ANUM_REPOSITORY_BACKEND` other than `postgresql`; NATS or Temporal selected without an address; the Temporal runtime with the worker disabled.
- Object storage other than `s3` with more than one API replica, with the HPA, or in production.
- `ANUM_MODEL_PROVIDER=mock` in production; rate limiting turned off.
- Missing Secret names, `web.cspConnectSrc`, ingress hosts, or the backup volume claim.
- A Keycloak admin credential (`KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KC_BOOTSTRAP_ADMIN_USERNAME`, `KC_BOOTSTRAP_ADMIN_PASSWORD`) in `config` or `extraEnv`, whatever its value: ANUM never uses Keycloak's admin account, and both are rendered into a ConfigMap.

The API checks the rest at startup (compose credentials in database URLs or S3 keys, Keycloak's compose `admin/admin` if it reaches the API's environment, missing `ANUM_SECRETS_KEY`; [Security](security.md#startup-policy)). Keycloak itself is not deployed by the chart, so neither the chart nor the API can see the password of the Keycloak you run: rotating its bootstrap admin away from `admin/admin` is a release-gate item ([anum-release-gate](../.claude/skills/anum-release-gate/SKILL.md)).

Settings to get right per environment:

- `api.forwardedAllowIps`: the ingress controller's pod or load balancer CIDR, so client IPs for rate limiting come from `X-Forwarded-For` only when the proxy sent it.
- `web.cspConnectSrc`: the API origin and the Keycloak issuer origin.
- `ANUM_RUN_LOCK_BACKEND=valkey` and `ANUM_RATE_LIMIT_BACKEND=valkey` (defaults) need `ANUM_VALKEY_URL` in `config` or in `secrets.app`.
- Ingress annotations for server-sent events (`/api/v1/events/stream`): unbuffered responses and a long read timeout (the overlays show ingress-nginx's).

## Deploying

```bash
# 1. Once per database (as the admin):
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -v migrator_password=... -v app_password=... \
  -v relay_password=... -f infra/helm/bootstrap-database.sql
# 2. Secrets in the namespace (normally from the secret store, not by hand):
kubectl -n anum-staging create secret generic anum-app --from-literal=ANUM_SECRETS_KEY=... \
  --from-literal=ANUM_DATABASE_URL=postgresql+psycopg://anum_app:...@db:5432/anum \
  --from-literal=ANUM_OUTBOX_DATABASE_URL=postgresql+psycopg://anum_relay:...@db:5432/anum
kubectl -n anum-staging create secret generic anum-migrate \
  --from-literal=ANUM_DATABASE_URL=postgresql+psycopg://anum_migrator:...@db:5432/anum
# 3. Install or upgrade: migrations run first, then the rollout.
helm upgrade --install anum infra/helm/anum --namespace anum-staging \
  -f infra/helm/anum/values-staging.yaml \
  --set-string image.api.digest=sha256:<digest> --set-string image.web.digest=sha256:<digest> \
  --wait --timeout 15m --rollback-on-failure
helm test anum --namespace anum-staging --logs
```

The chart is checked with Helm 4 (`v4.3.0`); Helm 3 renders it too, with `--atomic` in place of `--rollback-on-failure`.

### Cluster access

`deploy-staging.yml` deploys on every push to `main` once the repository variable `STAGING_DEPLOY_TARGET=kubernetes` is set. `deploy-production.yml` is started by hand with the commit SHA staging already deployed, and runs in the `production` GitHub environment: give that environment required reviewers, so every production deploy and rollback waits for a manual approval. Both stay skipped while their `*_DEPLOY_TARGET` variable is unset.

| Setting | Where | Purpose |
|---|---|---|
| `STAGING_DEPLOY_TARGET`, `PRODUCTION_DEPLOY_TARGET` | repository variables | `kubernetes` turns the deploy job on. |
| `STAGING_KUBECONFIG`, `PRODUCTION_KUBECONFIG` | `staging` / `production` environment secrets | Kubeconfig of a deploy identity limited to the release namespace. Alternatively replace the "Configure cluster access" step with the cloud's GitHub OIDC login (the jobs have `id-token: write`); it must leave a kubeconfig at `$KUBECONFIG`. |
| `STAGING_NAMESPACE`, `PRODUCTION_NAMESPACE` | repository variables | Release namespace (defaults `anum-staging`, `anum`). It and its Secrets must exist. |
| `STAGING_HELM_VALUES`, `PRODUCTION_HELM_VALUES` | repository variables | YAML layered over the overlay: real hosts, endpoints, CIDRs. |
| `STAGING_API_URL`, `STAGING_OIDC_ISSUER`, `PRODUCTION_API_URL`, `PRODUCTION_OIDC_ISSUER` | repository variables | Compiled into the web bundle; the API URL is also smoke-tested after the deploy. |
| `STAGING_WEB_URL`, `PRODUCTION_WEB_URL` | repository variables | Environment URL shown on the run. |

The deploy identity needs, in the release namespace only: create, update, patch and delete on Deployments, Services, ConfigMaps, ServiceAccounts, Jobs, CronJobs, PodDisruptionBudgets, HorizontalPodAutoscalers, Ingresses, NetworkPolicies and Pods (for `helm test`), get and list on Pods, Events and Secrets of type `helm.sh/release.v1` (Helm stores release state in Secrets), and read on ReplicaSets for `--wait`. It does not need to read the application's Secrets' values, and it needs no cluster-scoped rights: the [admission policy](#admission-policy) is installed by a cluster admin, so the deploy identity cannot change or escape it.

### Image supply chain

Every image the deploy workflows deploy is scanned, described and signed in CI, and verified again right before `helm upgrade`. `infra/supply-chain/images.sh` holds the steps for both workflows:

| Step | Where | What fails the run |
|---|---|---|
| Trivy scan (`trivy image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1`, as in the CI Docker images job) | `deploy-staging.yml` images job, before anything is pushed; `deploy-production.yml` web-image job before its push; the production deploy job again on all three digests (new advisories since staging block the promotion) | Any HIGH or CRITICAL vulnerability with a fixed version, or a secret in a layer. Exceptions go in `.trivyignore` with the CVE, a reason and an expiry; none exist. |
| SBOM (`trivy image --format cyclonedx`) | Same jobs, from the image that is pushed | An empty SBOM. |
| Keyless signature and SBOM attestation (`cosign sign`, `cosign attest --type cyclonedx`), annotated `commit=<sha>` | On the pushed digest, in the job that built it (`id-token: write`): GitHub OIDC is exchanged for a short-lived Fulcio certificate that names the workflow; there is no signing key to store or rotate | Signing errors. |
| Verification (`cosign verify`, `cosign verify-attestation --type cyclonedx`) | Deploy jobs, before cluster access and `helm upgrade` | A signature or SBOM attestation that is missing, made by another workflow, repository or branch than `main`, or annotated with another commit. |
| Deploy by digest | `helm upgrade` gets `image.*.digest` / `backup.image.digest` (the chart renders `repository@sha256:...`) | Nothing else can be pulled in place of the verified digest. |

Expected signers (certificate identity, issuer `https://token.actions.githubusercontent.com`):

- Staging API, web and backup images, and the API and backup images production promotes: `https://github.com/<owner>/<repo>/.github/workflows/deploy-staging.yml@refs/heads/main`.
- The production web image (built per environment): `.../deploy-production.yml@refs/heads/main`, so dispatch `deploy-production.yml` from `main`.

To check an image by hand (for example as release evidence):

```bash
cosign verify ghcr.io/<owner>/anum-api@sha256:<digest> \
  --certificate-identity "https://github.com/<owner>/<repo>/.github/workflows/deploy-staging.yml@refs/heads/main" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com -a commit=<sha>
cosign verify-attestation --type cyclonedx ghcr.io/<owner>/anum-api@sha256:<digest> \
  --certificate-identity "..." --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  | jq -r '.payload' | base64 -d | jq '.predicate.components | length'
```

Tools: Trivy `v0.75.0` (the version the CI Docker images job runs) and cosign `v2.6.5` are built with `go install` at those versions, so the Go module proxy and checksum database (`sum.golang.org`) verify their source, as for Helm, kind and kubeconform; no third-party action is added. Keyless signatures are written to the public Rekor transparency log, which records the repository and workflow names, so the staging workflow signs only once `STAGING_DEPLOY_TARGET` is set (scanning and SBOMs run on every push). Images pushed before that are unsigned and cannot be deployed or promoted; rebuild them by re-running the workflow.

The deploy workflows' check protects the workflows' own deploys. The [admission policy](#admission-policy) enforces the same rule in the cluster for anyone who creates a pod.

### Admission policy

In the staging and production clusters, the release namespace admits ANUM images only when they carry a keyless signature and a signed CycloneDX SBOM attestation made by this repository's deploy workflows on `main`. This holds whoever creates the pod: a person with break-glass `kubectl`, a leaked deploy credential, or a `helm rollback` to an unsigned revision.

**Controller: Sigstore policy-controller.** It is the Sigstore project's own admission controller and uses the same verification code as cosign: the same Fulcio certificate identities, the Rekor transparency log and the TUF trust root. So the cluster checks exactly what `images.sh verify` checks before `helm upgrade`. It is cloud-neutral (Apache-2.0, any conformant cluster). It does one job, with one CRD (`ClusterImagePolicy`) and a namespace opt-in label, so it adds less to the cluster than a general policy engine. Kyverno `verifyImages` would work too, and is the better choice in a cluster that already runs Kyverno for other policies. Translate the policies in `infra/helm/anum-admission` one to one if so.

**Where it lives.** The policies are a separate chart, `infra/helm/anum-admission`, not part of `infra/helm/anum`. ClusterImagePolicies are cluster-scoped, and the deploy identity is limited to the release namespace ([Cluster access](#cluster-access)). Keeping the policy out of the release means a cluster admin installs and changes it, and the deploy identity can neither remove the policy nor relabel its namespace to escape it.

| What | Enforced by | Refused |
|---|---|---|
| `anum-api-signature`, `anum-web-signature`, `anum-backup-signature` | One keyless authority each. The issuer is `https://token.actions.githubusercontent.com` and the subject is `https://github.com/<owner>/<repo>/.github/workflows/<signer>@refs/heads/main`. Signers come from the values: staging uses `deploy-staging.yml` for all three images; production uses `deploy-staging.yml` for the API and backup images it promotes and `deploy-production.yml` for its web image. | A pod, Deployment, ReplicaSet, StatefulSet, Job or CronJob whose image is in `ghcr.io/<owner>/anum-{api,web,backup}` without such a signature in Rekor. The API image also runs the worker, migration Job, CronJobs and `helm test` pod, so those are covered too. |
| `anum-api-sbom`, `anum-web-sbom`, `anum-backup-sbom` | Same identities, with an attestation of predicate type `cyclonedx` (`cosign attest --type cyclonedx`). | Images without the signed SBOM attestation. policy-controller requires every matching policy (AND) and at least one authority inside a policy (OR), so signature and SBOM are separate policies. |
| `no-match-policy: deny` (controller install) | policy-controller | Any image in a labelled namespace that no policy matches. Run NATS, Temporal, Valkey and other third-party services in their own unlabelled namespaces. The production overlay's placeholder NATS in the `anum` namespace must move before the label is set. |
| Namespace label `policy.sigstore.dev/include=true` | The controller's `namespaceSelector` (`controller/policy-controller-values.yaml`) | Enforcement applies in labelled namespaces only. Label the release namespace (`anum-staging`, `anum`) and nothing else ANUM-specific. |
| `failurePolicy: Fail` | Kubernetes API server | While the webhook is down, nothing new starts in a labelled namespace ([Runbooks](runbooks.md#a-pod-was-refused-by-admission)). |

The chart refuses (fails to render) a signer other than `deploy-staging.yml` or `deploy-production.yml`, an empty signer list, a repository with a tag, digest or glob, a malformed `github.repository`, non-https Fulcio, Rekor or GitHub URLs, and a `mode` other than `enforce` or `warn`. The branch is fixed to `refs/heads/main`; there is no value to change it.

Install, once per cluster, as a cluster admin (Helm `v4.3.0`):

```bash
# 1. The controller: chart 0.10.8 (policy-controller v0.13.1) pulled from ghcr.io by
#    manifest digest; the controller image is pinned by digest in its values.
bash infra/helm/anum-admission/controller/install.sh
# 2. The policies (replace owner/repo; image repositories use the lowercase owner).
helm upgrade --install anum-admission infra/helm/anum-admission \
  -f infra/helm/anum-admission/values-staging.yaml \
  --set github.repository=<owner>/<repo> \
  --set images.api.repository=ghcr.io/<owner>/anum-api \
  --set images.web.repository=ghcr.io/<owner>/anum-web \
  --set images.backup.repository=ghcr.io/<owner>/anum-backup
kubectl get clusterimagepolicies
# 3. Opt the release namespace in, once the running digests are signed (check them with
#    cosign verify first). Running pods are not evicted; new ones are checked.
kubectl label namespace anum-staging policy.sigstore.dev/include=true
```

For production, use `values-production.yaml` and the `anum` namespace. If staging and production share a cluster, the policies are cluster-wide: list both workflows as web signers. To roll out gradually, install with `--set mode=warn` (non-compliant pods are admitted with a warning) and switch to `enforce` after a clean deploy. The chart accepts `warn`; `infra/helm/ci/lint.sh` fails if a committed overlay renders it.

Consequences for operators:

- A Deployment that fails the policy is refused when it is applied, so `helm upgrade` fails at once and `--rollback-on-failure` keeps the previous release running.
- `helm rollback` to a revision whose images predate signing (pushed before `STAGING_DEPLOY_TARGET` was set) is refused. Redeploy a signed commit instead.
- The `commit=<sha>` annotation is checked by the deploy job only. Admission checks the signer identity and the SBOM, not which commit a digest belongs to.
- policy-controller v0.13 verifies image signatures in cosign's classic format only (attestations in either format). `images.sh` uses cosign `v2.6.5`, which signs in that format. Before moving to cosign v3, whose default is the new bundle format, pass `--new-bundle-format=false` to `cosign sign` or upgrade the controller to a version that reads bundles, and let the kind job prove it.
- The controller fetches signatures from GHCR, so a cluster that pulls private images needs the pull secret given to the controller (`webhook.env`/`imagePullSecrets` of its chart, or a public package).

Pinned versions: the policy-controller chart `0.10.8` at manifest digest `sha256:9ff3ca6ae4a0155de4dd667f8b760e56e1bd5c8233a0321ad870a654ce022f2f`, and the controller image `v0.13.1` at `sha256:0bcd60beb93f4427c29cf3a669743caf58490e98ded4380c33c09f092734a6ab`. One image in that chart is not pinned: the chart's `post-delete` lease cleanup hook uses `cgr.dev/chainguard/kubectl:latest-dev`, which runs only on `helm uninstall`. Uninstall with `--no-hooks` and delete the leftover leases by hand, or pin `leasescleanup.image.version` to a digest. kubeconform validates the policies against `infra/helm/ci/schemas/policy.sigstore.dev/clusterimagepolicy_v1beta1.json`, generated from the pinned chart's CRD by `infra/helm/ci/crd-schema.py`. The kind job regenerates that schema from the CRD it installs and fails if they differ. To upgrade the controller, change the chart version and digest in `controller/install.sh` and the image digest in `controller/policy-controller-values.yaml`, regenerate the schema, and let the kind job run.

## Migrations

The migration Job runs `python -m alembic upgrade head` as `anum_migrator` before any pod of a new release starts, on install and on every upgrade. A failed migration fails the release: with `--rollback-on-failure` Helm restores the previous release, and the running pods were never replaced. The Job is kept until the next release (`kubectl logs job/anum-migrate`) and records the applied revision in its log; `select version_num from alembic_version` shows it too (release evidence, [Production readiness gates](production-readiness.md)).

Migrations are forward-only in deployment. Because the old pods keep running against the new schema during a rolling update, and a rollback runs old code against it as well, every migration must be backward compatible with the previous release: add columns and tables first (expand), switch code, and remove old ones in a later release (contract).

## Rollback

```bash
helm history anum -n anum
helm rollback anum <revision> -n anum --wait --timeout 15m
helm test anum -n anum --logs
```

In production, start `deploy-production.yml` with `rollback_to_revision` set; it waits for the `production` approval, rolls back, runs `helm test` and the health check. A rollback restores the previous release's images, ConfigMap and manifests. It does not reverse migrations (rollback runs no hooks), which is safe while migrations follow expand/contract. To undo a migration, deploy a new release with a reviewed down-migration or restore from backup ([Runbooks](runbooks.md#restoring-for-real)); never run `alembic downgrade` against production ad hoc. The CI kind job performs an upgrade and a `helm rollback` to revision 1 on every run.

## Scaling

- **API:** stateless; the HPA (`api.hpa`, CPU 70 percent by default) scales between `minReplicas` and `maxReplicas`; the PDB keeps at least one pod during node drains. More than one replica requires `ANUM_OBJECT_STORAGE_BACKEND=s3` (the chart enforces it) and Valkey for shared rate limits and run locks. The automation scheduler is safe on every replica ([Automation](automation.md#scheduler)); the outbox relay is multi-instance safe ([Events](events.md)).
- **Worker:** scale `worker.replicas` with the Temporal task queue backlog (`AnumTemporalActivityFailures` and queue depth, [Observability](observability.md)). Each replica polls the same task queue; Temporal distributes work.
- **Web:** static files; two or three replicas cover availability.
- Resource requests and limits are in `values.yaml` and the production overlay; measure under load and adjust ([Scaling](scaling.md)).

## Network Policies

A default-deny policy selects every pod of the release. Then:

- API: ingress on 8000 from `networkPolicy.ingressFrom` (the ingress controller's namespace) and from the release's `helm test` pod; egress to DNS and `networkPolicy.appEgress`.
- Worker: no ingress; egress to DNS and `networkPolicy.appEgress`.
- Web: ingress on 8080 from the ingress controller and the test pod; no egress.
- Migration, retention and backup jobs: egress to DNS and `networkPolicy.jobEgress` (PostgreSQL) only.

`appEgress` defaults to the dependency ports (5432, 4222, 7233, 6379, 443, 4317, 4318) to any address; the production overlay shows how to narrow each to CIDRs or namespaces. The CI kind cluster enforces these policies (kindnet), so a missing rule fails the smoke test.

## Checks

The CI job **Helm deploy (kind)** (`.github/workflows/ci.yml`) runs on every PR and push to `main`:

1. `infra/helm/ci/lint.sh`: `helm lint --strict` for the staging, production and CI values; `helm template` piped into `kubeconform -strict` against Kubernetes 1.37.0 schemas pinned to a commit; no rendered Secret, no writable root filesystem, no mounted token; and fourteen refusals (local or test environment, header auth, http, local and wildcard CORS origins, the memory repository, mock model in production, local storage with replicas, a non-https issuer, a Keycloak admin credential in `config` or `extraEnv`, the worker disabled with Temporal, backup without a volume). The admission chart (`infra/helm/anum-admission`) gets `helm lint --strict` and kubeconform for its staging, production and kind values. kubeconform validates the ClusterImagePolicies against the committed CRD schema, with no skipped kinds. The script also checks each overlay's signer identities, issuer, SBOM predicate type and `mode: enforce`, plus eight refusals (another workflow as signer, no signer, `mode=off`, a tag or a glob in a repository, a malformed repository name, http GitHub or Rekor URLs).
2. Builds the API, web and backup images.
3. `infra/helm/ci/kind-smoke.sh`: creates a kind cluster (kind `v0.33.0`, node image pinned by digest), loads the images and digest-pinned PostgreSQL (pgvector), NATS and Temporal dev server images, and installs Sigstore policy-controller from its digest-pinned chart. It checks that the committed ClusterImagePolicy schema equals the installed CRD's, then installs `anum-admission` with `ci/kind-values.yaml`. The kind images are unsigned local builds, so the ANUM image policies are off there, and one probe policy requires the staging identity (`deploy-staging.yml` on `main` of this repository) for the digest-pinned pgvector image, which this repository never signed. In a namespace labelled `policy.sigstore.dev/include=true`, a pod with that image must be refused by the probe policy, and a pod with an image no policy matches must be refused too. The release namespace is not labelled, so the rest of the job also proves that unlabelled namespaces are unaffected. The script then runs `bootstrap-database.sql`, creates the Secrets with generated passwords and a generated `ANUM_SECRETS_KEY`, installs the chart with `ci/kind-values.yaml` (`ANUM_ENVIRONMENT=staging`, OIDC, PostgreSQL, NATS, Temporal), and then checks: the migration Job succeeded and tables are owned by `anum_migrator`; pods are non-root, without a token and cannot write their root filesystem; the worker polls Temporal; the API created the `ANUM_EVENTS` stream; `/health`, `/healthz`, CSP headers and a 401 without a token through port-forward; `helm test`; one run each of the retention and backup CronJobs; a deleted worker pod logs a clean shutdown well inside its grace period; `helm upgrade` (migration hook again) and `helm rollback` to revision 1, then `helm test` again.

Tools are pinned: Helm `v4.3.0`, kind `v0.33.0` and kubeconform `v0.8.0` are built with `go install` (the Go module proxy and checksum database verify them), and kubectl `v1.37.0` is downloaded from `dl.k8s.io` and checked against its SHA-256. The policy-controller chart and image are pinned by digest ([Admission policy](#admission-policy)); the kind job pulls them from `ghcr.io`. To run locally, build the three images as the script header shows, put the tools on `PATH` and run `bash infra/helm/ci/lint.sh && bash infra/helm/ci/kind-smoke.sh` (`KIND_KEEP=1` keeps the cluster; `DEP_MIRROR=mirror.gcr.io` pulls the dependency images through Google's Docker Hub mirror; hosts with cgroup v1 need a `KIND_CONFIG` that sets the kubelet's `failCgroupV1: false`).

## Not Done Yet

- The cloud itself: cluster, managed PostgreSQL, NATS, Temporal, Valkey, object storage, DNS and certificates (OpenTofu, [Infrastructure](infrastructure.md#opentofu)).
- A real staging run of the workflows: they stay skipped until the owner sets the variables and secrets above.
- Installing the [admission policy](#admission-policy) in a real cluster, and proof that a signed image is admitted: kind can only show refusals, because no image of this repository has been signed yet.
- Shipping backups off the volume to encrypted, versioned object storage in a second region.
