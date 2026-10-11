#!/usr/bin/env bash
# Smoke test of a running ANUM deployment from infra/oracle (docs/deploy-oracle.md#check-the-deployment).
#
# On the VM (no sudo; as the user who ran install.sh, who can read infra/oracle/.env):
#   bash infra/oracle/smoke.sh
# From anywhere with bash and curl (Linux, macOS, WSL or Git Bash on Windows):
#   bash smoke.sh anum-app-yourname.duckdns.org anum-api-yourname.duckdns.org anum-auth-yourname.duckdns.org
#
# Options:
#   --public                 on the VM: go through public DNS instead of connecting to
#                            Caddy on 127.0.0.1 (the default there, which works whether
#                            or not Oracle routes the VM's own public IP back to it)
#   --wait SECONDS           keep retrying the first HTTPS request this long (a fresh
#                            install waits for Let's Encrypt; install.sh uses 300)
#   --admin-allowed-ips LIST the ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS in effect, when run
#                            away from the VM (read from .env on the VM)
#
# It checks, and prints one line per check and a summary: the three host names (set,
# valid, all different) and their DNS; a trusted certificate on each and how long it
# has left; http -> https redirects; the web app and its security headers (CSP
# connect-src naming the API and Keycloak); the API's /health, its headers, 401 without
# a token and no /docs; Keycloak's OIDC discovery for realm anum with the right issuer;
# the address Caddy sees for this caller (the real client IP, not a Docker address);
# Keycloak's admin console and master realm refused (403) unless this caller is on the
# allow-list; and Keycloak's management endpoints not proxied.
# Exit status: 0 when nothing failed (warnings allowed), 1 when a check failed, 2 on
# bad usage. It never prints a secret: it reads only host names and the allow-list.
set -euo pipefail

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
PUBLIC=false
WAIT=0
ALLOWED_IPS=""
ALLOWED_KNOWN=false
HOSTS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --public) PUBLIC=true ;;
    --wait) [ "$#" -ge 2 ] || usage; WAIT="$2"; shift ;;
    --admin-allowed-ips) [ "$#" -ge 2 ] || usage; ALLOWED_IPS="$2"; ALLOWED_KNOWN=true; shift ;;
    -h | --help) usage ;;
    -*) echo "unknown option $1" >&2; usage ;;
    *) HOSTS+=("$1") ;;
  esac
  shift
done
[[ "$WAIT" =~ ^[0-9]+$ ]] || { echo "--wait takes a number of seconds" >&2; exit 2; }

env_value() {
  # One value from .env without sourcing it.
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"
}

if [ "${#HOSTS[@]}" -eq 3 ]; then
  WEB_HOST="${HOSTS[0]}" API_HOST="${HOSTS[1]}" AUTH_HOST="${HOSTS[2]}"
  LOCAL=false
  if [ "$ALLOWED_KNOWN" = false ] && [ -n "${ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS+set}" ]; then
    ALLOWED_IPS="$ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS"
    ALLOWED_KNOWN=true
  fi
elif [ "${#HOSTS[@]}" -eq 0 ]; then
  if [ ! -r "$ENV_FILE" ]; then
    echo "ERROR: cannot read $ENV_FILE. On the VM, run this as the user who ran install.sh;" >&2
    echo "elsewhere, pass the three host names: smoke.sh <web host> <api host> <auth host>" >&2
    exit 2
  fi
  WEB_HOST="$(env_value ANUM_WEB_HOST)" API_HOST="$(env_value ANUM_API_HOST)" AUTH_HOST="$(env_value ANUM_AUTH_HOST)"
  if [ "$ALLOWED_KNOWN" = false ]; then
    ALLOWED_IPS="$(env_value ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS)"
    ALLOWED_KNOWN=true
  fi
  if [ "$PUBLIC" = true ]; then LOCAL=false; else LOCAL=true; fi
else
  echo "pass all three host names (web, api, auth) or none" >&2
  usage
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

