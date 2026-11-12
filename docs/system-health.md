# System health

The owner of a one-VM deployment has to know every day that backups ran, that the AI is real, that the disk and memory are not running out, that email can go out, that background jobs run, and that the API did not crash. System health answers those questions from real state on the server, in the platform console on the web and as a summary on the phone, and tells the owner in the app when something is red. It was added after the [product review of 12 November 2026](audits/2026-11-12-product-review.md) ("Basic reliability: daily backups confirmed, and an uptime check").

It is read-only: nothing on the page changes anything, and no secret, file path or log text is ever returned.

## Who can see it

Platform operators only (realm role `platform_operator`, `platform:operate`), the same people who see the platform console. Anyone else gets `403` and the page says who can grant the role.

## `GET /api/v1/platform/health`

A plain `def` route (files and one audit read run in FastAPI's thread pool, never on the event loop; `anum_api/system_health.py`). The answer:

- `checked_at`, `started_at` (this API process), `uptime_seconds`
- `overall`: `green`, `amber` or `red` (the worst check; an unknown check counts as amber)
- `counts`: how many checks are in each state
- `items[]`: `{key, state, detail}`, `state` is `green`, `amber`, `red` or `unknown`
- `you`: the caller's `tenant_id` and `workspace_id`, so the owner can paste them into `.env` for the notices

| Key | Read from | Green | Amber | Red |
|---|---|---|---|---|
| `backup_local` | `last-local.json` in `ANUM_BACKUP_STATUS_DIR` (written by `infra/oracle/backup.sh`) | last success within 26 hours | the last run failed but a success is within 26 hours | no success within 26 hours, or never; also a status folder that exists without a readable file |
| `backup_offsite` | `last-offsite.json` | as above | as above | as above |
| `restore_drill` | `last-drill.json` ([Restore drill](#restore-drill)) | last drill succeeded within 35 days | never run, or the last success is older than 35 days | the last drill failed |
| `ai_model` | `ANUM_MODEL_PROVIDER` and whether `ANUM_MODEL_API_KEY` is set (never its value) | a real provider with its key | | `mock` (placeholder answers) or a hosted provider without its key |
| `disk` | `shutil.disk_usage` of the backup status folder (the VM's disk), else `/` | 20% or more free | 10% to 20% free | under 10% free |
| `memory` | `/proc/meminfo` (`MemAvailable`, the VM) and the container's cgroup `memory.max` and `memory.current` | the tighter of the two has 20% or more free | 10% to 20% | under 10% |
| `email` | `ANUM_SMTP_HOST` and `ANUM_SMTP_FROM` set | both set | not set | |
| `scheduler` | `ANUM_AUTOMATION_SCHEDULER_ENABLED` and the heartbeats of the team reminder loop and the [command sweep](command.md#the-sweep) | on and a pass finished within max(5 minutes, 3 poll intervals), or starting (first 5 minutes) | off | on but no recent pass |
| `errors` | ERROR and CRITICAL log records of this API process in the last 24 hours (times only) | none | 1 to 49 | 50 or more |
| `restarts` | the audit log of the notice workspace ([Notices](#notices)) | no unexpected restart in 7 days | one in the last 7 days | one in the last 24 hours |
| `notices` | `ANUM_SYSTEM_HEALTH_TENANT_ID` and `ANUM_SYSTEM_HEALTH_WORKSPACE_ID` | both set | not set | |

Without a status folder the backup and drill checks are `unknown` ("this server cannot know"); without the notice settings the restart check is `unknown`. The error count is per process and starts at zero after a restart (the page says so).

Each check on the web and the phone shows why it matters, the numbers behind it and, when it is not green, what to do on the VM.

## Notices

With `ANUM_SYSTEM_HEALTH_TENANT_ID` and `ANUM_SYSTEM_HEALTH_WORKSPACE_ID` set (the owner's own organisation and workspace; the page shows both ids and the two lines to add to `infra/oracle/.env`), a monitor runs in the API process:

- **Daily notice.** Every `ANUM_SYSTEM_HEALTH_CHECK_SECONDS` (default 3600) it evaluates the checks. While something is red and no notice went out that UTC day, every active owner of that workspace gets one in-app notice (kind `system_health`): the red checks and their fixes. The day's notice is recorded in the audit log (`system.health_notice_sent`), so a restart does not send it twice. So a backup older than 26 hours reaches the owner within about an hour.
- **Unexpected restart.** At start the API reads the last `system.api_started` or `system.api_stopped` record of that workspace. A start with no clean stop after it means the previous process ended without shutting down (a crash, the kernel's out-of-memory killer, `heal.sh` restarting a hung container, a power cut): the owners get a `system_restart` notice at once and `system.api_restarted_unexpectedly` is recorded. A clean shutdown (an update, `docker compose restart`) records `system.api_stopped` and sends nothing.

Both kinds are in-app only (the web bell and the phone's Notifications screen), never an email or a phone push, and open System health. The restart check assumes one API replica, as on the Oracle VM; with several replicas each start would look unexpected, so leave the notice settings empty there.

## Restore drill

`backup.sh drill` restores the latest backup into a scratch database, verifies it and drops it ([Backups](deploy-oracle.md#backups)). It now writes `status/last-drill.json` like the backups, and `anum-drill.timer` runs it on the 1st of each month at 05:13 UTC. `install.sh` installs the timer; on a VM installed earlier, `sudo bash infra/oracle/update.sh` adds it (or `sudo bash infra/oracle/backup.sh drill-timer`).

## Outside uptime check

ANUM cannot say it is down while it is down. `GET /health` on the API host answers `{"status": "ok", "environment": ...}` without sign-in and is not rate limited, so a free external monitor can poll it. The page shows the address with a copy button. Steps for the owner ([Monitoring basics](deploy-oracle.md#monitoring-basics)):

1. Create a free account at UptimeRobot (50 monitors, 5-minute checks) or Better Stack (free tier).
2. Add an HTTP(S) monitor for `https://api.anum360os.com/health`, interval 5 minutes, keyword `ok` if the service offers a keyword check.
3. Add a second one for `https://app.anum360os.com/healthz` (the web app).
4. Set the alert contact to your email and, if offered, the phone app's push.

Nothing on the VM changes and no paid resource is used. Uptime Kuma on a machine of yours is the self-hosted alternative ([Monitoring the Oracle VM](monitoring-oracle.md)).

## Web

Platform console (`#platformBilling`), tab **System health**, also opened by link as `#platformBilling-health` (the notices go there). Checks are listed problems first, each with a green, amber, red or "not known" badge, the reason, the numbers and the fix; the notices check shows the two `.env` lines with the caller's ids; a second panel gives the uptime monitor address. Loading, refused (who can grant it), failed (with Try again) and all-green states. English and Arabic. Code: `apps/web/src/billing/ConsoleHealth.tsx`, `apps/web/src/lib/systemHealth.ts`; tests `apps/web/test/systemHealth.test.ts`, `apps/web/e2e/system-health.spec.ts` and the UI gate entry `platformBilling-health`.

## On the phone

Menu, **System health** (platform owners; route `/system-health`): the same checks problems first, each with its state, one line from its numbers and what to do; the full numbers and the `.env` lines are on the web. Pull to refresh or the refresh button. Refused, failed with retry and all-green states, English and Arabic. Code: `apps/mobile/lib/features/system_health/`; test `apps/mobile/test/owner_desk/system_health_test.dart`.

## Not done yet

- The error count is this process's log records only; errors of the worker, Keycloak or PostgreSQL are not counted (read them with `docker compose logs`).
- No history or chart: the page shows the state now. Uptime history lives in the external monitor.
- The notices go to one workspace's owners; there is no per-person choice of checks.
