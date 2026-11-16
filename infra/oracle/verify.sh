#!/usr/bin/env bash
# One read-only check that a deployment worked (docs/deploy-oracle.md#verify-a-deployment).
#
#   sudo bash infra/oracle/verify.sh              everything below, then a summary to paste back
#   sudo bash infra/oracle/verify.sh --no-fetch   do not run `git fetch` (offline, or GitHub down)
#   sudo bash infra/oracle/verify.sh --no-smoke   skip smoke.sh (the HTTPS checks through Caddy)
#
# Run as root (sudo) or a docker-group user. It changes nothing: `git fetch` only updates
# the remote-tracking branch (never a pull), `alembic current` / `alembic heads` run in a
# throwaway migrate container, and the API's route list is read inside the API container
# (never through Caddy). It checks: the deployed commit against origin/main and the
# images built from it; container states, health and restarts; the database revision
# against the newest migration (pending migrations); smoke.sh (API /health, web /healthz,
# TLS, headers, Keycloak); the heal, backup, drill and retention timers and their last
# result; which settings are set (System health ids, phone push, SMTP, model provider:
# names and set/empty only); the last backup's age, the off-site copy and the last restore
# drill; and that the personal finance routes exist in the running API.
# It never prints a secret or a setting's value. Exit status: 0 when nothing failed
# (warnings allowed), 1 when a check failed, 2 on bad usage.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$ORACLE_DIR/../.." && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
MIGRATIONS_DIR="$REPO_DIR/services/api/migrations/versions"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
SCRIPT="sudo bash infra/oracle"

FETCH=true
SMOKE=true
for argument in "$@"; do
  case "$argument" in
    --no-fetch) FETCH=false ;;
    --no-smoke) SMOKE=false ;;
    *) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
  esac
done
[ -r "$ENV_FILE" ] || { echo "ERROR: cannot read $ENV_FILE: run this with sudo on the VM" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is not installed here: run this on the VM" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is needed (install.sh installs it)" >&2; exit 2; }

env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"; }
REPO_OWNER="$(stat -c %U "$REPO_DIR")"
as_repo_owner() {
  if [ "$(id -un)" = "$REPO_OWNER" ] || [ "$(id -u)" -ne 0 ]; then "$@"; else runuser -u "$REPO_OWNER" -- "$@"; fi
}
git_() { as_repo_owner git -C "$REPO_DIR" "$@"; }

RESULTS=()
FAILED=0
WARNED=0
# record PASS|WARN|FAIL "name" "detail" ["next command"]
record() {
  local status="$1" name="$2" detail="${3:-}" next="${4:-}" line
  case "$status" in
    FAIL) FAILED=$((FAILED + 1)) ;;
    WARN) WARNED=$((WARNED + 1)) ;;
  esac
  line="$(printf '%-4s  %-28s %s' "$status" "$name" "$detail")"
  [ -z "$next" ] || [ "$status" = PASS ] || line="$line"$'\n'"      next: $next"
  RESULTS+=("$line")
  printf '%s\n' "$line"
}
section() { printf '\n-- %s\n' "$*"; }
# Seconds since an ISO 8601 time, or empty when it cannot be read.
age_of() { local start; start="$(date -d "$1" +%s 2>/dev/null)" || return 0; echo $(($(date +%s) - start)); }
hours() { echo "$(($1 / 3600))h"; }

echo "ANUM deployment check, $(date -u +%Y-%m-%dT%H:%MZ), host $(hostname)"
echo "(run each 'next:' command from $REPO_DIR)"

# --- Code ------------------------------------------------------------------------------
section "Code"
if ! head_commit="$(git_ rev-parse HEAD 2>/dev/null)"; then
  record FAIL "git checkout" "$REPO_DIR is not a git checkout readable by $REPO_OWNER" "ls -la $REPO_DIR"
