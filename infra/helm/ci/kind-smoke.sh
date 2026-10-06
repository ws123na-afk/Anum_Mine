#!/usr/bin/env bash
# Deploy the ANUM chart into a throwaway kind cluster and smoke-test it
# (docs/deployment.md#checks). Used by the "Helm deploy (kind)" CI job and runnable
# locally. Needs docker, kind, helm, kubectl, openssl and python3 on PATH, network access
# to ghcr.io (the pinned policy-controller chart and image), and the images
# built beforehand:
#
#   docker build -t anum-api:ci services/api
#   docker build -f apps/web/Dockerfile -t anum-web:ci .
#   docker build -t anum-backup:ci infra/backup
#
# What it checks, in order:
#   1. PostgreSQL (pgvector), NATS JetStream and a Temporal dev server come up in the
#      anum-deps namespace; infra/helm/bootstrap-database.sql creates the migration,
#      application and relay logins exactly as docs/deployment.md tells an operator to.
#   2. `helm upgrade --install` with staging-shaped values (OIDC, PostgreSQL, NATS,
#      Temporal, generated ANUM_SECRETS_KEY and non-default database logins) runs the
#      migration hook Job and rolls out the API, the worker and the web client under
#      default-deny NetworkPolicies.
#   3. /health and the web /healthz through port-forward; `helm test` (health, 401
#      without a token, CSP); the API created the NATS stream; the worker polls Temporal.
#   4. The voice retention and backup CronJobs run once each and succeed.
#   5. SIGTERM: a deleted worker pod logs a clean shutdown well inside its grace period.
#   6. `helm upgrade` (migration hook again, idempotent) then `helm rollback` to
#      revision 1, and the release is healthy again.
#   Before step 1, the admission policy (docs/deployment.md#admission-policy):
#      Sigstore policy-controller from its digest-pinned chart, the committed
#      ClusterImagePolicy schema equals the installed CRD's, and in a namespace labelled
#      policy.sigstore.dev/include=true an image without this repository's deploy-workflow
#      signature and an image no policy matches are both refused. The release namespace
#      is not labelled (the kind images are unsigned local builds), so steps 1 to 6 also
#      prove the webhooks leave unlabelled namespaces alone.
#
# Environment:
#   KIND_CLUSTER      cluster name (default anum-ci)
#   KIND_CONFIG       optional kind config file (local sandboxes with cgroup v1 need one)
#   KIND_NODE_IMAGE   node image (default: pinned below)
#   DEP_MIRROR        registry for dependency images (default docker.io)
#   KIND_KEEP=1       keep the cluster afterwards
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHART="$ROOT/infra/helm/anum"
CLUSTER="${KIND_CLUSTER:-anum-ci}"
# kind v0.33.0's default node image (Kubernetes 1.37.0), pinned by digest.
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5}"
DEP_MIRROR="${DEP_MIRROR:-docker.io}"
API_IMAGE=anum-api:ci
WEB_IMAGE=anum-web:ci
BACKUP_IMAGE=anum-backup:ci
NS=anum
DEPS=anum-deps
RELEASE=anum
WORK="$(mktemp -d)"
PF_PIDS=()

log() { printf '[kind-smoke] %s\n' "$*"; }
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }

diagnostics() {
  log "diagnostics"
  kubectl get all,networkpolicy,pdb,cronjob,job,pvc -A -o wide || true
  kubectl -n "$NS" describe pods || true
  for pod in $(kubectl -n "$NS" get pods -o name 2>/dev/null); do
    echo "----- logs $pod"
    kubectl -n "$NS" logs "$pod" --all-containers --tail 120 || true
  done
  kubectl -n "$DEPS" logs statefulset/postgres --tail 60 || true
  kubectl get clusterimagepolicies.policy.sigstore.dev -o yaml || true
  kubectl -n cosign-system logs deployment/policy-controller-webhook --tail 120 || true
  kubectl get events -A --sort-by=.lastTimestamp | tail -n 60 || true
}

