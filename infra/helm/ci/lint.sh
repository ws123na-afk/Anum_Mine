#!/usr/bin/env bash
# Static checks for the ANUM Helm chart (docs/deployment.md#checks):
#   1. helm lint --strict with each values set (staging, production, CI kind values);
#   2. the rendered manifests validate against the Kubernetes OpenAPI schemas
#      (kubeconform -strict, pinned Kubernetes version);
#   3. the chart refuses configurations the API would refuse at startup.
# Needs helm and kubeconform on PATH. Used by the "Helm deploy (kind)" CI job.
#
# Environment:
#   KUBERNETES_VERSION  schema version for kubeconform (default 1.37.0)
#   KUBECONFORM_SCHEMA  schema location template (default: yannh/kubernetes-json-schema
#                       pinned to a commit, so the schemas cannot change under CI)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHART="$ROOT/infra/helm/anum"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.37.0}"
# Assigned in two steps: a `}` inside ${VAR:-default} would end the expansion early.
DEFAULT_SCHEMA='https://raw.githubusercontent.com/yannh/kubernetes-json-schema/8df8a883b68a24a104b4a9e43c1288090ae60b3b/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json'
KUBECONFORM_SCHEMA="${KUBECONFORM_SCHEMA:-$DEFAULT_SCHEMA}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

log() { printf '[helm-lint] %s\n' "$*"; }
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }

for values in values-staging.yaml values-production.yaml ci/kind-values.yaml; do
  name="$(basename "$values" .yaml)"
  log "helm lint --strict -f $values"
  helm lint --strict "$CHART" -f "$CHART/$values"
  log "helm template + kubeconform (Kubernetes $KUBERNETES_VERSION) -f $values"
  helm template anum "$CHART" --namespace anum -f "$CHART/$values" > "$OUT/$name.yaml"
  kubeconform -strict -summary -kubernetes-version "$KUBERNETES_VERSION" \
    -schema-location "$KUBECONFORM_SCHEMA" "$OUT/$name.yaml"
done

# Every pod spec the chart renders must run as non-root without a service account token.
for name in values-staging values-production kind-values; do
  pods="$(grep -c 'automountServiceAccountToken: false' "$OUT/$name.yaml" || true)"
  [ "$pods" -ge 6 ] || fail "$name: expected automountServiceAccountToken: false on every pod and ServiceAccount, found $pods"
  if grep -q 'readOnlyRootFilesystem: false' "$OUT/$name.yaml"; then
    fail "$name: a container has a writable root filesystem"
  fi
  if grep -qE 'kind: Secret$' "$OUT/$name.yaml"; then
    fail "$name: the chart must reference Secrets by name, never render one"
  fi
done

# The chart refuses what the API refuses (anum_api/hardening.py, identity.py).
refuses() {
  local expected="$1"; shift
  local output
  if output="$(helm template anum "$CHART" -f "$CHART/values-staging.yaml" "$@" 2>&1)"; then
    fail "chart rendered with $* (expected a refusal mentioning: $expected)"
  fi
  grep -qF "$expected" <<<"$output" || { echo "$output" >&2; fail "refusal for $* did not mention: $expected"; }
  log "refused as expected: $*"
}
refuses "development environment" --set config.ANUM_ENVIRONMENT=local
refuses "development environment" --set config.ANUM_ENVIRONMENT=test
refuses "ANUM_AUTH_MODE must be oidc" --set config.ANUM_AUTH_MODE=headers
refuses "must be an https origin" --set-string 'config.ANUM_CORS_ORIGINS=["http://app.anum.example"]'
refuses "must be an https origin" --set-string 'config.ANUM_CORS_ORIGINS=["https://localhost:5173"]'
refuses "must be an https origin" --set-string 'config.ANUM_CORS_ORIGINS=["*"]'
refuses "ANUM_REPOSITORY_BACKEND must be postgresql" --set config.ANUM_REPOSITORY_BACKEND=memory
refuses "mock is a release blocker" --set config.ANUM_ENVIRONMENT=production --set config.ANUM_MODEL_PROVIDER=mock
refuses "files on one pod's disk" --set config.ANUM_OBJECT_STORAGE_BACKEND=local
refuses "ANUM_KEYCLOAK_ISSUER" --set config.ANUM_KEYCLOAK_ISSUER=http://id.anum.example/realms/anum
refuses "worker.enabled must be true" --set worker.enabled=false
refuses "backup.persistentVolumeClaim" --set backup.enabled=true
log "all chart checks passed"
