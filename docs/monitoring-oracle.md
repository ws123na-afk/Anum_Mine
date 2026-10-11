# Monitoring the Oracle VM

Optional monitoring for the [one-VM deployment](deploy-oracle.md). It has three parts, and each one covers something the others miss:

| Part | Where it runs | What it tells you |
|---|---|---|
| **Uptime Kuma** | **Off the VM**: a home PC, a Raspberry Pi or a second cheap host | The app, API and sign-in are reachable from the internet, and certificates are not about to expire. The only part that can alert you when the whole VM, Docker, Caddy or the Oracle region is down. |
| **Beszel** (hub and agent) | On the VM, profile `monitoring` / `monitoring-agent` | CPU, memory, load, disk usage and disk I/O of the VM over time, with threshold alerts (memory nearly full, disk filling up). |
| **GlitchTip** | On the VM, profile `monitoring` | Every unhandled API error with its stack trace, route and tenant id, grouped into issues, with alerts on new issues, and optionally the web app's browser errors ([Web errors](#web-errors)). Sentry-compatible. |

Beszel and GlitchTip are off by default and turned on with one script, `infra/oracle/monitoring.sh`. Nothing in them publishes a port: Caddy serves each UI on its own hostname behind HTTP basic auth, in front of the tool's own login. Monitoring from the same VM is useless when the VM is down, so set up [Uptime Kuma off the VM](#uptime-kuma-off-the-vm) first, even if you skip the rest.

## What Gets Added

| Service | Image | Profile | Memory limit | Networks | What it does |
|---|---|---|---|---|---|
| `beszel` | `henrygd/beszel:0.20.0` | `monitoring` | 256 MB | `edge`, `monitoring` | The Beszel hub (UI, history, alerts). Data in the `beszel-data` volume. |
| `beszel-agent` | `henrygd/beszel-agent:0.20.0` | `monitoring-agent` | 128 MB | `monitoring` (internal) | Reads the host's CPU, memory, load and disk figures and sends them to the hub over the internal network. Started only after the hub has issued its key and token. |
| `glitchtip` | `glitchtip/glitchtip:6.2.6` | `monitoring` | 1 GB | `edge`, `backend` | GlitchTip web, event ingest and task worker in one process (all-in-one mode). Its own `glitchtip` login and database in the existing PostgreSQL; uploads in the `glitchtip-uploads` volume. |