else
  echo "deployed checkout: $(git_ log -1 --format='%h %cs %s' HEAD) (branch $(git_ rev-parse --abbrev-ref HEAD))"
  fetched=true
  if [ "$FETCH" = true ] && ! git_ fetch --quiet origin main 2>/dev/null; then
    fetched=false
  fi
  if ! remote="$(git_ rev-parse --verify --quiet origin/main 2>/dev/null)"; then
    record WARN "commit vs origin/main" "origin/main is unknown here" "git -C $REPO_DIR fetch origin main"
  else
    read -r ahead behind <<<"$(git_ rev-list --left-right --count HEAD...origin/main)"
    note=""
    [ "$fetched" = true ] || note=" (git fetch failed: compared with the last fetched origin/main)"
    [ "$FETCH" = true ] || note=" (--no-fetch: compared with the last fetched origin/main)"
    if [ "$head_commit" = "$remote" ]; then
      record PASS "commit vs origin/main" "${head_commit:0:12} = origin/main$note"
    elif [ "$ahead" = 0 ]; then
      record WARN "commit vs origin/main" "$behind commit(s) behind origin/main ${remote:0:12}$note" "$SCRIPT/update.sh"
    else
      record WARN "commit vs origin/main" "$ahead local commit(s) not on origin/main, $behind behind$note" "git -C $REPO_DIR status"
    fi
  fi
  # The images serving now: built after the newest commit that changed what goes into
  # them (a commit touching only scripts or docs reuses the cached image, whose creation
  # time stays older), and the ones running.
  for pair in api:anum/api web:anum/web; do
    service="${pair%%:*}" image="${pair#*:}:local"
    if [ "$service" = api ]; then
      inputs=(services/api)
    else
      inputs=(package.json pnpm-lock.yaml pnpm-workspace.yaml apps/web apps/desktop/package.json packages/contracts docs/legal)
    fi
    commit_time="$(git_ log -1 --format=%cI HEAD -- "${inputs[@]}" 2>/dev/null || true)"
    [ -n "$commit_time" ] || commit_time="$(git_ log -1 --format=%cI HEAD)"
    if ! built="$(docker image inspect --format '{{.Created}}' "$image" 2>/dev/null)"; then
      record FAIL "image $image" "not built on this VM" "$SCRIPT/update.sh --no-pull"
      continue
    fi
    container="$(dc ps -q "$service" 2>/dev/null | head -n 1 || true)"
    running_id="$( [ -z "$container" ] || docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true)"
    image_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
    built_s="$(date -d "$built" +%s 2>/dev/null || echo 0)" commit_s="$(date -d "$commit_time" +%s 2>/dev/null || echo 0)"
    if [ -n "$running_id" ] && [ "$running_id" != "$image_id" ]; then
      record WARN "image $image" "$service runs an older image than $image" "$SCRIPT/update.sh --no-pull"
    elif [ "$built_s" -lt "$commit_s" ]; then
      record WARN "image $image" "built ${built%%.*} before the last commit that changed it ($commit_time)" "$SCRIPT/update.sh --no-pull"
    else
      record PASS "image $image" "built ${built%%.*}, after the last commit that changed it"
    fi
  done
fi

# --- Containers --------------------------------------------------------------------------
section "Containers"
config_json="$(dc config --format json 2>/dev/null || true)"
ps_json="$(dc ps --all --format json 2>/dev/null || true)"
if [ -z "$config_json" ]; then
  record FAIL "compose configuration" "docker compose config failed" "docker compose --project-directory $ORACLE_DIR config --quiet"