RESULTS=()
FAILED=0
WARNED=0
record() {
  # record PASS|FAIL|WARN|SKIP "name" "detail"
  local status="$1" name="$2" detail="${3:-}"
  case "$status" in
    FAIL) FAILED=$((FAILED + 1)) ;;
    WARN) WARNED=$((WARNED + 1)) ;;
  esac
  RESULTS+=("$(printf '%-4s  %-34s %s' "$status" "$name" "$detail")")
  printf '%-4s  %-34s %s\n' "$status" "$name" "$detail"
}

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import ipaddress' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

CURL=(curl -sS --max-time 20 --proto '=http,https')
if [ "$LOCAL" = true ]; then
  # Straight to Caddy's published ports on this VM, with the real names for SNI and
  # certificate checks; a proxy in the environment must not take the request.
  CURL+=(--noproxy '*')
  for host in "$WEB_HOST" "$API_HOST" "$AUTH_HOST"; do
    CURL+=(--resolve "$host:443:127.0.0.1" --resolve "$host:80:127.0.0.1")
  done
fi

# fetch URL [curl options...]: sets CODE (000 on a connection or TLS error), writes the
# headers to $TMP/headers and the body to $TMP/body, and the error to $TMP/error.
fetch() {
  local url="$1"
  shift
  : >"$TMP/headers"
  : >"$TMP/body"
  CODE="$("${CURL[@]}" "$@" -o "$TMP/body" -D "$TMP/headers" -w '%{http_code}' "$url" 2>"$TMP/error")" || CODE="000"
  [ -n "$CODE" ] || CODE="000"
}
header() {
  # The value of one response header (last occurrence), without the CR.
  grep -i "^$1:" "$TMP/headers" | tail -n 1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//' || true
}
curl_error() { tr -d '\r' <"$TMP/error" | tail -n 1; }
check_headers() {
  # check_headers NAME HEADER... : every header present in the last response.
  local name="$1" missing=() wanted
  shift
  for wanted in "$@"; do
    [ -n "$(header "$wanted")" ] || missing+=("$wanted")
  done
  if [ "${#missing[@]}" -eq 0 ]; then
    record PASS "$name" "$*"
  else
    record FAIL "$name" "missing: ${missing[*]}"
  fi
}

echo "ANUM smoke test: web $WEB_HOST, api $API_HOST, auth $AUTH_HOST"
if [ "$LOCAL" = true ]; then
  echo "(connecting to Caddy on 127.0.0.1 with those names; --public goes through DNS)"
fi
echo

# --- Host names ------------------------------------------------------------------------
HOSTNAME_RE='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$'
hosts_ok=true
for pair in "web:$WEB_HOST" "api:$API_HOST" "auth:$AUTH_HOST"; do
  role="${pair%%:*}" host="${pair#*:}"
  if [ -z "$host" ] || ! [[ "$host" =~ $HOSTNAME_RE ]]; then
    record FAIL "host name ($role)" "'$host' is not a lowercase host name (no https://, no slash)"
    hosts_ok=false
  fi
done
if [ "$WEB_HOST" = "$API_HOST" ] || [ "$WEB_HOST" = "$AUTH_HOST" ] || [ "$API_HOST" = "$AUTH_HOST" ]; then
  record FAIL "host names distinct" "web, api and auth need three different names (anum-app-*, anum-api-*, anum-auth-*)"
  hosts_ok=false
fi
if [ "$hosts_ok" = false ]; then
  echo
  echo "Smoke test stopped: fix the host names first (infra/oracle/.env, then install.sh or update.sh --no-pull)."
  exit 1
fi
record PASS "host names" "three valid, different names"

# --- DNS -------------------------------------------------------------------------------
resolve_ipv4() {
  if command -v getent >/dev/null 2>&1; then
    getent ahostsv4 "$1" 2>/dev/null | awk '{ print $1; exit }'
  elif command -v nslookup >/dev/null 2>&1; then
    nslookup "$1" 2>/dev/null | awk '/^Address/ && $2 !~ /#/ { ip = $2 } END { print ip }'
  fi
}
VM_IP=""
if [ "$LOCAL" = true ]; then
  VM_IP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