cleanup() {
  local status=$?
  for pid in "${PF_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  if [ "$status" -ne 0 ]; then diagnostics; fi
  if [ "${KIND_KEEP:-0}" != "1" ]; then kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

# Dependency images, pinned by digest and tagged with the names deps.yaml uses.
pull_dep() {
  local repository="$1" digest="$2" tag="$3"
  docker pull --quiet "$DEP_MIRROR/$repository@$digest" >/dev/null
  docker tag "$DEP_MIRROR/$repository@$digest" "$tag"
}

for image in "$API_IMAGE" "$WEB_IMAGE" "$BACKUP_IMAGE"; do
  docker image inspect "$image" >/dev/null 2>&1 || fail "image $image is missing; build it first"
done

log "pulling dependency images"
pull_dep pgvector/pgvector sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a pgvector/pgvector:pg16
pull_dep library/nats sha256:b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927 nats:2.10-alpine
pull_dep temporalio/temporal sha256:ad4c82c97bd12b417d1ea942610dbcd511afb250c4d5ed26c694009533df447e temporalio/temporal:1.9.1

log "creating kind cluster $CLUSTER"
kind_args=(--name "$CLUSTER" --image "$KIND_NODE_IMAGE" --wait 180s)
if [ -n "${KIND_CONFIG:-}" ]; then kind_args+=(--config "$KIND_CONFIG"); fi
kind create cluster "${kind_args[@]}"
kubectl config use-context "kind-$CLUSTER" >/dev/null

# `kind load` imports every platform of an image index; with Docker's containerd image
# store only the local platform's layers exist and the import fails. Then import the
# local platform alone, which is what the node runs.
load_image() {
  local image="$1" platform node
  if kind load docker-image "$image" --name "$CLUSTER" >/dev/null 2>&1; then return 0; fi
  platform="linux/$(docker version --format '{{.Server.Arch}}')"
  log "kind load failed for $image; importing $platform only"
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker save --platform "$platform" "$image" \
      | docker exec --privileged -i "$node" ctr --namespace=k8s.io images import --snapshotter=overlayfs - >/dev/null
  done
}

log "loading images into kind"
for image in "$API_IMAGE" "$WEB_IMAGE" "$BACKUP_IMAGE" pgvector/pgvector:pg16 nats:2.10-alpine temporalio/temporal:1.9.1; do
  load_image "$image"
done

# Admission policy (docs/deployment.md#admission-policy). The controller is installed
# before anything else, so the whole release lifecycle below (install, CronJobs,
# upgrade, rollback) runs with its webhooks in the cluster. The release namespace is
# not labelled policy.sigstore.dev/include=true: the kind images are unsigned local
# builds, and enforcement is scoped by that label exactly as in staging and production.
log "installing Sigstore policy-controller (chart and image pinned by digest)"
bash "$ROOT/infra/helm/anum-admission/controller/install.sh" --set webhook.replicaCount=1

log "committed ClusterImagePolicy schema matches the installed CRD"
kubectl get crd clusterimagepolicies.policy.sigstore.dev -o json > "$WORK/cip-crd.json"
python3 "$ROOT/infra/helm/ci/crd-schema.py" "$WORK/cip-crd.json" v1beta1 > "$WORK/cip-schema.json"
diff -u "$ROOT/infra/helm/ci/schemas/policy.sigstore.dev/clusterimagepolicy_v1beta1.json" "$WORK/cip-schema.json" \
  || fail "infra/helm/ci/schemas is stale for the pinned policy-controller chart; regenerate it with infra/helm/ci/crd-schema.py"

# The probe policy requires the staging identity (deploy-staging.yml on main of this
# repository) for a public image this repository never signed: the digest-pinned
# pgvector dependency image, referenced through DEP_MIRROR like the pulls above.
if [ "$DEP_MIRROR" = "docker.io" ]; then PROBE_REPOSITORY=index.docker.io/pgvector/pgvector; else PROBE_REPOSITORY="$DEP_MIRROR/pgvector/pgvector"; fi
PROBE_IMAGE="$DEP_MIRROR/pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a"
UNMATCHED_IMAGE="$DEP_MIRROR/library/nats@sha256:b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927"
log "anum-admission chart with the kind probe policy"
helm upgrade --install anum-admission "$ROOT/infra/helm/anum-admission" \
  -f "$ROOT/infra/helm/anum-admission/ci/kind-values.yaml" \
  --set "github.repository=${GITHUB_REPOSITORY:-ws123na-afk/Anum_Mine}" \
  --set "images.probe.repository=$PROBE_REPOSITORY" --wait --timeout 2m
kubectl get clusterimagepolicies.policy.sigstore.dev

# Refused: expects a denial that names the probe policy (or, for the second image, no
# matching policy). An admitted pod fails the check at once. Retries cover the seconds
# the webhook takes to load a new policy (until then it answers "no matching policies").
expect_refused() {
  local name="$1" image="$2" pattern="$3" output
  for attempt in $(seq 1 30); do
    if output="$(kubectl -n "$ADMISSION_NS" run "$name" --image="$image" --restart=Never 2>&1)"; then
      fail "admission: $image was admitted in $ADMISSION_NS ($output)"
    fi
    if grep -qiE "$pattern" <<<"$output"; then
      log "refused as expected: $output"
      return 0
    fi
    sleep 4
  done
  fail "admission: $image was refused, but not for the expected reason ($pattern): $output"
}
ADMISSION_NS=anum-admission-test
kubectl create namespace "$ADMISSION_NS"
kubectl label namespace "$ADMISSION_NS" policy.sigstore.dev/include=true
log "admission: an image without this repository's signature is refused in a labelled namespace"
expect_refused unsigned "$PROBE_IMAGE" 'anum-probe-signature'
log "admission: an image no policy matches is refused in a labelled namespace (no-match-policy: deny)"
expect_refused unmatched "$UNMATCHED_IMAGE" 'no matching polic'
test -z "$(kubectl -n "$ADMISSION_NS" get pods -o name)" || fail "admission: a pod exists in $ADMISSION_NS"

# Throwaway credentials, generated per run (hex: no quoting issues anywhere).
ADMIN_PASSWORD="$(openssl rand -hex 24)"
MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
APP_PASSWORD="$(openssl rand -hex 24)"
RELAY_PASSWORD="$(openssl rand -hex 24)"
BACKUP_PASSWORD="$(openssl rand -hex 24)"
SECRETS_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
DB_HOST="postgres.$DEPS.svc.cluster.local:5432"

log "starting PostgreSQL, NATS and Temporal in $DEPS"
kubectl create namespace "$DEPS"
kubectl -n "$DEPS" create secret generic postgres-admin --from-literal=password="$ADMIN_PASSWORD"
kubectl apply -f "$ROOT/infra/helm/ci/deps.yaml"
kubectl -n "$DEPS" rollout status statefulset/postgres --timeout=180s
kubectl -n "$DEPS" rollout status deployment/nats --timeout=180s
kubectl -n "$DEPS" rollout status deployment/temporal --timeout=180s

log "bootstrapping database roles (infra/helm/bootstrap-database.sql)"
for attempt in $(seq 1 30); do
  if kubectl -n "$DEPS" exec postgres-0 -- psql -U anum_admin -d anum -tAc 'select 1' >/dev/null 2>&1; then break; fi
  sleep 2
done
{
  printf "\\\\set migrator_password '%s'\n\\\\set app_password '%s'\n\\\\set relay_password '%s'\n" \
    "$MIGRATOR_PASSWORD" "$APP_PASSWORD" "$RELAY_PASSWORD"
  cat "$ROOT/infra/helm/bootstrap-database.sql"
  # Backup login for the backup CronJob (docs/runbooks.md#backups).
  printf "create role anum_backup login password '%s' bypassrls;\ngrant pg_read_all_data to anum_backup;\n" "$BACKUP_PASSWORD"
} | kubectl -n "$DEPS" exec -i postgres-0 -- psql -q -U anum_admin -d anum -v ON_ERROR_STOP=1 -f - >/dev/null

log "creating the release's Secrets (referenced by name from the chart)"
kubectl create namespace "$NS"
kubectl -n "$NS" create secret generic anum-app \
  --from-literal=ANUM_SECRETS_KEY="$SECRETS_KEY" \
  --from-literal=ANUM_DATABASE_URL="postgresql+psycopg://anum_app:$APP_PASSWORD@$DB_HOST/anum" \
  --from-literal=ANUM_OUTBOX_DATABASE_URL="postgresql+psycopg://anum_relay:$RELAY_PASSWORD@$DB_HOST/anum"
kubectl -n "$NS" create secret generic anum-migrate \
  --from-literal=ANUM_DATABASE_URL="postgresql+psycopg://anum_migrator:$MIGRATOR_PASSWORD@$DB_HOST/anum"
kubectl -n "$NS" create secret generic anum-backup \
  --from-literal=ANUM_BACKUP_DATABASE_URL="postgresql://anum_backup:$BACKUP_PASSWORD@$DB_HOST/anum"
kubectl -n "$NS" apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: anum-backups
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
YAML

log "helm upgrade --install (revision 1: migration hook, then rollout)"
helm upgrade --install "$RELEASE" "$CHART" --namespace "$NS" \
  -f "$CHART/ci/kind-values.yaml" --wait --timeout 10m
kubectl -n "$NS" rollout status deployment/anum-api --timeout=300s
kubectl -n "$NS" rollout status deployment/anum-web --timeout=300s
kubectl -n "$NS" rollout status deployment/anum-worker --timeout=300s

log "migration Job"
test "$(kubectl -n "$NS" get job anum-migrate -o jsonpath='{.status.succeeded}')" = "1" \
  || fail "migration Job did not succeed"
kubectl -n "$NS" logs job/anum-migrate | tee "$WORK/migrate.log"
grep -q "Running upgrade" "$WORK/migrate.log" || fail "migration Job applied no revision"
revision="$(kubectl -n "$DEPS" exec postgres-0 -- psql -U anum_admin -d anum -tAc 'select version_num from alembic_version')"
log "alembic revision: $revision"
owner="$(kubectl -n "$DEPS" exec postgres-0 -- psql -U anum_admin -d anum -tAc "select tableowner from pg_tables where tablename = 'tasks'")"
test "$owner" = "anum_migrator" || fail "tables should be owned by anum_migrator, got '$owner'"

log "security context: non-root, no token, read-only root filesystem"
for component in api web worker; do
  pod="$(kubectl -n "$NS" get pod -l "app.kubernetes.io/component=$component" -o jsonpath='{.items[0].metadata.name}')"
  uid="$(kubectl -n "$NS" exec "$pod" -- id -u)"
  test "$uid" != "0" || fail "$component runs as root"
  if kubectl -n "$NS" exec "$pod" -- sh -c 'test -e /var/run/secrets/kubernetes.io/serviceaccount/token'; then
    fail "$component has a service account token mounted"
  fi
  if kubectl -n "$NS" exec "$pod" -- sh -c 'touch /probe-write 2>/dev/null'; then
    fail "$component can write to its root filesystem"
  fi
done

log "worker polls Temporal; API connected to NATS"
for attempt in $(seq 1 60); do
  if kubectl -n "$NS" logs deployment/anum-worker | grep -q "ANUM worker polling anum-agent-runs"; then break; fi
  sleep 2
done
kubectl -n "$NS" logs deployment/anum-worker | grep -q "ANUM worker polling anum-agent-runs" \
  || fail "worker did not start polling Temporal"
for attempt in $(seq 1 60); do
  if kubectl -n "$DEPS" exec deployment/nats -- wget -qO- 'http://127.0.0.1:8222/jsz?streams=true' | grep -q '"ANUM_EVENTS"'; then break; fi
  sleep 2
done
kubectl -n "$DEPS" exec deployment/nats -- wget -qO- 'http://127.0.0.1:8222/jsz?streams=true' | grep -q '"ANUM_EVENTS"' \
  || fail "the API did not create the ANUM_EVENTS stream (NATS unreachable under the NetworkPolicies?)"

log "port-forward smoke test"
kubectl -n "$NS" port-forward svc/anum-api 18000:8000 >"$WORK/pf-api.log" 2>&1 &
PF_PIDS+=($!)
kubectl -n "$NS" port-forward svc/anum-web 18080:8080 >"$WORK/pf-web.log" 2>&1 &
PF_PIDS+=($!)
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18000/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18000/health | tee "$WORK/health.json"; echo
grep -q '"environment":"staging"' "$WORK/health.json" || fail "API is not running with ANUM_ENVIRONMENT=staging"
curl -fsS http://127.0.0.1:18080/healthz; echo
curl -fsS -D - -o /dev/null http://127.0.0.1:18000/health | grep -qi '^content-security-policy:' || fail "API has no CSP header"
curl -fsS -D - -o /dev/null http://127.0.0.1:18080/ | grep -qi '^content-security-policy:' || fail "web has no CSP header"
status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18000/api/v1/tasks)"
test "$status" = "401" || fail "API answered $status without a token (expected 401 in oidc mode)"