else
  # One line per service: "<status> <service> <detail>"; one-shots (restart: "no") may have exited.
  while read -r status service detail; do
    [ -n "$status" ] || continue
    case "$status" in
      PASS) record PASS "container $service" "$detail" ;;
      WARN) record WARN "container $service" "$detail" "docker compose --project-directory $ORACLE_DIR logs --tail 100 $service" ;;
      *) record FAIL "container $service" "$detail" "docker compose --project-directory $ORACLE_DIR logs --tail 100 $service" ;;
    esac
  done < <(CONFIG="$config_json" PS="$ps_json" python3 -c '
import json, os
config = json.loads(os.environ["CONFIG"])["services"]
raw = os.environ["PS"].strip()
rows = []
if raw.startswith("["):
    rows = json.loads(raw)
else:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
by_service = {}
for row in rows:
    by_service.setdefault(row.get("Service", ""), row)
for name in sorted(config):
    one_shot = str(config[name].get("restart", "")) == "no"
    row = by_service.get(name)
    if row is None:
        print("PASS" if one_shot else "FAIL", name, "not run yet" if one_shot else "missing: not created")
        continue
    state, health = row.get("State", ""), row.get("Health", "")
    code = row.get("ExitCode", 0)
    if one_shot:
        if state == "running" or code == 0:
            print("PASS", name, f"one-shot, {state} (exit {code})")
        else:
            print("FAIL", name, f"one-shot exited with {code}")
    elif state != "running":
        print("FAIL", name, f"{state} (exit {code})")
    elif health == "unhealthy":
        print("FAIL", name, "running, unhealthy")
    elif health == "starting":
        print("WARN", name, "running, health check still starting")
    else:
        print("PASS", name, "running, " + (health or "no health check"))
')
  # Restarts: Docker's restart count (policy restarts since the container was created, for
  # containers started in the last day) and the heal timer's restarts in the last day.
  restarts=""
  for container in $(docker ps --all --quiet --filter label=com.docker.compose.project=anum 2>/dev/null); do
    read -r service count started <<<"$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.RestartCount}} {{.State.StartedAt}}' "$container" 2>/dev/null || true)"
    if [ -z "${count:-}" ] || [ "$count" = 0 ]; then continue; fi
    age="$(age_of "$started")"
    if [ -n "$age" ] && [ "$age" -lt 86400 ]; then restarts="$restarts $service($count)"; fi
  done
  healed="$(journalctl -u anum-heal --since -24h --no-pager -q -o cat 2>/dev/null | grep -c '^restarting unhealthy' || true)"
  if [ -n "$restarts" ] || [ "${healed:-0}" -gt 0 ]; then
    record WARN "restarts in the last day" "crash restarts:${restarts:- none}; heal restarts: ${healed:-0}" "journalctl -u anum-heal --since -24h --no-pager"
  else
    record PASS "restarts in the last day" "none"
  fi
fi

# --- Database schema -------------------------------------------------------------------
section "Database schema"
revisions() { dc run --rm --no-deps -T migrate python -m alembic "$1" 2>/dev/null | awk 'NF { print $1 }' | sort | tr '\n' ' '; }
# The newest revision in the checkout: the one no other migration names as down_revision.
checkout_head="$(python3 - "$MIGRATIONS_DIR" <<'PY' 2>/dev/null || true
import pathlib, re, sys
revisions, parents = set(), set()
for path in pathlib.Path(sys.argv[1]).glob("*.py"):
    text = path.read_text(encoding="utf-8")
    revision = re.search(r'^revision\s*=\s*["\']([^"\']+)', text, re.M)
    if revision:
        revisions.add(revision.group(1))
    down = re.search(r'^down_revision\s*=\s*(.+)$', text, re.M)
    if down:
        parents.update(re.findall(r'["\']([^"\']+)["\']', down.group(1)))
print(" ".join(sorted(revisions - parents)))
PY
)"
if ! current="$(revisions current)" || [ -z "${current// /}" ]; then
  record FAIL "database revision" "could not read it (alembic current)" "docker compose --project-directory $ORACLE_DIR run --rm --no-deps migrate python -m alembic current"
elif ! image_head="$(revisions heads)" || [ -z "${image_head// /}" ]; then
  record FAIL "database revision" "at ${current% }; the image lists no head (alembic heads)" "$SCRIPT/update.sh --no-pull"
else
  current="${current% }" image_head="${image_head% }"
  if [ "$current" = "$image_head" ]; then
    record PASS "database revision" "$current = newest migration in the image"
  else
    record FAIL "database revision" "at $current, the image has $image_head: migrations are pending" "$SCRIPT/update.sh --no-pull"
  fi
  if [ -n "$checkout_head" ] && [ "$checkout_head" != "$image_head" ]; then
    record WARN "checkout migrations" "the checkout's newest is $checkout_head, the built image's $image_head" "$SCRIPT/update.sh --no-pull"
  fi
fi

# --- Through Caddy (smoke.sh) ----------------------------------------------------------
section "HTTPS through Caddy (smoke.sh)"
if [ "$SMOKE" = false ]; then
  record WARN "smoke test" "skipped (--no-smoke)" "bash infra/oracle/smoke.sh"
elif smoke_output="$(as_repo_owner bash "$ORACLE_DIR/smoke.sh" 2>&1)"; then
  printf '%s\n' "$smoke_output" | grep -E '^(FAIL|WARN)|(API /health|web /healthz)' | sed 's/^/  /' || true
  record PASS "smoke test" "$(printf '%s\n' "$smoke_output" | grep '^Summary:' | sed 's/^Summary: //')"
else
  printf '%s\n' "$smoke_output" | grep -E '^(FAIL|WARN)|(API /health|web /healthz)' | sed 's/^/  /' || true
  record FAIL "smoke test" "$(printf '%s\n' "$smoke_output" | grep '^Summary:' | sed 's/^Summary: //')" "bash infra/oracle/smoke.sh"
fi