fi
for host in "$WEB_HOST" "$API_HOST" "$AUTH_HOST"; do
  address="$(resolve_ipv4 "$host" || true)"
  if [ -z "$address" ]; then
    if command -v getent >/dev/null 2>&1 || command -v nslookup >/dev/null 2>&1; then
      record FAIL "DNS $host" "no A record: add one (or the DuckDNS name) pointing at the VM's public IP"
    else
      record SKIP "DNS $host" "no getent or nslookup here"
    fi
  elif [ -n "$VM_IP" ] && [ "$address" != "$VM_IP" ]; then
    record FAIL "DNS $host" "points at $address, but this VM's public IP is $VM_IP: fix the A record or DUCKDNS_* in .env"
  else
    record PASS "DNS $host" "$address${VM_IP:+ (this VM)}"
  fi
done

# --- TLS -------------------------------------------------------------------------------
if [ "$WAIT" -gt 0 ]; then
  deadline=$((SECONDS + WAIT))
  fetch "https://$WEB_HOST/healthz"
  while [ "$CODE" = "000" ] && [ "$SECONDS" -lt "$deadline" ]; do
    echo "      waiting for https://$WEB_HOST (certificate not ready yet: $(curl_error))"
    sleep 10
    fetch "https://$WEB_HOST/healthz"
  done
fi
to_epoch() { date -d "$1" +%s 2>/dev/null || date -j -f '%b %e %T %Y %Z' "$1" +%s 2>/dev/null; }
cert_window() {
  # "<seconds left> <lifetime in seconds>" for the certificate served for $1; empty
  # without openssl or when it cannot be read.
  command -v openssl >/dev/null 2>&1 || return 0
  local target="$1" dates start end start_s end_s
  [ "$LOCAL" = true ] && target="127.0.0.1"
  dates="$(openssl s_client -connect "$target:443" -servername "$1" </dev/null 2>/dev/null \
    | openssl x509 -noout -startdate -enddate 2>/dev/null)" || return 0
  start="$(printf '%s\n' "$dates" | sed -n 's/^notBefore=//p')"
  end="$(printf '%s\n' "$dates" | sed -n 's/^notAfter=//p')"
  [ -n "$start" ] && [ -n "$end" ] || return 0
  start_s="$(to_epoch "$start")" && end_s="$(to_epoch "$end")" || return 0
  echo "$((end_s - $(date +%s))) $((end_s - start_s))"
}
TLS_OK=()
for host in "$WEB_HOST" "$API_HOST" "$AUTH_HOST"; do
  fetch "https://$host/" --head
  if [ "$CODE" = "000" ]; then
    record FAIL "TLS $host" "$(curl_error) (certificate not issued? docker compose logs caddy; ports 80/443 open?)"
    continue
  fi
  TLS_OK+=("$host")
  window="$(cert_window "$host" || true)"
  if [ -z "$window" ]; then
    record PASS "TLS $host" "trusted certificate"
    continue
  fi
  left="${window% *}" lifetime="${window#* }"
  # Caddy renews when a third of the lifetime is left (30 days of Let's Encrypt's 90).
  # Overdue by a day (or a tenth of a short lifetime): warn. Nearly expired: fail.
  slack=$((lifetime / 10 < 86400 ? lifetime / 10 : 86400))
  critical=$((lifetime / 10 < 259200 ? lifetime / 10 : 259200))
  if [ "$left" -lt "$critical" ]; then
    record FAIL "TLS $host" "certificate expires in $((left / 3600)) hours: renewal is failing (docker compose logs caddy)"
  elif [ "$left" -lt $((lifetime / 3 - slack)) ]; then
    record WARN "TLS $host" "certificate expires in $((left / 86400)) days and should have been renewed (docker compose logs caddy)"
  else
    record PASS "TLS $host" "trusted certificate, $((left / 86400)) days left"
  fi
