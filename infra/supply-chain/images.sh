#!/usr/bin/env bash
# Container image supply chain for the deploy workflows (docs/deployment.md#image-supply-chain):
# scan with Trivy, generate a CycloneDX SBOM, sign keyless with cosign (GitHub OIDC) and
# verify the signature and the SBOM attestation before `helm upgrade`.
#
# Usage:
#   images.sh install                         go install pinned trivy and cosign (needs Go)
#   images.sh scan <image>                    fail on fixable HIGH/CRITICAL vulnerabilities
#   images.sh sbom <image> <file>             write a CycloneDX SBOM of <image>
#   images.sh digest <pushed-image:tag>       print repository@sha256:... of a pushed local image
#   images.sh resolve <image:tag>             print repository@sha256:... from the registry
#   images.sh sign <image@digest> <sbom> <commit>
#                                             sign keyless and attest the SBOM (needs id-token: write)
#   images.sh verify <image@digest> <workflow file> <commit>
#                                             verify signature + SBOM attestation from <workflow file>
#                                             on main of this repository, annotated with <commit>
#
# The tools are built with `go install` at pinned versions, so the Go module proxy and
# checksum database (sum.golang.org) verify their source, the same way CI installs Helm,
# kind, kubeconform and OSV-Scanner. No third-party GitHub Action is used.
set -euo pipefail

TRIVY_VERSION="${TRIVY_VERSION:-v0.75.0}"
COSIGN_VERSION="${COSIGN_VERSION:-v2.6.5}"
OIDC_ISSUER="https://token.actions.githubusercontent.com"

log() { printf '[supply-chain] %s\n' "$*"; }
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }
need() { [ "$#" -ge "$1" ] || fail "usage: see the header of $0"; }
digest_ref() { [[ "$1" =~ ^[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]] || fail "not an image@sha256 digest reference: $1"; }

cmd="${1:-}"
shift || true
case "$cmd" in
  install)
    go install "github.com/aquasecurity/trivy/cmd/trivy@${TRIVY_VERSION}"
    go install "github.com/sigstore/cosign/v2/cmd/cosign@${COSIGN_VERSION}"
    bin="$(go env GOPATH)/bin"
    if [ -n "${GITHUB_PATH:-}" ]; then echo "$bin" >> "$GITHUB_PATH"; fi
    "$bin/trivy" --version
    "$bin/cosign" version
    ;;
  scan)
    need 1 "$@"
    log "trivy: fixable HIGH/CRITICAL vulnerabilities and secrets in $1"
    # Exceptions, if ever needed, go in .trivyignore with the CVE id, a reason and an expiry.
    trivy image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed \
      --exit-code 1 --no-progress --format table "$1"
    ;;
  sbom)
    need 2 "$@"
    log "trivy: CycloneDX SBOM of $1 -> $2"
    trivy image --format cyclonedx --no-progress --output "$2" "$1"
    components="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("components", [])))' "$2")"
    log "SBOM lists $components components"
    ;;
  digest)
    need 1 "$@"
    repository="${1%:*}"
    ref="$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" | grep -F "${repository}@sha256:" | head -n 1)"
    digest_ref "$ref"
    echo "$ref"
    ;;
  resolve)
    need 1 "$@"
    digest="$(docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}')"
    ref="${1%:*}@${digest}"
    digest_ref "$ref"
    echo "$ref"
    ;;
  sign)
    need 3 "$@"
    digest_ref "$1"
    [ -s "$2" ] || fail "SBOM file $2 is missing or empty"
    log "cosign: keyless signature and SBOM attestation for $1"
    cosign sign --yes -a "commit=$3" "$1"
    cosign attest --yes --type cyclonedx --predicate "$2" "$1"
    ;;
  verify)
    need 3 "$@"
    digest_ref "$1"
    : "${GITHUB_SERVER_URL:?}" "${GITHUB_REPOSITORY:?}"
    identity="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/.github/workflows/$2@refs/heads/main"
    log "cosign: verifying $1 was signed by $identity for commit $3"
    cosign verify \
      --certificate-identity "$identity" --certificate-oidc-issuer "$OIDC_ISSUER" \
      --certificate-github-workflow-repository "$GITHUB_REPOSITORY" \
      -a "commit=$3" "$1" > /dev/null
    cosign verify-attestation --type cyclonedx \
      --certificate-identity "$identity" --certificate-oidc-issuer "$OIDC_ISSUER" \
      --certificate-github-workflow-repository "$GITHUB_REPOSITORY" "$1" > /dev/null
    log "verified: $1"
    ;;
  *)
    fail "unknown command '${cmd}' (install, scan, sbom, digest, resolve, sign, verify)"
    ;;
esac