# --- Timers ----------------------------------------------------------------------------
section "Timers"
check_timer() {
  # check_timer <unit> <install command> <max hours since the last run, or 0>
  local unit="$1" install="$2" max_hours="$3" result status exited age
  if ! systemctl is-enabled --quiet "$unit.timer" 2>/dev/null; then
    record FAIL "timer $unit" "not enabled" "$install"
    return
  fi
  result="$(systemctl show -p Result --value "$unit.service" 2>/dev/null || true)"
  status="$(systemctl show -p ExecMainStatus --value "$unit.service" 2>/dev/null || true)"
  exited="$(systemctl show -p ExecMainExitTimestamp --value "$unit.service" 2>/dev/null || true)"
  if [ -z "$exited" ] || [ "$exited" = "n/a" ]; then
    record PASS "timer $unit" "enabled, not run since boot"
    return
  fi
  age="$(age_of "$exited")"
  if [ "$result" != "success" ] || { [ -n "$status" ] && [ "$status" != 0 ]; }; then
    record FAIL "timer $unit" "last run failed ($result, exit ${status:-?}) ${age:+$(hours "$age") ago}" "journalctl -u $unit.service -n 50 --no-pager"
  elif [ "$max_hours" -gt 0 ] && [ -n "$age" ] && [ "$age" -gt $((max_hours * 3600)) ]; then
    record WARN "timer $unit" "last run $(hours "$age") ago" "systemctl list-timers '$unit*'"
  else
    record PASS "timer $unit" "last run ok${age:+, $(hours "$age") ago}"
  fi
}
check_timer anum-heal "$SCRIPT/heal.sh install" 1
check_timer anum-backup "$SCRIPT/backup.sh backup" 26
check_timer anum-drill "$SCRIPT/backup.sh drill-timer" 0
check_timer anum-retention "$SCRIPT/install.sh" 26
if [ "$(env_value ANUM_AUTO_UPDATE)" = "true" ]; then
  check_timer anum-update "systemctl enable --now anum-update.timer" 26
fi

# --- Settings (names and set/empty only) -----------------------------------------------
section "Settings in the running API (names only, never values)"
SETTINGS_SCRIPT='
import os
def is_set(name):
    return bool(os.environ.get(name, "").strip())
for name in ("ANUM_SYSTEM_HEALTH_TENANT_ID", "ANUM_SYSTEM_HEALTH_WORKSPACE_ID", "ANUM_FCM_SERVICE_ACCOUNT_JSON",
             "ANUM_SMTP_HOST", "ANUM_SMTP_FROM", "ANUM_MODEL_API_KEY"):
    print(name, "set" if is_set(name) else "empty")
provider = os.environ.get("ANUM_MODEL_PROVIDER", "").strip().lower()
print("ANUM_MODEL_PROVIDER", "mock" if provider in ("", "mock") else "real")
'
if settings="$(dc exec -T api python -c "$SETTINGS_SCRIPT" 2>/dev/null)"; then
  setting() { printf '%s\n' "$settings" | awk -v name="$1" '$1 == name { print $2 }'; }
  if [ "$(setting ANUM_SYSTEM_HEALTH_TENANT_ID)" = set ] && [ "$(setting ANUM_SYSTEM_HEALTH_WORKSPACE_ID)" = set ]; then
    record PASS "System health notices" "ANUM_SYSTEM_HEALTH_TENANT_ID, ANUM_SYSTEM_HEALTH_WORKSPACE_ID set"
  else
    missing="$(for name in ANUM_SYSTEM_HEALTH_TENANT_ID ANUM_SYSTEM_HEALTH_WORKSPACE_ID; do [ "$(setting "$name")" = set ] || printf '%s ' "$name"; done)"
    record WARN "System health notices" "empty: ${missing% }" "set them in $ENV_FILE (docs/system-health.md#notices), then $SCRIPT/update.sh --no-pull"
  fi
  if [ "$(setting ANUM_FCM_SERVICE_ACCOUNT_JSON)" = set ]; then
    record PASS "phone push (FCM)" "ANUM_FCM_SERVICE_ACCOUNT_JSON set"
  else
    record WARN "phone push (FCM)" "ANUM_FCM_SERVICE_ACCOUNT_JSON empty: no push to phones" "set it in $ENV_FILE (docs/deploy-oracle.md#phone-push), then $SCRIPT/update.sh --no-pull"
  fi
  if [ "$(setting ANUM_SMTP_HOST)" = set ] && [ "$(setting ANUM_SMTP_FROM)" = set ]; then
    record PASS "notification email (SMTP)" "ANUM_SMTP_HOST, ANUM_SMTP_FROM set"
  else
    record WARN "notification email (SMTP)" "ANUM_SMTP_HOST or ANUM_SMTP_FROM empty: no email is sent" "set ANUM_SMTP_* in $ENV_FILE (.env.example explains), then $SCRIPT/update.sh --no-pull"
  fi
  if [ "$(setting ANUM_MODEL_PROVIDER)" = real ]; then
    record PASS "model provider" "real (ANUM_MODEL_API_KEY $(setting ANUM_MODEL_API_KEY))"
  else
    record WARN "model provider" "mock: the assistant gives canned answers" "set ANUM_MODEL_PROVIDER in $ENV_FILE (.env.example explains), then $SCRIPT/update.sh --no-pull"
  fi