done
tls_ok() {
  local host
  for host in "${TLS_OK[@]}"; do [ "$host" = "$1" ] && return 0; done
  return 1
}

# --- HTTP redirects ----------------------------------------------------------------------
for host in "$WEB_HOST" "$API_HOST" "$AUTH_HOST"; do
  fetch "http://$host/"
  location="$(header Location)"
  if [[ "$CODE" =~ ^30[178]$ ]] && [[ "$location" == "https://$host"* ]]; then
    record PASS "http -> https $host" "$CODE"
  else
    record FAIL "http -> https $host" "got $CODE${location:+ to $location} (port 80 open in the security list and iptables?)"
  fi
done

# --- Web -------------------------------------------------------------------------------
if tls_ok "$WEB_HOST"; then
  fetch "https://$WEB_HOST/"
  if [ "$CODE" = "200" ] && grep -qi '<html' "$TMP/body"; then
    record PASS "web app" "200, HTML"
  else
    record FAIL "web app" "https://$WEB_HOST/ answered $CODE (docker compose logs web)"
  fi
  check_headers "web security headers" Strict-Transport-Security Content-Security-Policy X-Content-Type-Options X-Frame-Options
  csp="$(header Content-Security-Policy)"
  if [[ "$csp" == *"https://$API_HOST"* ]] && [[ "$csp" == *"https://$AUTH_HOST"* ]]; then
    record PASS "web CSP connect-src" "names the API and Keycloak"
  else
    record FAIL "web CSP connect-src" "does not name https://$API_HOST and https://$AUTH_HOST: rebuild the web image (update.sh --no-pull)"
  fi
  fetch "https://$WEB_HOST/healthz"
  if [ "$CODE" = "200" ]; then record PASS "web /healthz" "200"; else record FAIL "web /healthz" "$CODE"; fi
else
  record SKIP "web app" "no TLS connection"
fi

# --- API -------------------------------------------------------------------------------
if tls_ok "$API_HOST"; then
  fetch "https://$API_HOST/health"
  if [ "$CODE" = "200" ] && grep -q '"status":"ok"' "$TMP/body"; then
    environment="$(sed -n 's/.*"environment":"\([a-z]*\)".*/\1/p' "$TMP/body")"
    record PASS "API /health" "200, environment ${environment:-?}"
  else
    record FAIL "API /health" "answered $CODE (docker compose ps api; docker compose logs api)"
  fi
  check_headers "API security headers" Strict-Transport-Security X-Content-Type-Options
  fetch "https://$API_HOST/api/v1/tasks"
  if [ "$CODE" = "401" ]; then
    record PASS "API refuses without a token" "401"
  else
    record FAIL "API refuses without a token" "GET /api/v1/tasks answered $CODE, expected 401"
  fi
  fetch "https://$API_HOST/docs"
  if [ "$CODE" = "404" ]; then
    record PASS "API docs not served" "404"
  else
    record WARN "API docs not served" "/docs answered $CODE, expected 404 outside local"
  fi
else
  record SKIP "API" "no TLS connection"
fi