log "helm test"
helm test "$RELEASE" --namespace "$NS" --logs --timeout 5m

log "voice retention CronJob, run once"
kubectl -n "$NS" create job --from=cronjob/anum-voice-retention anum-voice-retention-smoke
kubectl -n "$NS" wait --for=condition=complete job/anum-voice-retention-smoke --timeout=300s
kubectl -n "$NS" logs job/anum-voice-retention-smoke

log "backup CronJob, run once"
kubectl -n "$NS" create job --from=cronjob/anum-backup anum-backup-smoke
kubectl -n "$NS" wait --for=condition=complete job/anum-backup-smoke --timeout=300s
kubectl -n "$NS" logs job/anum-backup-smoke | tee "$WORK/backup.log"
grep -q '"manifest": "/backups/anum/anum-.*\.manifest\.json"' "$WORK/backup.log" || fail "backup wrote no manifest"
grep -q '"tables": [1-9]' "$WORK/backup.log" || fail "backup dumped no tables"

log "worker SIGTERM: clean shutdown inside the grace period"
pod="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=worker -o jsonpath='{.items[0].metadata.name}')"
kubectl -n "$NS" logs -f "$pod" >"$WORK/worker-shutdown.log" 2>&1 &
logs_pid=$!
sleep 2
started="$(date +%s)"
kubectl -n "$NS" delete pod "$pod" --wait=true --timeout=90s
elapsed=$(( $(date +%s) - started ))
wait "$logs_pid" 2>/dev/null || true
log "worker pod terminated in ${elapsed}s (grace period 60s)"
grep -q "Shutting down" "$WORK/worker-shutdown.log" || fail "worker did not log a clean shutdown"
grep -q "Beginning worker shutdown" "$WORK/worker-shutdown.log" || fail "Temporal worker did not shut down"
[ "$elapsed" -lt 55 ] || fail "worker took ${elapsed}s to stop (killed at the grace period?)"
kubectl -n "$NS" rollout status deployment/anum-worker --timeout=180s

log "helm upgrade (revision 2: migration hook again), then helm rollback to 1"
helm upgrade "$RELEASE" "$CHART" --namespace "$NS" -f "$CHART/ci/kind-values.yaml" \
  --set-string 'api.podAnnotations.anum\.dev/smoke=upgrade' --wait --timeout 10m
test "$(kubectl -n "$NS" get job anum-migrate -o jsonpath='{.status.succeeded}')" = "1" \
  || fail "migration hook did not succeed on upgrade"
helm rollback "$RELEASE" 1 --namespace "$NS" --wait --timeout 10m
helm history "$RELEASE" --namespace "$NS"
kubectl -n "$NS" rollout status deployment/anum-api --timeout=300s
test -z "$(kubectl -n "$NS" get deployment anum-api -o jsonpath='{.spec.template.metadata.annotations.anum\.dev/smoke}')" \
  || fail "rollback did not restore revision 1's pod template"
helm test "$RELEASE" --namespace "$NS" --logs --timeout 5m

log "all kind checks passed"
