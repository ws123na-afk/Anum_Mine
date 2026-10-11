#!/usr/bin/env bash
# Point the DuckDNS names in infra/oracle/.env (DUCKDNS_DOMAINS) at this VM's public IP.
# anum-duckdns.timer runs it every 5 minutes when DUCKDNS_TOKEN is set
# (docs/deploy-oracle.md#dns). DuckDNS detects the caller's address when `ip` is empty.
# curl reads the request URL (which carries the token) from stdin, never from its
# command line.
set -euo pipefail

ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.env"
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"; }

token="$(env_value DUCKDNS_TOKEN)"
domains="$(env_value DUCKDNS_DOMAINS)"
if [ -z "$token" ] || [ -z "$domains" ]; then
  echo "DUCKDNS_TOKEN or DUCKDNS_DOMAINS is empty; nothing to do"
  exit 0
fi
[[ "$domains" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || { echo "DUCKDNS_DOMAINS: names without .duckdns.org, comma-separated" >&2; exit 2; }
[[ "$token" =~ ^[A-Za-z0-9-]+$ ]] || { echo "DUCKDNS_TOKEN has an unexpected format" >&2; exit 2; }

answer="$(printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip="\n' "$domains" "$token" \
  | curl -fsS --max-time 20 --config -)"
if [ "$answer" != "OK" ]; then
  echo "DuckDNS refused the update (answer: $answer)" >&2
  exit 1
fi
echo "DuckDNS: $domains updated"
