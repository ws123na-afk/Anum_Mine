# Deploying on One Oracle Cloud VM

This is the $0 deployment path for an owner with a Windows PC, PowerShell and no Kubernetes: the whole of ANUM runs on **one** Oracle Cloud "Always Free" virtual machine (Ampere A1, arm64, 4 OCPU, 24 GB RAM, Ubuntu 24.04) with Docker Compose. Everything here is driven by `infra/oracle/` (a compose file, a Caddyfile and a few scripts), the Oracle Cloud Console in a browser, and Windows PowerShell with SSH.

The other two paths stay in the repository: [Render](deploy-render.md) (paid, managed databases, nothing to patch yourself) and [Kubernetes with the Helm chart](deployment.md) (the strict path). All three use the same Dockerfiles, the same `infra/helm/bootstrap-database.sql` roles, the same startup refusals and the same expand/contract migration rule.

The first environment is **staging** (`ANUM_ENVIRONMENT=staging`). Production on this path is a second VM with its own `.env`, buckets and Keycloak ([Production on one VM](#production-on-one-vm)).

## What Gets Created

All on the one VM, from `infra/oracle/compose.yaml` (compose project `anum`):

| Service | Image | What it does |
|---|---|---|
| `caddy` | `caddy:2.11.7-alpine` | The only container with published ports (TCP 80 and 443). Gets and renews Let's Encrypt certificates for the three hostnames, redirects http to https, proxies to `web`, `api` and `keycloak`. Sets `X-Forwarded-For` itself (a client's own header is replaced). Blocks Keycloak's `/admin` from addresses outside `ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS`. |
| `postgres` | `pgvector/pgvector:0.8.7-pg17-trixie` | PostgreSQL 17 with pgvector. Two databases: `anum` (ANUM's data, RLS) and `keycloak` (Keycloak's own, its own login). Data in the `postgres-data` volume. Not published, on an internal network. |
| `db-init` | same image, one-shot | Runs `infra/oracle/db-init.sh` on every `up`: Keycloak's login and database, the `anum_backup` login, then `infra/helm/bootstrap-database.sql` (the same file as on Render and Kubernetes), then sets every password from `.env` and lets only the right logins connect to each database. |
| `migrate` | API image, one-shot | `python -m alembic upgrade head` as `anum_migrator` (the schema owner). The API starts only after it succeeded. |
| `api` | `services/api/Dockerfile`, built on the VM | FastAPI as `anum_app` (RLS applies): OIDC, PostgreSQL repository, Valkey rate limits and run locks, inline runs, events in PostgreSQL, files in an S3-compatible bucket. Health check `/health`. |
| `web` | `apps/web/Dockerfile`, built on the VM | The Vite bundle on unprivileged nginx with the same CSP as everywhere. Health check `/healthz`. |
| `keycloak` | `infra/render/keycloak.Dockerfile`, built on the VM | Keycloak 26.4 with the `anum` realm imported on first start, without the development user (the same image as on Render). |
| `valkey` | `valkey/valkey:9.1-alpine` | Rate limits and run locks; no persistence, `noeviction`. Internal network only. |
| `retention` | API image, profile `jobs` | `python -m anum_api.voice_retention`, daily at 03:17 UTC from the `anum-retention.timer` systemd timer. |
| `backup`, `restore-tools` | `infra/backup/Dockerfile`, profile `jobs` | `anum_backup.py` backups, restores and the restore drill, plus a `pg_dump` of Keycloak's database. Nightly at 02:41 UTC from `anum-backup.timer` (`infra/oracle/backup.sh`). |
| `backup-upload` | `rclone/rclone:1.75.2`, profile `jobs` | Copies the dumps to an off-site bucket, encrypted on the VM first (rclone `crypt`). |
| `ollama`, `ollama-pull` | `ollama/ollama:0.40.3`, profile `ai` (optional) | A local model on the VM's CPU ([AI model](#ai-model)). |

Three Docker networks keep the pieces apart: `edge` (Caddy, web, API, Keycloak), `backend` (`internal`: no internet; PostgreSQL, Valkey, the jobs, and the API and Keycloak) and `egress` (outbound internet for the API, Keycloak, the backup upload and Ollama). Caddy, the only container the internet reaches, is on `edge` only, so it cannot even open a connection to PostgreSQL or Valkey.

Every image exists for `linux/arm64`, and every image ANUM builds is built on the VM (`docker compose build`): no registry, no image upload. The API's Python dependencies all have `aarch64` wheels, and the web build's native packages (`rolldown`, `lightningcss`, the TypeScript native binary) have `linux-arm64` builds in `pnpm-lock.yaml`.

Not on this path, as on Render: no Temporal worker (`ANUM_RUNTIME_BACKEND=inline`: a run stops if the API restarts mid-run and is resumed with `POST /api/v1/agent-runs/{id}/resume`), no NATS (`ANUM_EVENT_BUS=memory`: events stay in PostgreSQL and the SSE stream reads them; [Events](events.md)), no OpenTelemetry collector (set `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` if you have a hosted OTLP endpoint; [Observability](observability.md)).

## What You Give Up Compared With Kubernetes and Render

| Kubernetes path / Render | On one VM |
|---|---|
| Kubernetes: images built once in CI, Trivy-scanned, cosign-signed, SBOM-attested, verified and admitted by digest ([Image supply chain](deployment.md#image-supply-chain)). | The VM builds its own images from the commit you pulled, with the same Dockerfiles. CI still Trivy-scans the API, web and backup Dockerfiles on every PR, but nothing signs or verifies what runs on the VM. You trust your GitHub account and the VM. |
| Kubernetes: replicas, zone spread, PodDisruptionBudgets, rolling updates with no dropped requests. Render: zero-downtime deploys. | One copy of everything on one machine. An update recreates the API and web containers: a few seconds of errors. A VM, disk or Oracle region problem takes everything down until you restore. |
| Managed PostgreSQL with provider backups and point-in-time recovery (Render, cloud providers). | You run PostgreSQL. Nightly logical dumps (RPO 24 hours) on the VM and, encrypted, in a bucket; no point-in-time recovery. Run the restore drill ([Backups](#backups)). |
| Default-deny NetworkPolicies. | Three Docker networks (above) and a host firewall; outbound traffic from `egress` is unrestricted. |
| The migration login in its own Secret (Kubernetes); in the API service's environment (Render). | Better than Render: the migration password reaches only the `db-init` and `migrate` one-shots, never the API container. But every secret is in one file, `infra/oracle/.env` (mode 600), readable by anyone with `sudo` on the VM. |
| Read-only root filesystem, dropped capabilities, seccomp (Kubernetes). | Docker's defaults. The containers still run as the non-root users of the Dockerfiles (API uid 10001, nginx uid 101, Keycloak uid 1000). |
| Patching is the platform's job (Render) or the node image's (Kubernetes). | Yours: `install.sh` turns on unattended security upgrades (with automatic reboots at 04:30 UTC when needed); image base updates come with `update.sh`. |
| Production deploys behind a GitHub `production` environment approval. | You decide when `update.sh` runs. The optional daily auto-update is for staging only. |

What stays the same: OIDC with Keycloak, the API's startup refusals, PostgreSQL row-level security with the non-owner `anum_app` login and the narrow NOLOGIN roles, Fernet-encrypted provider keys, the web CSP and security headers, the SSRF guard, budgets and approvals.

## Costs

$0 while you stay inside Oracle's Always Free limits: at the time of writing, Ampere A1 up to 4 OCPUs and 24 GB of memory in total, 200 GB of block volume in total (the boot volume counts), and 10 TB of outbound traffic a month. Backblaze B2's free tier covers the first 10 GB stored (files and backups together; check [backblaze.com/cloud-storage/pricing](https://www.backblaze.com/cloud-storage/pricing)). DuckDNS is free. A domain of your own is optional (roughly $10 a year). Oracle's free-tier terms change: check [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) before you start, and set the budget alert below.

## Before You Start

On the Windows PC:

1. **PowerShell** (Windows PowerShell 5.1 or PowerShell 7) with the **OpenSSH client**, built into Windows 10 and 11. Check with `ssh -V`. If it is missing: Settings → System → Optional features → Add a feature → "OpenSSH Client".
2. A **password manager**. You will store about a dozen generated secrets there.

Accounts:

- **Oracle Cloud** ([Create the Oracle Cloud account](#create-the-oracle-cloud-account)).
- **Backblaze B2** for files and backups ([File storage](#file-storage)), or any S3-compatible storage with versioning.
- **GitHub** access to this repository from the VM ([Get the code onto the VM](#get-the-code-onto-the-vm)).
- Either a **domain** whose DNS you control, or a free **DuckDNS** account ([DNS](#dns)).

## Create the Oracle Cloud Account

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/). A credit card is needed for identity checks; Always Free resources are not charged.
2. **Choose the home region carefully.** It cannot be changed later, and Always Free compute only runs in the home region. Pick one close to your users that also tends to have Ampere capacity; large regions (for example Frankfurt, Ashburn, Phoenix) are often "out of capacity" for A1, smaller ones less so. There is no reliable public list; if you have no preference, a less popular region nearby is a reasonable bet.
3. **Upgrade to Pay As You Go** (Console → Billing & Cost Management → Upgrade and Manage Payment). Recommended: on a free-trial or free-tier account Oracle may stop ("reclaim") Always Free instances it considers idle (a staging ANUM is mostly idle), and A1 capacity is easier to get on paid accounts. You are still charged nothing as long as you only use Always Free resources ("Always Free-eligible" in the Console).
4. **Set a budget alert at $1** right away: Billing & Cost Management → Budgets → Create Budget → target your root compartment, monthly amount `1`, alert rule "Actual spend" at 100 % to your e-mail. Any charge then reaches you within a day.

## Create the VM

### SSH key (PowerShell)

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\anum_oracle" -C "anum-oracle"
Get-Content "$HOME\.ssh\anum_oracle.pub"    # the public key, one line starting with ssh-ed25519
```

Give the key a passphrase when asked. The private key `anum_oracle` never leaves your PC.

### Instance

Console → Compute → Instances → **Create instance**:

1. **Name** `anum-staging`. Compartment: your root compartment (or one you made).
2. **Placement**: any availability domain. If one says "out of capacity", try the next.
3. **Image and shape** → Edit:
   - Image: **Canonical Ubuntu 24.04** (the aarch64 build is selected automatically with an Ampere shape).
   - Shape: **Ampere** → **VM.Standard.A1.Flex**, **4 OCPUs**, **24 GB** memory. It must show "Always Free-eligible".
4. **Networking**: create a new virtual cloud network and public subnet (the defaults), and **Assign a public IPv4 address**: yes.
5. **Add SSH keys**: "Paste public keys", paste the line from `anum_oracle.pub`.
6. **Boot volume**: "Specify a custom boot volume size", for example **100 GB** (up to 200 GB stays free; images, the database and local backups live here).
7. **Create**.

**"Out of capacity for shape VM.Standard.A1.Flex"** is common. Try another availability domain; try 2 OCPU / 12 GB and resize later (Instance → Edit shape, while stopped); try again at a different time of day (early morning in the region's time zone is often better); upgrading to Pay As You Go usually helps most. Do not run scripts that hammer the create API; Oracle may limit the account.

When the instance is **Running**, note its **Public IP address** (instance page). This guide calls it `<VM_IP>`. Optionally make it permanent: instance → Attached VNICs → the VNIC → IPv4 Addresses → the public IP → edit → **Reserved public IP** (check that it stays "Always Free-eligible"; otherwise keep the ephemeral one, which survives reboots, and let the DuckDNS timer follow it if it ever changes).

### Open ports 80 and 443 in Oracle's network

Oracle filters traffic twice: the VCN's **security list** in the cloud, and **iptables on the VM** (Oracle's Ubuntu images reject everything except SSH). `install.sh` handles the VM; the security list is yours:

Console → Networking → Virtual cloud networks → your VCN → **Subnets** → the public subnet → **Security** (or Security Lists) → **Default Security List** → **Add Ingress Rules**, twice:

| Source type | Source CIDR | IP protocol | Destination port range | Description |
|---|---|---|---|---|
| CIDR | `0.0.0.0/0` | TCP | `80` | HTTP: Let's Encrypt challenges and the redirect to https |
| CIDR | `0.0.0.0/0` | TCP | `443` | HTTPS |

Leave the existing SSH rule (TCP 22). For more safety, change its source from `0.0.0.0/0` to your home IP as `/32` (find it with `Invoke-RestMethod https://api.ipify.org`); if your IP changes you then edit the rule again before you can log in. UDP 443 (HTTP/3) is not needed: Caddy is configured for HTTP/1.1 and HTTP/2 only.

## DNS

Decide the three public addresses now; they are compiled into the web bundle and written into Keycloak's realm on its first start.

| Address | With your own domain | With DuckDNS (free) |
|---|---|---|
| Web | `app.staging.example.com` | `anum-app-yourname.duckdns.org` |
| API | `api.staging.example.com` | `anum-api-yourname.duckdns.org` |
| Keycloak | `auth.staging.example.com` | `anum-auth-yourname.duckdns.org` |

- **Own domain**: at your DNS provider, add three **A** records (`app.staging`, `api.staging`, `auth.staging`) with the value `<VM_IP>`. If the provider proxies traffic (for example Cloudflare's orange cloud), turn the proxy **off** ("DNS only"): Caddy must be reached directly, and the forwarded-header trust in the [Caddyfile](../infra/oracle/Caddyfile) assumes no proxy in front.
- **DuckDNS**: sign in at [duckdns.org](https://www.duckdns.org), add three subdomains (the free account allows five), set each one's IP to `<VM_IP>`. Note the **token** at the top of the page. `install.sh` adds a timer that keeps the names on the VM's IP when `DUCKDNS_TOKEN` and `DUCKDNS_DOMAINS` are set.

Check from PowerShell before installing (each must print `<VM_IP>`):

```powershell
Resolve-DnsName app.staging.example.com -Type A | Select-Object -ExpandProperty IPAddress
```

The rest of this guide writes `<WEB>`, `<API>` and `<AUTH>` for the three host names (no `https://`).

## File Storage

The API keeps file bytes in an S3-compatible bucket with **versioning** (`ANUM_OBJECT_STORAGE_BACKEND=s3`, [Workspace files](files.md)), exactly as on Render ([Deploying on Render: File storage](deploy-render.md#file-storage) has the details and the AWS S3 alternative). With Backblaze B2, create **two** private buckets, each with its own application key:

| Bucket | Example name | Settings | Application key |
|---|---|---|---|
| Files | `anum-staging-files` | Private, Default Encryption on, "Keep all versions" | This bucket only, Read and Write → `ANUM_S3_ACCESS_KEY` (keyID), `ANUM_S3_SECRET_KEY` (applicationKey) |
| Backups | `anum-staging-backups` | Private, Default Encryption on; **Lifecycle Settings** → custom rule: file name prefix `anum-backups/`, hide files 35 days after upload, delete hidden files 1 day later | This bucket only → `ANUM_BACKUP_S3_ACCESS_KEY`, `ANUM_BACKUP_S3_SECRET_KEY` |

The bucket page shows the **Endpoint**, for example `s3.us-west-004.backblazeb2.com`: the endpoint is then `https://s3.us-west-004.backblazeb2.com` and the region `us-west-004` (for both buckets if they are in the same account).

Stronger, optional: create the backup key **without delete rights**, so that someone who takes over the VM cannot delete the off-site copies (the lifecycle rule still expires them). The web console's "Read and Write" includes deletion; a restricted key needs Backblaze's command-line tool, on the PC: `py -m pip install b2`, `b2 account authorize`, then `b2 key create --bucket anum-staging-backups anum-vm-backups listFiles,readFiles,writeFiles`.

## Get the Code onto the VM

SSH in from PowerShell (the first time, answer `yes` to the host key question):

```powershell
ssh -i "$HOME\.ssh\anum_oracle" ubuntu@<VM_IP>
```

On the VM, if the repository is **public**: `git clone https://github.com/<owner>/<repo>.git ~/anum`.

If it is **private**, give the VM a read-only deploy key (it can read this one repository and nothing else):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/anum_deploy -N "" -C "anum-vm"
cat ~/.ssh/anum_deploy.pub
printf 'Host github.com\n  IdentityFile ~/.ssh/anum_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```

On GitHub: repository → Settings → Deploy keys → Add deploy key → paste the `.pub` line, **without** write access. Then `git clone git@github.com:<owner>/<repo>.git ~/anum`.

## Run install.sh

```bash
bash ~/anum/infra/oracle/install.sh
```

It runs as the `ubuntu` user and uses `sudo`. In order, and safe to run again:

1. Installs Docker Engine, Buildx and the compose plugin from Docker's apt repository (removing Ubuntu's `docker.io` if present), sets Docker's log rotation and `live-restore`, adds `ubuntu` to the `docker` group (takes effect at the next login).
2. Enables unattended upgrades: security updates daily, Docker Engine updates too, automatic reboot at 04:30 UTC when an update needs one. The containers come back by themselves (`restart: unless-stopped`).
3. Opens TCP 80 and 443 in the host firewall: inserts `ACCEPT` rules before Oracle's final `REJECT` rule, live and in `/etc/iptables/rules.v4` so they survive reboots. It does **not** use ufw (which conflicts with Oracle's rules and does not filter Docker's published ports anyway) and never runs `netfilter-persistent save`, which would store Docker's own chains in the rules file. Do not run it yourself either.
4. Adds a 4 GB swap file if there is none (headroom for the image builds).
5. Creates `infra/oracle/.env` from `.env.example` with mode 600 (`infra/oracle/configure_env.py`): every `generate-on-server` value becomes a fresh secret from the OS random generator (database passwords as url-safe base64; `ANUM_SECRETS_KEY` as a Fernet key, 44 characters), and it **asks** for the values only you have ([Values you supply](#values-you-supply)). Secret answers are typed without echo. Nothing is printed. If you stop it, edit the file (`nano ~/anum/infra/oracle/.env`) and run `install.sh` again.
6. Creates the backup directory `/var/lib/anum/backups` (owner uid 10001, mode 700).
7. Points the DuckDNS names at the VM, if configured.
8. Builds the images on the VM and starts the stack (`update.sh --no-pull`): PostgreSQL and Valkey, then `db-init` (roles, as on Render: migrations as `anum_migrator`, the API as `anum_app`), then the migrations, then everything else. The first build takes 10 to 20 minutes on the A1; later builds use the cache.
9. Installs the systemd timers: `anum-retention.timer` (03:17 UTC), `anum-backup.timer` (02:41 UTC), and, when enabled in `.env`, `anum-update.timer` (04:05 UTC) and `anum-duckdns.timer` (every 5 minutes).

### Values you supply

| Variable in `infra/oracle/.env` | Value | Where it comes from |
|---|---|---|
| `ANUM_WEB_HOST`, `ANUM_API_HOST`, `ANUM_AUTH_HOST` | `<WEB>`, `<API>`, `<AUTH>` (host names only) | [DNS](#dns) |
| `ACME_EMAIL` | your e-mail | Let's Encrypt sends certificate warnings there. |
| `ANUM_S3_ENDPOINT`, `ANUM_S3_REGION`, `ANUM_S3_BUCKET` | `https://s3.<region>.backblazeb2.com`, `<region>`, the files bucket | [File storage](#file-storage) |
| `ANUM_S3_ACCESS_KEY`, `ANUM_S3_SECRET_KEY` | the files bucket's keyID and applicationKey | B2 → Application Keys (shown once) |
| `ANUM_BACKUP_S3_ENDPOINT`, `ANUM_BACKUP_S3_REGION`, `ANUM_BACKUP_S3_BUCKET` | the backup bucket's endpoint, region, name | [File storage](#file-storage) |
| `ANUM_BACKUP_S3_ACCESS_KEY`, `ANUM_BACKUP_S3_SECRET_KEY` | the backup bucket's keyID and applicationKey | B2 → Application Keys |
| Optional: `DUCKDNS_TOKEN`, `DUCKDNS_DOMAINS` | token; names without `.duckdns.org`, comma-separated | duckdns.org |
| Optional: `ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS` | for example `203.0.113.7/32` (your home IP) | `Invoke-RestMethod https://api.ipify.org` |
| Optional: `ANUM_MODEL_*`, `COMPOSE_PROFILES`, `OLLAMA_MODEL` | see [AI model](#ai-model) | |
| Optional: `ANUM_AUTO_UPDATE` | `true` for a daily update (staging) | |
| Optional: `ANUM_ENVIRONMENT` | `staging` (default) or `production` | |

Generated on the VM, never typed by you: `ANUM_POSTGRES_PASSWORD` (superuser, used only by `db-init` and the restore tools), `ANUM_MIGRATOR_PASSWORD`, `ANUM_APP_PASSWORD`, `ANUM_RELAY_PASSWORD`, `ANUM_BACKUP_DB_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `ANUM_SECRETS_KEY`, `KC_BOOTSTRAP_ADMIN_PASSWORD`, `ANUM_BACKUP_ENCRYPTION_PASSPHRASE`. **Copy them into your password manager now** (`sudo cat ~/anum/infra/oracle/.env` in the SSH window, then clear the terminal). Without `ANUM_BACKUP_ENCRYPTION_PASSPHRASE` the off-site backups cannot be decrypted, and without `ANUM_SECRETS_KEY` stored provider keys cannot be read. The file is gitignored; never copy it into the repository, a chat or an e-mail.

`.env` is the source of truth for the database passwords: `db-init` sets them from it on every `docker compose up`. To rotate one, put a new value in `.env` (same format: letters, digits, `-`, `_`, at least 24 characters) and run `cd ~/anum/infra/oracle && docker compose up -d`.

## Keycloak

The realm is imported on the first start exactly as on Render ([Deploying on Render: Keycloak](deploy-render.md#keycloak)): no development user, the web client's URLs set to `https://<WEB>` from `ANUM_WEB_ORIGIN`. The import is skipped once the realm exists.

Day one, before anyone else uses it:

1. Open `https://<AUTH>/admin/`. User `anum-bootstrap-admin`, password `KC_BOOTSTRAP_ADMIN_PASSWORD` from `.env`.
2. Realm **master** → **Users** → **Add user** with your name and e-mail → **Credentials**: a long password (not temporary) → **Role mapping** → **Assign role** → realm role `admin` → **Required user actions**: `Configure OTP`.
3. Sign out, sign in as the new user, set up OTP, then **Users** → `anum-bootstrap-admin` → **Delete**.
4. On the VM, empty both bootstrap values in `.env` (keep the lines, so `update.sh` does not add them again): `KC_BOOTSTRAP_ADMIN_USERNAME=` and `KC_BOOTSTRAP_ADMIN_PASSWORD=`; then `cd ~/anum/infra/oracle && docker compose up -d keycloak`. Keycloak only creates the bootstrap admin against an empty database, so this only removes the password from the container.
5. Optional but recommended: set `ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS` to your home IP (`203.0.113.7/32`; several separated by spaces), then `docker compose up -d caddy`. Others then get 403 on `/admin`; sign-in for ANUM users (`/realms/anum/...`) is unaffected. If your IP changes, edit it over SSH.
6. Realm **anum** → **Realm settings**: "User registration" stays off; set up e-mail if you want password-reset mail. Then **Clients** → `anum-web` → **Valid redirect URIs** must show `https://<WEB>/*`.

Create ANUM users as on Render: realm **anum** → **Users** → **Add user**, attribute `tenant_id` (and optionally `default_workspace_id`), realm role `owner` for a tenant's first person, a temporary password sent out of band.

## AI Model

The VM has no GPU. Staging starts with `ANUM_MODEL_PROVIDER=mock` (placeholder answers; production refuses it in the release gate). Three real options:

**Ollama on the VM (profile `ai`).** Free and private: prompts never leave the VM. Honest about speed: on 4 Ampere cores a 3-billion-parameter model (the default `qwen2.5:3b`, quantized, about 2 GB) produces in the order of a few to ten words per second, and a long answer takes tens of seconds; 7B models are noticeably slower and need about 5 GB of memory. Good enough to try ANUM's agent flow, not to serve many people. This was not measured on an A1 from the build environment: time a few tasks yourself. In `.env`:

```bash
COMPOSE_PROFILES=ai
ANUM_MODEL_PROVIDER=ollama
ANUM_MODEL_NAME=qwen2.5:3b
ANUM_MODEL_BASE_URL=http://ollama:11434/v1
ANUM_MODEL_ALLOWED_HOSTS=ollama:11434
ANUM_MODEL_TIMEOUT_SECONDS=300
OLLAMA_MODEL=qwen2.5:3b
```

then `cd ~/anum/infra/oracle && docker compose up -d`. `ollama-pull` downloads the model and `nomic-embed-text` (retrieval embeddings with Ollama). `ANUM_MODEL_ALLOWED_HOSTS` is the operator allow-list that lets the API's SSRF guard call this private host ([Model gateway](model-gateway.md#outbound-guard-ssrf)); every workspace owner can then choose it too. Ollama has no authentication; it is reachable only on the compose networks.

**A hosted OpenAI-compatible API.** Fast, but prompts leave the VM. Some providers offer free tiers with OpenAI-compatible endpoints (at the time of writing, for example, Google's Gemini API, Groq, and OpenRouter's free models); their rate limits, model lists and data-use terms change, and free tiers may use your prompts for training: read them before you send tenant data. In `.env`: `ANUM_MODEL_PROVIDER=openai-compatible`, `ANUM_MODEL_BASE_URL=https://<provider>/.../v1`, `ANUM_MODEL_NAME=<model>`, `ANUM_MODEL_API_KEY=<key>`, then `docker compose up -d api`. Set monthly budgets through the owner screens or `/api/v1/model-budgets` ([Model gateway](model-gateway.md#monthly-budgets)).

**Claude through the native Anthropic API.** Prompts leave the VM; repeated prompt prefixes are cached and billed at the cache rate. In `.env`: `ANUM_MODEL_PROVIDER=anthropic` and `ANUM_MODEL_API_KEY=<key>`, keeping `ANUM_MODEL_NAME` and `ANUM_MODEL_BASE_URL` at the example's defaults (the provider then uses `https://api.anthropic.com`, `claude-haiku-5-5` for chat and voice replies and `claude-sonnet-5-5` for planning), then `docker compose up -d api`. `ANUM_MODEL_FAST_NAME`, `ANUM_MODEL_CAPABLE_NAME`, `ANUM_MODEL_FAST_EFFORT` and `ANUM_MODEL_CAPABLE_EFFORT` change the routing ([Model routing](model-gateway.md#model-routing)).

## Check the Deployment

In PowerShell on the PC (replace the host names). `-UseBasicParsing` keeps Windows PowerShell 5.1 from needing the Internet Explorer engine:

```powershell
$Api = "https://api.staging.example.com"
Invoke-RestMethod "$Api/health"                                   # status ok, environment staging
(Invoke-WebRequest -UseBasicParsing "$Api/health").Headers["Strict-Transport-Security"]
try { Invoke-WebRequest -UseBasicParsing "$Api/api/v1/tasks" } catch { $_.Exception.Response.StatusCode }   # Unauthorized (401)
try { Invoke-WebRequest -UseBasicParsing "$Api/docs" } catch { $_.Exception.Response.StatusCode }           # NotFound (404)
Invoke-RestMethod "https://auth.staging.example.com/realms/anum/.well-known/openid-configuration" | Select-Object issuer
(Invoke-WebRequest -UseBasicParsing "https://app.staging.example.com/").Headers["Content-Security-Policy"]
Invoke-WebRequest -UseBasicParsing "http://app.staging.example.com/" -MaximumRedirection 0 -ErrorAction SilentlyContinue | Select-Object StatusCode   # 308 to https
```

The issuer must be `https://<AUTH>/realms/anum`, and the CSP's `connect-src` must list `https://<API>` and `https://<AUTH>`. Then the staging smoke test of the [release gate](../.claude/skills/anum-release-gate/SKILL.md): sign in through Keycloak, create a task, approve a tool call, write and read a memory, upload and download a file (proves the bucket), and watch the live event stream.

**Client IPs for rate limiting.** Caddy replaces any `X-Forwarded-For` a client sends with the address it sees, and the API trusts forwarded headers only from Caddy's fixed address (`FORWARDED_ALLOW_IPS=172.30.10.10`; Keycloak likewise with `KC_PROXY_TRUSTED_ADDRESSES`). Check once: send a forged header and look at the API's access log on the VM:

```powershell
Invoke-WebRequest -UseBasicParsing "$Api/health" -Headers @{"X-Forwarded-For"="203.0.113.9"} | Out-Null
```

```bash
cd ~/anum/infra/oracle && docker compose logs --tail 5 api
```

The line for that request must show your public IP, not `203.0.113.9` and not a `172.30.x.x` address. (Docker forwards the published ports with NAT, so Caddy sees the real client address; this was checked locally through Caddy on the compose network, not on an Oracle VM.)

On the VM: `docker compose ps` (every service `healthy`, the one-shots `exited (0)`), `systemctl list-timers 'anum-*'`.

## Backups

Nightly (`anum-backup.timer` → `infra/oracle/backup.sh backup`):

1. `anum_backup.py backup` as `anum_backup` (`BYPASSRLS` and `pg_read_all_data`, a login nothing else uses): a `pg_dump` custom-format file of the whole `anum` database plus a manifest (row counts per table and per tenant, RLS flags, policies, Alembic revision, SHA-256) from one snapshot ([Runbooks: Backup and restore](runbooks.md#backup-and-restore)).
2. `pg_dump` of the `keycloak` database (users, MFA settings, realm changes made in the console).
3. Local copies older than `ANUM_BACKUP_LOCAL_KEEP_DAYS` (7) are deleted from `/var/lib/anum/backups`.
4. `rclone copy` to the backup bucket under `anum-backups/`, encrypted on the VM with `ANUM_BACKUP_ENCRYPTION_PASSPHRASE` (rclone `crypt`; file names stay readable, contents do not) and stored with B2's server-side encryption as well. The bucket's lifecycle rule expires copies after 35 days.

Files are `0600` in `0700` directories owned by uid 10001. Workspace files are not in the dump: bucket versioning covers them.

Commands (on the VM, from `~/anum/infra/oracle`):

| Command | What it does |
|---|---|
| `sudo ./backup.sh backup` | Run a backup now. Do this once after install and check the output. |
| `sudo ./backup.sh list` | List the off-site copies. |
| `sudo ./backup.sh drill` | **Restore drill**: backup → restore into a scratch `anum_restore_drill_*` database → verify revision, policies, row counts per tenant and that RLS still isolates tenants → drop. Needs at least one tenant with data (after onboarding). Run it monthly; keep the JSON report. |
| `sudo ./backup.sh fetch anum-20261011` | Download off-site files whose names start with that prefix into `/var/lib/anum/backups/fetched` (decrypted). |
| `sudo ./backup.sh restore <dump>` | Restore a dump (path under the backup directory, its `.manifest.json` next to it) into a new `anum_restore_<UTC>` database and verify it. Never touches the live database. |
| `sudo ./backup.sh promote anum_restore_<UTC>` | Make a verified restore the live database (asks you to type its name): stops the API, renames `anum` to `anum_replaced_<UTC>` and the restore to `anum`, re-runs `db-init`, starts everything. Data written after that backup is lost (it stays in `anum_replaced_*`). |
| `sudo ./backup.sh restore-keycloak <dump>` | Replace Keycloak's database with a dump (asks first; the old one is kept as `keycloak_replaced_<UTC>`). |
| `sudo ./backup.sh drop-database <name>` | Drop a scratch or replaced database once you are sure. |

**Losing the whole VM** (terminated, disk lost, account problem): create a new VM as above, run `install.sh`, then put the old values into the new `.env` from your password manager **before** the first start (at least `ANUM_SECRETS_KEY`, `ANUM_BACKUP_ENCRYPTION_PASSPHRASE` and the backup bucket keys), `sudo ./backup.sh fetch <date>`, `restore`, `promote`, `restore-keycloak`. Practise this once on a second VM if you can; the A1 allowance fits two 2-OCPU VMs.

RPO is 24 hours (nightly dumps) and there is no point-in-time recovery on this path. For tighter objectives, use a managed database (Render or a cloud provider) instead.

## Updates

```bash
cd ~/anum && sudo infra/oracle/update.sh
```

`update.sh` runs `git pull --ff-only` (as the repository's owner), adds any new variables from `.env.example` to `.env` (generating new secrets, never touching existing values; it stops if a new owner value is needed), builds the images with fresh base images, starts PostgreSQL and Valkey, runs `db-init` and the migrations as one-shots (the running API keeps serving; a failed migration stops the update before anything is replaced), recreates what changed, waits for the API to be healthy, and prunes dangling images and old build cache. Migrations are expand/contract ([Migrations](deployment.md#migrations)), so going back means checking out the previous commit and running `update.sh --no-pull`; a rollback never reverses migrations.

`ANUM_AUTO_UPDATE=true` in `.env` (then `install.sh` again) runs it daily at 04:05 UTC from `anum-update.timer`: convenient for staging; for production update by hand after staging has run the same commit. Check the last run with `journalctl -u anum-update --since today`.

## Monitoring Basics

- `docker compose ps` and `docker compose logs --tail 100 <service>` (logs are rotated: 5 files of 10 MB per container).
- `systemctl list-timers 'anum-*'` and `systemctl --failed`: a failed backup or retention run shows up there; `journalctl -u anum-backup -n 100` for the details.
- `df -h /` and `free -h`: the disk holds images, the database and 7 days of dumps.
- An external uptime check on `https://<API>/health` and `https://<WEB>/healthz` with e-mail alerts from any free uptime-monitoring service; this is the only alert you get when the whole VM is down.
- Oracle Console → the instance → **Metrics** (CPU, memory, network); **Monitoring → Alarms** can e-mail you on high CPU or a stopped instance.
- Certificates renew automatically; Let's Encrypt e-mails `ACME_EMAIL` if renewal keeps failing (usually DNS or the security list).

## Changing an Address Later

An address is in several places. Change the host in `.env`, then:

| Changed | Also do |
|---|---|
| `ANUM_WEB_HOST` | Keycloak admin console → realm `anum` → client `anum-web` → Valid redirect URIs, Valid post logout redirect URIs, Web origins (the realm was imported once). |
| `ANUM_API_HOST` | Nothing else (the web bundle is rebuilt). |
| `ANUM_AUTH_HOST` | Desktop and Flutter release builds (`ANUM_PRODUCTION_OIDC_ISSUER`). Existing sessions end: the token issuer changes. |

Then `sudo infra/oracle/update.sh --no-pull` (rebuilds the web bundle, recreates the services, Caddy requests the new certificate).

## Production on One VM

Production is a second VM (a second Oracle account's allowance, or a paid shape) with its own `.env`, its own two buckets and keys, its own Keycloak and DNS names, and `ANUM_ENVIRONMENT=production`. Additionally: a model provider other than `mock` and `ANUM_OBJECT_STORAGE_BACKEND=s3` (hard blockers of the release gate; `test_oracle_compose.py` checks that the compose file passes the startup policy in production with Ollama); `ANUM_AUTO_UPDATE=false`; the restore drill run and recorded; `ANUM_KEYCLOAK_ADMIN_ALLOWED_IPS` set; SSH limited to your IP. Decide whether the single-machine trade-offs in [What you give up](#what-you-give-up-compared-with-kubernetes-and-render) are acceptable for production data.

## How This Path Is Checked

- `services/api/tests/test_oracle_compose.py` (API unit tests): no literal secret in `compose.yaml` or `.env.example`; only Caddy publishes ports; PostgreSQL and Valkey on the internal network only; the migration password only in `db-init` and `migrate`; the API and retention job as `anum_app`; Keycloak's admin and database credentials only in Keycloak; restart policies, health checks and log rotation; forwarded headers trusted only from Caddy's address and the Caddyfile replacing client headers; the role SQL (only `anum_backup` bypasses RLS); `configure_env.py` generating private, valid, stable secrets; and the API's startup policy accepting the example values (staging, and production with Ollama).
- CI's "Docker Compose config" job runs `docker compose config` on the file with `.env.example` (also with the `ai` and `jobs` profiles) and ShellCheck on the scripts.
- Verified locally on x86_64 (October 2026) with generated secrets: `db-init`, the migrations to `0016_retrieval_hnsw` as `anum_migrator`, the API healthy as `anum_app` (`/health` reports `staging` with HSTS, 401 without a token, no `/docs`, no migration or Keycloak variables in its environment), the containers coming back after a Docker daemon restart, `keycloak` and `anum_app` refused `CONNECT` to each other's databases, the retention job, a second `db-init` run (idempotent), the nightly backup and Keycloak dump, a restore plus verification and the restore drill (with two seeded tenants), `caddy validate`, and through Caddy (with its local CA instead of Let's Encrypt): a forged `X-Forwarded-For` ignored and the real client address in the API log, and `/admin` refused from an address outside the allow-list.
- Not verified from the build environment: an Oracle VM itself (arm64 builds, Oracle's iptables rules, the security list, Let's Encrypt issuance, DuckDNS), `install.sh` end to end, the Keycloak and web images (Keycloak's registry `quay.io` was not reachable there), the off-site upload to a real bucket, and Ollama's speed on an A1.