else
  record FAIL "settings" "could not read them in the api container (not running?)" "docker compose --project-directory $ORACLE_DIR ps api"
fi

# --- Backups ---------------------------------------------------------------------------
section "Backups"
BACKUP_DIR="$(env_value ANUM_BACKUP_DIR)"
BACKUP_DIR="${BACKUP_DIR:-/var/lib/anum/backups}"
# check_status <kind> <name> <warn hours> <fail hours> <command to run it now>
check_status() {
  local file="$BACKUP_DIR/status/last-$1.json" name="$2" warn_h="$3" fail_h="$4" command="$5" ok last age
  if [ ! -f "$file" ]; then
    record WARN "$name" "never recorded ($file missing)" "$command"
    return
  fi
  read -r ok last <<<"$(python3 -c '
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print("true" if data.get("ok") else "false", data.get("last_success_at") or "-")
' "$file" 2>/dev/null || echo "unreadable -")"
  age="$( [ "$last" = "-" ] || age_of "$last")"
  if [ "$ok" = unreadable ]; then
    record FAIL "$name" "$file is not valid JSON" "$command"
  elif [ "$ok" != true ]; then
    record FAIL "$name" "last run failed; last success ${age:+$(hours "$age") ago}${age:-never}" "$command"
  elif [ -z "$age" ]; then
    record WARN "$name" "no time recorded" "$command"
  elif [ "$age" -gt $((fail_h * 3600)) ]; then
    record FAIL "$name" "last success $(hours "$age") ago" "$command"
  elif [ "$age" -gt $((warn_h * 3600)) ]; then
    record WARN "$name" "last success $(hours "$age") ago" "$command"
  else
    record PASS "$name" "last success $(hours "$age") ago"
  fi
}
check_status local "backup (on the VM)" 26 50 "$SCRIPT/backup.sh backup"
check_status offsite "backup (off-site copy)" 26 50 "$SCRIPT/backup.sh backup"
check_status drill "restore drill" 840 1500 "$SCRIPT/backup.sh drill"

# --- Personal finance routes in the running API ----------------------------------------
section "Personal finance routes (read inside the api container)"
FINANCE_PATHS=(
  /api/v1/finance/matching/run
  /api/v1/finance/matching/backfill
  /api/v1/finance/automation/matching
  /api/v1/finance/debt-overview
  /api/v1/finance/budgets/plans
  /api/v1/finance/alerts/settings
  /api/v1/finance/advisor/answer
)
# The route list straight from the API process (127.0.0.1:8000 inside the container), not
# through Caddy. ANUM_VERIFY_OPENAPI_URL exists only for the stand-in in test_oracle_compose.py.
OPENAPI_SCRIPT='
import json, os, sys, urllib.request
url = os.environ.get("ANUM_VERIFY_OPENAPI_URL") or "http://127.0.0.1:8000/openapi.json"
with urllib.request.urlopen(url, timeout=20) as response:
    paths = json.load(response)["paths"]
missing = [path for path in sys.argv[1:] if path not in paths]
print("missing", *missing) if missing else print("ok", len(paths))
'
if openapi="$(dc exec -T api python -c "$OPENAPI_SCRIPT" "${FINANCE_PATHS[@]}" 2>/dev/null)"; then
  case "$openapi" in
    ok*) record PASS "finance routes" "${#FINANCE_PATHS[@]} of ${#FINANCE_PATHS[@]} present (${openapi#ok } routes in all)" ;;
    *) record FAIL "finance routes" "${openapi}" "$SCRIPT/update.sh --no-pull" ;;
  esac
else
  record FAIL "finance routes" "could not read the API's route list in the api container" "docker compose --project-directory $ORACLE_DIR logs --tail 100 api"
fi

# --- Summary ---------------------------------------------------------------------------
echo
echo "== Summary: ${#RESULTS[@]} checks, $FAILED failed, $WARNED warnings"
printf '%s\n' "${RESULTS[@]}"
if [ "$FAILED" -gt 0 ]; then
  echo "RESULT: FAIL"
elif [ "$WARNED" -gt 0 ]; then
  echo "RESULT: WARN"
else
  echo "RESULT: PASS"
fi
echo "Copy from '== Summary' down to here and paste it back."
[ "$FAILED" -eq 0 ]