All three images are published for `linux/arm64` and `linux/amd64` (checked against Docker Hub's manifests when the versions were pinned). Changes to the base stack, all inert while monitoring is off:

- **Caddy** imports `infra/oracle/sites-enabled/*.caddy`. The directory is empty in git; `monitoring.sh enable` writes a one-line import of `infra/oracle/monitoring.caddy` there, and `disable` removes it. Caddy therefore never requests a certificate for an unused hostname.
- **db-init** creates the `glitchtip` login and database (`infra/oracle/db/glitchtip.sql`) only when `GLITCHTIP_DB_PASSWORD` is set in `.env`. The login owns only that database. It is not a member of any ANUM role, and `anum` and `keycloak` do not grant it `CONNECT`.
- **PostgreSQL** runs with `max_locks_per_transaction=512`, GlitchTip's recommended floor for its partitioned event tables. This costs a few MB of shared memory. The first `update.sh` after this change restarts PostgreSQL once.
- **The API** reads `ANUM_SENTRY_DSN`. It is empty by default, so error reporting is off ([Observability: Error reporting](observability.md#error-reporting)).
- **The web build** takes `ANUM_WEB_SENTRY_DSN` as its `VITE_SENTRY_DSN` build argument. It is empty by default, so the bundle has no reporting code and the CSP is unchanged ([Web errors](#web-errors)).

### Why GlitchTip does not use Valkey

GlitchTip can run its cache and task queue in Valkey or in its own database. Here it uses the database (`VALKEY_URL` empty). The stack's Valkey runs with `noeviction` and a 256 MB cap because it holds the API's rate-limit counters and run locks. If GlitchTip's cache filled it, those writes would fail and the API would start refusing requests. At this event volume the database mode is fast enough, and it keeps the two systems independent.

### Why the agent has no Docker socket

Beszel can show per-container figures when its agent can reach the Docker API. On this host that is the `/var/run/docker.sock` socket, which amounts to root access. `docker inspect` through it would also expose every container's environment, which holds the passwords from `.env`. The agent therefore gets no socket and no host `/proc`. It still reports the host's CPU, memory, load, disk usage and disk I/O, because the kernel does not namespace those figures. Its network graph shows only the agent container's own traffic. For per-container memory, use `docker stats --no-stream` on the VM. For the same reason the hub runs with `CONTAINER_DETAILS=false`.

## RAM Budget on the 24 GB Ampere A1

Compose memory limits are caps, not reservations, and the services normally use far less than their caps. The table adds up the caps, so it shows the worst case:

| Group | Services and limits | Total |
|---|---|---|
| Base stack | PostgreSQL 4 GB, API 2 GB, Keycloak 2 GB, Valkey 384 MB, Caddy 256 MB, web 256 MB | 8.7 GB |
| Monitoring | GlitchTip 1 GB, Beszel hub 256 MB, Beszel agent 128 MB | 1.4 GB |
| `ai` profile (optional) | Ollama 12 GB | 12 GB |

- **Base plus monitoring: 10.1 GB of 24 GB.** This leaves about 14 GB for the kernel, Docker, the nightly backup jobs and the page cache that PostgreSQL relies on (`effective_cache_size=4GB`). This is the recommended setup.
- **Base plus `ai` plus monitoring: 22.1 GB.** This still fits, but leaves under 2 GB of headroom if every service hit its cap at the same time. Ollama is the only service that comes close to its cap, and only while a large model is loaded. If you run both, set a Beszel alert on memory above 90% (see [Beszel](#beszel)) and prefer a smaller `OLLAMA_MODEL`. `install.sh` also adds swap as a last resort.
- GlitchTip's web worker restarts itself at 640 MB (`GRANIAN_WORKERS_MAX_RSS`), before it reaches its 1 GB limit.
- Disk: Beszel keeps a few hundred MB of history at most. GlitchTip's events live in PostgreSQL and expire after `GLITCHTIP_MAX_EVENT_LIFE_DAYS` (30). Check `df -h /` in `monitoring.sh status` now and then.

## Before You Start

1. **Bring the VM up to date**: `cd ~/anum && sudo infra/oracle/update.sh`. The monitoring files, the `max_locks_per_transaction` setting and the API's `ANUM_SENTRY_DSN` support arrive with that update.
2. **Two more hostnames** whose DNS A records point at the VM's public IP, as for the other three ([DNS](deploy-oracle.md#dns)):
   - With **DuckDNS**, create two more subdomains on duckdns.org, for example `anum-monitor-yourname` and `anum-errors-yourname`. The script adds them to `DUCKDNS_DOMAINS`, so the existing `anum-duckdns.timer` keeps them pointed at the VM.
   - With **your own domain**, add `monitor.example.com` and `errors.example.com` A records.
3. **A password manager entry** for the basic-auth login, the GlitchTip admin and the Beszel admin.
4. Ports: nothing new. Caddy already listens on 80 and 443, so neither Oracle's security list nor the iptables rules change.

## Turn It On

On the VM, as the user who ran `install.sh` (the `ubuntu` user, which is in the `docker` group). No `sudo` is needed:

```bash
cd ~/anum && bash infra/oracle/monitoring.sh enable
```

The script walks you through these steps. Every secret is read with hidden input and written to `infra/oracle/.env` (mode 600) through `configure_env.py`. Secrets are never printed and never passed on a command line.

1. **Hostnames**: it asks for the Beszel and GlitchTip hostnames and validates them, adds DuckDNS names to `DUCKDNS_DOMAINS` and updates DuckDNS, then shows what each name resolves to.
2. **Basic auth**: it asks for a user name and a password (twice). Caddy hashes the password with bcrypt (`caddy hash-password`, fed on stdin) and only the hash is stored (`ANUM_MONITORING_BASIC_AUTH_HASH`).
3. **Secrets**: it generates `GLITCHTIP_SECRET_KEY` and `GLITCHTIP_DB_PASSWORD`. Copy both from `.env` into your password manager.
4. **Database**: it runs `db-init`, which creates GlitchTip's login and database.
5. **Start**: it pulls and starts `beszel` and `glitchtip`, and adds `monitoring` to `COMPOSE_PROFILES`. From then on `docker compose up -d` and `update.sh` keep them running and recreate them after updates.
6. **Caddy**: it writes the site import, validates the new configuration inside the running Caddy, and only then recreates Caddy with the new hostnames. If validation fails, it removes the import again and stops, and the ANUM sites are untouched. Recreating Caddy interrupts the three ANUM sites for a second or two. Caddy then gets certificates for the two new hostnames.
7. **GlitchTip admin**: it waits for GlitchTip to become healthy (the first start runs its migrations, which takes a few minutes), then asks for the admin e-mail and password and creates the account. GlitchTip sends no mail on this setup, so the address is only a login name.
8. **Beszel agent** (`monitoring.sh agent`): see [Beszel](#beszel).
9. **API DSN** (`monitoring.sh dsn`): see [GlitchTip](#glitchtip).
10. **Web DSN** (`monitoring.sh web-dsn`, optional): see [Web errors](#web-errors).

Press Enter at the agent or a DSN prompt to skip that step. You can run `monitoring.sh agent`, `monitoring.sh dsn` or `monitoring.sh web-dsn` later. Running `enable` again is safe: it keeps existing values and asks before changing the basic-auth login.

## Beszel

1. Open `https://<ANUM_MONITOR_HOST>` and sign in with the basic-auth login. On the first visit, Beszel asks you to create its admin account. Nobody else can reach that page, because basic auth is in front of it.
2. Click **Add System**. Use the name `anum-vm` (`BESZEL_SYSTEM_NAME`) and the host `beszel-agent`, and leave the port as it is. Copy the **Public Key** and the **Token**, then click **Add system**.
3. Paste the key and the token (the token is read hidden) into `monitoring.sh agent`. The script stores them as `BESZEL_AGENT_KEY` and `BESZEL_AGENT_TOKEN`, adds `monitoring-agent` to `COMPOSE_PROFILES` and starts the agent. The system turns green within a minute.

The agent connects out to the hub at `http://beszel:8090` on the internal `monitoring` network, authenticates with the token, and checks the hub against its public key. The hub never connects to the VM or to the agent.

**Alerts**: open the system, then the bell icon. Suggested thresholds: memory above 90% for 10 minutes, disk above 80%, CPU above 90% for 15 minutes. Set up a notification target under Settings → Notifications. Beszel sends to webhooks such as ntfy, Telegram, Discord or Slack (Shoutrrr URLs) without any mail server. E-mail notifications need SMTP settings in Beszel's settings.

**Optional extra protection**: Beszel's own two-factor login sends one-time codes by e-mail. It is off here because the stack has no mail server. The basic-auth layer in front of Beszel is the second factor.

## GlitchTip

1. Open `https://<ANUM_ERRORS_HOST>`, pass basic auth, then sign in with the GlitchTip admin from step 7. Self-registration and organization creation by other users are off.
2. Create an **organization**, then a **project** with the platform Python or FastAPI.
3. Under **Project settings → Client Keys (DSN)**, copy the DSN, which looks like `https://<key>@<ANUM_ERRORS_HOST>/<id>`.
4. Paste it into `monitoring.sh dsn` (read hidden). The script stores it as `http://<key>@glitchtip:8000/<id>`, which is the private Docker network: the API never sends errors out through the public address and back in. It then recreates the API and waits until the API is healthy. The API's startup policy accepts plain http only to such a dotless service name ([Error reporting](observability.md#error-reporting)).

**Send a test event** (on the VM, from `~/anum/infra/oracle`). The issue "ANUM error reporting test" should appear in the project within a few seconds:

```bash
docker compose exec api python -c "import sentry_sdk; from anum_api.error_reporting import setup_error_reporting; from anum_api.settings import settings; setup_error_reporting(settings, service_name='anum-api'); sentry_sdk.capture_message('ANUM error reporting test'); sentry_sdk.flush(5)"
```

**What arrives**: the exception type and stack frames (file, function, line), the route, the request method and path, the environment, `server_name` (`anum-api`), and the tags `anum.tenant_id` and `anum.correlation_id`. Exception messages, request bodies, query strings, headers, cookies, local variables, source lines, user identity and client addresses are stripped before sending. To find the API log lines for an event, search the logs for its correlation id: `docker compose logs api | grep <anum.correlation_id>`.

**Alerts**: Project → **Alerts** → create a rule for new issues, and add a webhook, Discord or Slack recipient. E-mail alerts need SMTP, which this setup does not configure.

**Retention**: events older than `GLITCHTIP_MAX_EVENT_LIFE_DAYS` (30) are deleted. GlitchTip's database is not in the nightly backup: error events are diagnostics, and a restore after losing the VM starts GlitchTip empty. Recreate the project and run `monitoring.sh dsn` again if that happens.

**Lost admin password**: `docker compose exec glitchtip python manage.py changepassword <e-mail>`, or run `monitoring.sh enable` again and give the same e-mail at the admin prompt.

### Web errors

The web app can report errors that happen in visitors' browsers to a second GlitchTip project. It is off by default. What is sent and what is stripped is described in [Observability: Browser errors](observability.md#browser-errors-web-app). In short: the exception type, the stack frames, the page path without its query, and the tenant id. Exception messages, user identity, headers, query strings and token-bearing breadcrumbs are stripped.

1. In GlitchTip, create a second **project** with the platform JavaScript (Browser). Keep the API's project separate, so browser and server issues do not mix.
2. Under **Project settings → Client Keys (DSN)**, copy its DSN (`https://<key>@<ANUM_ERRORS_HOST>/<id>`).
3. Run `bash infra/oracle/monitoring.sh web-dsn` and paste it. `enable` also asks for it at the end. The script accepts only an `https` DSN on your `ANUM_ERRORS_HOST`, stores it as `ANUM_WEB_SENTRY_DSN` in `.env`, then rebuilds and restarts the web container (`docker compose up -d --build --no-deps web`).

The browser DSN stays the public address, unlike the API's private one, because browsers send to it directly: Caddy passes GlitchTip's ingest path (`/api/<id>/envelope/`) through without basic auth (`monitoring.caddy`), and the project key authenticates the event. The key is compiled into the bundle every visitor downloads, so treat it as public: it can only submit events to that project. If it is abused, rotate it under Client Keys and run `web-dsn` again.

The DSN is a build argument (`VITE_SENTRY_DSN` in `compose.yaml`), so changing it always needs a rebuild of the web image. `update.sh` keeps it, because the value lives in `.env`. The image adds the DSN's origin (`https://<ANUM_ERRORS_HOST>`) to the CSP's `connect-src` at build time, and only when a DSN is set. Check it from your computer with `curl -sI https://<ANUM_WEB_HOST>/ | grep -i content-security-policy`. `bash infra/oracle/monitoring.sh web-dsn --clear` turns browser reporting off and rebuilds, which also removes the origin from the CSP. `disable` clears it as well when it points at this GlitchTip.

**Test it**: open the web app, open the browser's developer console and run `setTimeout(() => { throw new Error('ANUM web error reporting test') })`. An issue of type `Error` (message `[redacted]`) appears in the browser project within a few seconds, tagged with your `anum.tenant_id`.

## Uptime Kuma Off the VM

Run Uptime Kuma on a machine that is not the VM and is usually on: a home PC with Docker Desktop, a NAS or a Raspberry Pi. Its web UI listens on `127.0.0.1` only, on that machine.

With Docker Desktop (PowerShell on Windows, or any shell):

```bash
docker volume create uptime-kuma
docker run -d --name uptime-kuma --restart unless-stopped -p 127.0.0.1:3001:3001 -v uptime-kuma:/app/data louislam/uptime-kuma:2.5.6
```

Or as a compose file (`compose.yaml` in an empty folder, then `docker compose up -d`):

```yaml
services:
  uptime-kuma:
    image: louislam/uptime-kuma:2.5.6
    restart: unless-stopped
    ports:
      - "127.0.0.1:3001:3001"
    volumes:
      - uptime-kuma:/app/data
    mem_limit: 512m
volumes:
  uptime-kuma:
```

Open `http://127.0.0.1:3001`, create the admin account, add a notification (Settings → Notifications: e-mail via SMTP, ntfy, Telegram, Discord and others), then add these monitors. Replace the hostnames with yours. For every HTTP monitor, turn on **Certificate Expiry Notification**.

| Monitor | Type | URL | Expect | Interval / retries |
|---|---|---|---|---|
| ANUM app | HTTP(s) | `https://<ANUM_WEB_HOST>/healthz` | status 200 | 60 s / 2 |
| ANUM API | HTTP(s) - Keyword | `https://<ANUM_API_HOST>/health` | keyword `"status":"ok"` | 60 s / 2 |
| ANUM sign-in (Keycloak) | HTTP(s) - Keyword | `https://<ANUM_AUTH_HOST>/realms/anum/.well-known/openid-configuration` | keyword `"issuer":"https://<ANUM_AUTH_HOST>/realms/anum"` | 120 s / 2 |
| ANUM API refuses anonymous calls | HTTP(s) | `https://<ANUM_API_HOST>/api/v1/tasks` | Accepted status codes: `401` only | 300 s / 1 |
| Monitoring sites behind basic auth (if enabled) | HTTP(s) | `https://<ANUM_MONITOR_HOST>/` and `https://<ANUM_ERRORS_HOST>/` | Accepted status codes: `401` only | 300 s / 1 |

The first three checks show whether users can open ANUM, call the API and sign in. The fourth alerts when the API stops enforcing authentication. The last checks Caddy and the basic-auth layer: a `200` there means the monitoring UIs are reachable without the login.

If no machine of yours is always on, use any hosted uptime service with a free tier and the same checks. The point is that the checks run somewhere other than the VM.

## Status, Turning Off and Removal

```bash
bash infra/oracle/monitoring.sh status    # profiles, containers, memory use, free RAM, 401 checks of both sites
bash infra/oracle/monitoring.sh disable   # stop and remove the three containers and the Caddy sites
bash infra/oracle/monitoring.sh purge     # disable, then delete all monitoring data (asks you to type "purge")
```

**`disable`** removes `monitoring` and `monitoring-agent` from `COMPOSE_PROFILES`, removes the Caddy site import and reloads Caddy, then stops and removes `beszel`, `beszel-agent` and `glitchtip`. If the API was reporting to this GlitchTip, it also clears `ANUM_SENTRY_DSN` and recreates the API. The volumes, GlitchTip's database and the secrets in `.env` stay, so `enable` brings back the same history, accounts and projects. After re-enabling, run `monitoring.sh dsn` again.

**`purge`** runs `disable`, then drops the `glitchtip` database and login, deletes the `beszel-data`, `beszel-agent-data` and `glitchtip-uploads` volumes, and empties the basic-auth, GlitchTip and Beszel values in `.env`. To remove everything:

1. `bash infra/oracle/monitoring.sh purge`.
2. Delete the two DNS records, or the two DuckDNS subdomains, and remove their names from `DUCKDNS_DOMAINS` in `.env`. Empty `ANUM_MONITOR_HOST` and `ANUM_ERRORS_HOST`.
3. Optionally prune the images: `docker image rm henrygd/beszel:0.20.0 henrygd/beszel-agent:0.20.0 glitchtip/glitchtip:6.2.6`.

`max_locks_per_transaction=512` stays in the compose file; it is harmless without GlitchTip. Uptime Kuma lives on your own machine: `docker rm -f uptime-kuma && docker volume rm uptime-kuma` there.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Browser asks for a login again and again | Wrong basic-auth password. Run `enable` again and choose a new login at the basic-auth prompt. |
| Certificate error on a monitoring host | Its DNS record does not point at the VM yet. Caddy keeps retrying; check with `nslookup <host>` and `docker compose logs caddy`. |
| `glitchtip` not healthy | `docker compose logs --tail 200 glitchtip`. On the first start, the migrations can take several minutes. "run infra/oracle/monitoring.sh enable" in the log means a secret or the hostname is missing in `.env`. |
| `beszel-agent` unhealthy or restarting | The key or token is wrong, or the system was not added in Beszel. Run `monitoring.sh agent` again with fresh values from **Add System**. |
| API does not start after `dsn` | `docker compose logs api` shows the startup refusal. Only `https://` DSNs, or `http://` to a name without dots such as `glitchtip`, are accepted. Run `monitoring.sh dsn --clear` to turn reporting off. |
| No events in GlitchTip | `docker compose exec api printenv ANUM_SENTRY_DSN` should print the `http://...@glitchtip:8000/...` value. Send the test event above. |

## How This Is Checked

- `services/api/tests/test_oracle_compose.py`: monitoring stays off in `.env.example`; none of the three services publishes or exposes a port, mounts the Docker socket or `/proc`, or keeps a Linux capability; the agent is only on the internal network; GlitchTip has its own database login, no Valkey and refuses the default secret key; GlitchTip's and Beszel's secrets reach only their services and `db-init`; the memory limits stay within this budget; Caddy imports the monitoring sites only from `sites-enabled/`, behind basic auth except for GlitchTip's ingest endpoints; `monitoring.sh` reads every secret hidden, edits `.env` through `configure_env.py` (mode 600, quoted bcrypt hash) and uses `sudo -n true || sudo -v`; and the DSN `monitoring.sh` writes passes the API's production startup policy.
- `services/api/tests/test_error_reporting.py`: the DSN checks, the startup policy, the scrubbing rules, and one end-to-end run of the real Sentry SDK against a FastAPI route that confirms no token, cookie, query, body, exception message, local variable or log argument is sent.
- CI's "Docker Compose config" job runs `docker compose config` with `--profile monitoring --profile monitoring-agent` and ShellCheck on `monitoring.sh`. The Caddy configuration was validated with `caddy validate` (v2.11.7) with and without the monitoring sites. This setup has not yet been run on an Oracle VM. Treat the first `enable` on staging as its acceptance test, including the test event and an Uptime Kuma alert that fires when you stop the `web` container.
