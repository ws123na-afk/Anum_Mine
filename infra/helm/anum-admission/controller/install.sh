#!/usr/bin/env bash
# Install (or upgrade) the Sigstore policy-controller that enforces the anum-admission
# ClusterImagePolicies (docs/deployment.md#admission-policy). Run once per cluster by a
# cluster admin, before installing infra/helm/anum-admission; also used by
# infra/helm/ci/kind-smoke.sh. Needs helm (v4.3.0, as CI) and cluster-admin access.
#
#   bash infra/helm/anum-admission/controller/install.sh [extra helm args...]
#
# The chart is pulled from the OCI registry by digest, so a re-pushed tag cannot change
# what is installed; the controller image is pinned by digest in
# policy-controller-values.yaml. Upgrade both together: take the new chart version's
# manifest digest (`crane digest ghcr.io/sigstore/helm-charts/policy-controller:<v>`) and
# the image digest from its values.yaml, regenerate the kubeconform schema
# (infra/helm/ci/crd-schema.py) and let the kind job prove the policies still work.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_REF="oci://ghcr.io/sigstore/helm-charts/policy-controller"
# Chart 0.10.8 = policy-controller v0.13.1.
CHART_VERSION="0.10.8"
CHART_DIGEST="sha256:9ff3ca6ae4a0155de4dd667f8b760e56e1bd5c8233a0321ad870a654ce022f2f"
NAMESPACE="${POLICY_CONTROLLER_NAMESPACE:-cosign-system}"
RELEASE="${POLICY_CONTROLLER_RELEASE:-policy-controller}"

printf '[policy-controller] installing %s:%s@%s into %s\n' "$CHART_REF" "$CHART_VERSION" "$CHART_DIGEST" "$NAMESPACE"
helm upgrade --install "$RELEASE" "$CHART_REF:$CHART_VERSION@$CHART_DIGEST" \
  --namespace "$NAMESPACE" --create-namespace \
  -f "$HERE/policy-controller-values.yaml" \
  --wait --timeout 10m "$@"