# --- Keycloak ----------------------------------------------------------------------------
if tls_ok "$AUTH_HOST"; then
  fetch "https://$AUTH_HOST/realms/anum/.well-known/openid-configuration"
  issuer="$(grep -o '"issuer":"[^"]*"' "$TMP/body" | head -n 1 | cut -d'"' -f4 || true)"
  if [ "$CODE" = "200" ] && [ "$issuer" = "https://$AUTH_HOST/realms/anum" ]; then
    record PASS "OIDC discovery (realm anum)" "issuer $issuer"
  elif [ "$CODE" = "200" ]; then
    record FAIL "OIDC discovery (realm anum)" "issuer is '$issuer', expected https://$AUTH_HOST/realms/anum (KC_HOSTNAME)"
  else
    record FAIL "OIDC discovery (realm anum)" "answered $CODE (Keycloak starting? docker compose logs keycloak)"
  fi
  check_headers "Keycloak security headers" Strict-Transport-Security X-Content-Type-Options

  fetch "https://$AUTH_HOST/anum/client-ip"
  CLIENT_IP="$(tr -d '[:space:]' <"$TMP/body")"
  if [ "$CODE" != "200" ] || [ -z "$CLIENT_IP" ]; then
    record FAIL "client address seen by Caddy" "/anum/client-ip answered $CODE (is the Caddyfile current? docker compose up -d caddy)"
    CLIENT_IP=""
  elif [[ "$CLIENT_IP" == 172.30.* ]]; then
    if [ "$LOCAL" = true ]; then
      record PASS "client address seen by Caddy" "$CLIENT_IP (Docker's gateway: expected from the VM itself; run from your PC to check the real IP)"
    else
      record FAIL "client address seen by Caddy" "$CLIENT_IP is a Docker address, not yours: the admin allow-list cannot work (docs/deploy-oracle.md#keycloak)"
    fi
  else
    record PASS "client address seen by Caddy" "$CLIENT_IP"
  fi

  # Whether this caller is on the allow-list, as Caddy decides it: yes, no or unknown.
  expected="unknown"
  if [ -n "$CLIENT_IP" ] && [[ "$CLIENT_IP" == 172.30.* ]]; then
    expected="no"
  elif [ -n "$CLIENT_IP" ] && [ "$ALLOWED_KNOWN" = true ]; then
    if [ -z "$(printf '%s' "$ALLOWED_IPS" | tr -d ' ,')" ]; then
      expected="no"
    elif [ -n "$PYTHON" ]; then
      expected="$("$PYTHON" -c '
import ipaddress, re, sys
address = ipaddress.ip_address(sys.argv[1])
networks = [ipaddress.ip_network(entry, strict=False) for entry in re.split(r"[,\s]+", sys.argv[2]) if entry]
print("yes" if any(address in network for network in networks) else "no")
' "$CLIENT_IP" "$ALLOWED_IPS" 2>/dev/null || echo unknown)"
    fi
  fi
  for path in /admin/ /realms/master/.well-known/openid-configuration; do
    fetch "https://$AUTH_HOST$path"
    who="${CLIENT_IP:-this caller}"
    case "$expected:$CODE" in
      no:403 | unknown:403) record PASS "Keycloak $path blocked" "403 for $who (not on the allow-list)" ;;
      yes:403) record FAIL "Keycloak $path open to allow-list" "403 for $who although it is on the allow-list (docker compose up -d caddy after editing .env)" ;;
      yes:000) record FAIL "Keycloak $path open to allow-list" "$(curl_error)" ;;
      yes:*) record PASS "Keycloak $path open to allow-list" "$CODE for $who (on the allow-list)" ;;
      unknown:*) record FAIL "Keycloak $path blocked" "answered $CODE to $who; if that address is on your allow-list, pass --admin-allowed-ips" ;;
      *) record FAIL "Keycloak $path blocked" "answered $CODE to $who, which is not on the allow-list: check the Caddyfile and docker compose up -d caddy" ;;
    esac
  done
  fetch "https://$AUTH_HOST/metrics"
  if [ "$CODE" = "404" ]; then
    record PASS "Keycloak management not proxied" "/metrics 404"
  else
    record FAIL "Keycloak management not proxied" "/metrics answered $CODE"
  fi
else
  record SKIP "Keycloak" "no TLS connection"
fi

# --- Summary ---------------------------------------------------------------------------
echo
echo "Summary: ${#RESULTS[@]} checks, $FAILED failed, $WARNED warnings"
if [ "$FAILED" -gt 0 ]; then
  echo "Failed:"
  printf '%s\n' "${RESULTS[@]}" | grep '^FAIL' | sed 's/^/  /'
  echo "Fix these, then run the smoke test again (docs/deploy-oracle.md#check-the-deployment)."
  exit 1
fi
echo "All checks passed."
