# Deploying on Render

This is the deployment path for an owner with a Windows PC and no Kubernetes. [Render](https://render.com) builds the images from this repository and runs them; the [Kubernetes path](deployment.md) (Helm chart, signed images, admission policy) stays in the repository as the alternative and is still the stricter of the two. Everything here is driven by `render.yaml` at the repository root (a Render *Blueprint*), the Render Dashboard in a browser, and Windows PowerShell.

The first environment is **staging** (`ANUM_ENVIRONMENT=staging`). A production environment repeats the same steps with its own Blueprint copy, secrets and database ([Production on Render](#production-on-render)).

## What Gets Created

| Resource (name in `render.yaml`) | Render type and plan | What it runs |
|---|---|---|
| `anum-api` | Web service, Docker, `starter` (0.5 CPU, 512 MB), 1 instance | `services/api/Dockerfile`; pre-deploy `python -m alembic upgrade head` as `anum_migrator`; then `uvicorn` as `anum_app`. Health check `/health`. |
| `anum-web` | Web service, Docker, `starter` | `apps/web/Dockerfile` (Vite bundle on unprivileged nginx, the same image and CSP as on Kubernetes). Health check `/healthz`. |
| `anum-keycloak` | Web service, Docker, `standard` (1 CPU, 2 GB; Keycloak does not fit in 512 MB) | `infra/render/keycloak.Dockerfile`: Keycloak 26.4 with the `anum` realm imported on first start, without the development user. Health check `/realms/anum/.well-known/openid-configuration`. |
| `anum-voice-retention` | Cron job, Docker, `starter`, daily at 03:17 UTC | The API image running `python -m anum_api.voice_retention` (voice transcripts past retention and retrieval index rows of expired memories). |
| `anum-kv` | Key Value (Valkey 8), `free`, private network only | Shared rate limits and run locks. Free Key Value keeps nothing on disk; ANUM only stores short-lived counters and locks there. |
| `anum-db` | Postgres 17, `basic-256mb`, private network only | ANUM's data, with the roles from `infra/helm/bootstrap-database.sql`. |
| `anum-keycloak-db` | Postgres 17, `basic-256mb`, private network only | Keycloak's own data, separate from ANUM's. |

Not deployed on Render, and what that means:

- **No Temporal worker.** `ANUM_RUNTIME_BACKEND=inline`: an agent run plans and executes inside the API request, as in local development. Runs work, approvals pause and resume them, but a run is not durable: if the API restarts mid-run (deploy, crash), that run stays where it stopped until someone calls `POST /api/v1/agent-runs/{id}/resume` or reruns the task. The API's Temporal client takes only a host and namespace (no TLS or API key), so a hosted Temporal Cloud namespace cannot be used without code changes ([Agent runtime](agent-runtime.md#durable-execution)).
- **No NATS.** `ANUM_EVENT_BUS=memory`: events are still written to PostgreSQL and the live SSE stream reads them from there, so the clients see every event. Nothing outside the API consumes events, and outbox rows stay unpublished; if NATS is added later, switching to `ANUM_EVENT_BUS=nats` publishes that backlog ([Events](events.md)).
- **No backup CronJob and no `anum_backup` login.** Render's own Postgres backups apply ([Backups](#backups)).
- **No OpenTelemetry collector.** Set `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` to a hosted OTLP/HTTP endpoint if you have one; otherwise logs are in the Render Dashboard only ([Observability](observability.md)).

Render's prices change; check [render.com/pricing](https://render.com/pricing) before applying. Every compute service above is on a paid instance type because pre-deploy commands, private-network traffic and no spin-down need one (free web services sleep and cannot receive private traffic). The two `basic-256mb` databases are the smallest paid Postgres; free Render Postgres is not used because it expires and has no backups.

## What You Give Up Compared With Kubernetes

Be clear about these before choosing Render for anything beyond staging:

| Kubernetes path | On Render |
|---|---|
| Images are built once in CI, scanned by Trivy, signed keyless with cosign, SBOM-attested, verified before `helm upgrade`, and deployed by digest; a Sigstore admission policy refuses any unsigned image in the namespace ([Image supply chain](deployment.md#image-supply-chain), [Admission policy](deployment.md#admission-policy)). | Render builds its own image from the GitHub commit with the same Dockerfiles. CI still builds and Trivy-scans those Dockerfiles on every PR, but the image that runs is a separate build that nothing signs or verifies, and there is no admission control. You trust Render's builder and your Render account. |
| Default-deny NetworkPolicies: each pod reaches only the ports it needs. | No network policy. Every service in the workspace and region shares one private network, and outbound traffic is unrestricted. Databases and Key Value are private (`ipAllowList: []`) and Postgres needs a password; the model egress guard in the API still applies ([Security](security.md#model-endpoints-and-budgets)). |
| The migration Job has its own Secret; the API pods never receive the schema-owner login. | `ANUM_MIGRATION_DATABASE_URL` is an environment variable of `anum-api`, because Render runs the pre-deploy command with the service's environment. The API's start command removes it before `uvicorn` starts, so the running API process never holds it, but anyone who can open the service in the Dashboard (or its Shell) can read it. Keep the Render workspace to people who may hold the schema owner. |
| Read-only root filesystem, all capabilities dropped, seccomp `RuntimeDefault`, no ServiceAccount token. | Not configurable. The containers still run as the non-root users of the Dockerfiles (API uid 10001, nginx uid 101, Keycloak uid 1000). |
| HPA, PodDisruptionBudgets, several replicas, zone spread. | One instance per service. Render deploys without downtime (new instance healthy before traffic moves) as long as no persistent disk is attached. More instances are possible on paid plans; the API is ready for them (Valkey rate limits and locks, S3 files). |
| `helm test`, the kind CI job and `--rollback-on-failure`. | A failed pre-deploy command or health check cancels the deploy and the previous version keeps serving; rollback is a button in the Dashboard. `services/api/tests/test_render_blueprint.py` checks `render.yaml` in CI; the [smoke checks](#check-the-deployment) are manual. |
| Production deploys need a GitHub `production` environment approval. | `autoDeployTrigger: checksPass` deploys staging once every GitHub check is green. For production use manual deploys ([Production on Render](#production-on-render)). |
| Keycloak is run by you behind your own ingress. | Keycloak's admin console (`/admin`) is on the public internet. Use a named admin with MFA and delete the bootstrap admin on day one ([Keycloak](#keycloak)). |

What stays the same: OIDC with Keycloak, the API's startup refusals, PostgreSQL row-level security with the non-owner `anum_app` login and the narrow NOLOGIN roles, Fernet-encrypted provider keys, the web CSP and security headers, the SSRF guard, budgets and approvals.

## Before You Start

On the Windows PC:

1. **Git for Windows** ([git-scm.com](https://git-scm.com/download/win)) and a clone of this repository, for example in `C:\src\Anum_Mine`. Render deploys from GitHub, so the repository must be on GitHub and you must be able to grant Render access to it.
2. **PowerShell.** Windows PowerShell 5.1 (built in) works for every command here; PowerShell 7 works too.
3. **psql**, the PostgreSQL command-line client, for the one-time database bootstrap. Download the PostgreSQL 17 installer for Windows from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/) (EDB installer) and select only **Command Line Tools**. psql is then at `C:\Program Files\PostgreSQL\17\bin\psql.exe`.
4. Optional: **Python 3.13** from [python.org](https://www.python.org/downloads/windows/) ("Add python.exe to PATH"), only if you prefer to generate the Fernet key with Python.

Accounts:

- A **Render** account with a payment method, connected to GitHub (Dashboard → Account settings → Git providers).
- An **S3-compatible object storage** account ([File storage](#file-storage)).
- Optional: a **domain** whose DNS you control ([Custom domains](#custom-domains)). Strongly recommended, see the next section.

## Decide the Three Public Addresses First

The web bundle, the API's CORS list, Keycloak's issuer and the realm's redirect URIs all contain the public addresses, and some of them are fixed at the first build or the first Keycloak start. Decide them before applying the Blueprint:

| Address | With your own domain (recommended) | Without a domain |
|---|---|---|
| Web | `https://app.staging.example.com` | `https://anum-web.onrender.com` |
| API | `https://api.staging.example.com` | `https://anum-api.onrender.com` |
| Keycloak | `https://auth.staging.example.com` | `https://anum-keycloak.onrender.com` |

Render names a service `https://<service name>.onrender.com`, but adds a random suffix when that name is already taken by someone else, so the `onrender.com` addresses are only known after creation. With your own domain the addresses are known in advance. If you go without a domain, enter the `onrender.com` guesses below and, if Render picked a different name, correct the values afterwards ([Changing an address later](#changing-an-address-later)).

The rest of this guide writes `<WEB>`, `<API>` and `<AUTH>` for these three `https://` origins (no trailing slash).

## Generate the Secrets in PowerShell

Open PowerShell and paste this function. It reads 32 bytes from the operating system's cryptographic random generator and prints them as URL-safe base64 (`+` becomes `-`, `/` becomes `_`), which is the format a Fernet key needs and is safe inside a database URL:

```powershell
function New-UrlSafeSecret {
    param([int]$Bytes = 32, [switch]$KeepPadding)
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buffer)
    $rng.Dispose()
    $text = [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
    if ($KeepPadding) { $text } else { $text.TrimEnd('=') }
}

$FernetKey        = New-UrlSafeSecret -KeepPadding   # ANUM_SECRETS_KEY: 44 characters, ends with '='
$MigratorPassword = New-UrlSafeSecret                # anum_migrator (schema owner, migrations only)
$AppPassword      = New-UrlSafeSecret                # anum_app (the API and the cron job)
$RelayPassword    = New-UrlSafeSecret                # anum_relay (unused until NATS is added)

$FernetKey.Length   # must print 44
```

Alternative for the Fernet key with Python: `py -m pip install cryptography`, then `py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

Copy each value into your password manager now, labelled (for example "ANUM staging: ANUM_SECRETS_KEY"). Do not save them in a file inside the repository, in a `.env` file, or in a chat. Keep this PowerShell window open: the bootstrap below uses the variables. The Fernet key is never generated by Render (`generateValue`), because Render only promises a random base64 value, not the URL-safe base64 Fernet requires.

## File Storage

Render has no object storage. ANUM keeps file bytes in any S3-compatible bucket (`ANUM_OBJECT_STORAGE_BACKEND=s3`, [Workspace files](files.md)), and the bucket must have **versioning** so a deleted or overwritten object can be recovered ([Production readiness gates](production-readiness.md)). Choose one:

**Backblaze B2 (simplest).** B2's S3-compatible API supports server-side encryption with Backblaze-managed keys, reported as `AES256`, which is what `ANUM_S3_SERVER_SIDE_ENCRYPTION=AES256` sends ([Backblaze S3-compatible API](https://www.backblaze.com/docs/cloud-storage-s3-compatible-api), [server-side encryption](https://www.backblaze.com/docs/cloud-storage-server-side-encryption)). B2 keeps every version of a file by default.

1. Backblaze web console → **Buckets** → **Create a Bucket**: name `anum-staging-files` (bucket names are global; add a suffix if taken), **Private**, **Default Encryption: Enable**.
2. On the bucket, **Lifecycle Settings**: keep "Keep all versions of the file" (versioning). Later you can expire old versions after a retention period you choose.
3. **Application Keys** → **Add a New Application Key**: access to this bucket only, **Read and Write**. Note the `keyID` and `applicationKey` (shown once).
4. The bucket page shows the **Endpoint**, for example `s3.us-west-004.backblazeb2.com`. Then `ANUM_S3_ENDPOINT=https://s3.us-west-004.backblazeb2.com` and `ANUM_S3_REGION=us-west-004`.

**AWS S3.** Create a bucket with Block Public Access on, **Bucket Versioning: Enable**, default encryption SSE-S3. Create an IAM user (or access key for a role) whose policy allows only `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` and `s3:ListBucket` on that bucket. `ANUM_S3_ENDPOINT=https://s3.<region>.amazonaws.com`, `ANUM_S3_REGION=<region>`. Add a lifecycle rule that expires noncurrent versions after your retention period.

**Cloudflare R2.** Not recommended for ANUM: when this was written R2's S3 compatibility did not include bucket versioning (this could not be re-checked from the build environment; see [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)). Use it only if that page lists `PutBucketVersioning` as implemented.

**A Render disk instead (staging only).** The API also supports `ANUM_OBJECT_STORAGE_BACKEND=local` on one instance: add a `disk` (for example `mountPath: /var/data`) to `anum-api` and set `ANUM_OBJECT_STORAGE_LOCAL_PATH=/var/data/objects`. The costs: zero-downtime deploys are off while a disk is attached, the service cannot scale beyond one instance, the disk is not mounted during the pre-deploy step, there is no versioning (only Render's daily disk snapshots), and it was not verified that the disk is writable by the API's non-root uid. Production refuses it (`test_render_blueprint.py` and the [release gate](../.claude/skills/anum-release-gate/SKILL.md) require `s3`).

## Apply the Blueprint

1. Push the commit that contains `render.yaml` to the branch Render will deploy (normally `main`). Before the first apply, decide the region: every resource in `render.yaml` uses `region: frankfurt`; change all of them together (for example to `oregon`) if another region is closer to your users. A database's region, name, user and PostgreSQL version cannot change after creation.
2. Render Dashboard → **New** → **Blueprint** → choose the GitHub repository → Blueprint name `anum-staging` → branch `main`. Render reads `render.yaml` and lists the services and databases it will create.
3. Render asks for every variable marked `sync: false`. Fill them in as below. Database URLs are not known yet: enter the placeholder shown, the API's first deploy then fails at the pre-deploy step, which is expected and harmless.
4. Click **Apply**. Render creates both databases, the Key Value instance, and builds and deploys the services (the first Keycloak and web builds take several minutes).

Values to enter when Render asks (service → variable → value):

| Service | Variable | Value | Where it comes from |
|---|---|---|---|
| `anum-api` | `ANUM_KEYCLOAK_ISSUER` | `<AUTH>/realms/anum` | Your Keycloak address. Must be `https`; tokens must carry exactly this `iss`. |
| | `ANUM_CORS_ORIGINS` | `["<WEB>"]` (JSON list, with the quotes and brackets) | Your web address. |
| | `ANUM_DATABASE_URL` | `postgresql+psycopg://pending@localhost/pending` for now | Replaced after the [bootstrap](#database-bootstrap). |
| | `ANUM_MIGRATION_DATABASE_URL` | `postgresql+psycopg://pending@localhost/pending` for now | Replaced after the bootstrap. |
| | `ANUM_SECRETS_KEY` | `$FernetKey` (paste the 44-character value) | [Generated above](#generate-the-secrets-in-powershell). |
| | `ANUM_S3_ENDPOINT` | `https://s3.<region>.backblazeb2.com` or `https://s3.<region>.amazonaws.com` | Your bucket's endpoint. |
| | `ANUM_S3_REGION` | `us-west-004`, `eu-central-1`, ... | Your bucket's region. |
| | `ANUM_S3_BUCKET` | `anum-staging-files` | Your bucket name. |
| | `ANUM_S3_ACCESS_KEY` | B2 `keyID` or AWS access key id | The bucket-scoped key. |
| | `ANUM_S3_SECRET_KEY` | B2 `applicationKey` or AWS secret access key | The bucket-scoped key. |
| `anum-web` | `VITE_ANUM_API_URL` | `<API>` | Compiled into the bundle (not secret). |
| | `VITE_ANUM_OIDC_ISSUER` | `<AUTH>/realms/anum` | Same as `ANUM_KEYCLOAK_ISSUER`. |
| | `ANUM_CSP_CONNECT_SRC` | `<API> <AUTH>` (two origins separated by one space) | Origins the page may call; substituted into the CSP at container start. |
| `anum-keycloak` | `KC_HOSTNAME` | `<AUTH>` | Keycloak builds the issuer from it. |
| | `ANUM_WEB_ORIGIN` | `<WEB>` | Fills the web client's redirect URIs in the realm on first start. |

Set by `render.yaml` itself (no input): `ANUM_ENVIRONMENT=staging`, `ANUM_AUTH_MODE=oidc`, `ANUM_OIDC_AUDIENCE=anum-api`, `ANUM_REPOSITORY_BACKEND=postgresql`, `ANUM_EVENT_BUS=memory`, `ANUM_RUNTIME_BACKEND=inline`, `ANUM_RUN_LOCK_BACKEND=valkey`, `ANUM_RATE_LIMIT_BACKEND=valkey`, `ANUM_RATE_LIMIT_ENABLED=true`, `ANUM_OBJECT_STORAGE_BACKEND=s3`, `ANUM_S3_SERVER_SIDE_ENCRYPTION=AES256`, `ANUM_S3_CREATE_BUCKET=false`, `ANUM_MODEL_PROVIDER=mock`, `ANUM_MODEL_NAME`, `ANUM_MODEL_BASE_URL`, `ANUM_AUTOMATION_SCHEDULER_ENABLED=true`, `PORT`, `FORWARDED_ALLOW_IPS`, `VITE_ANUM_OIDC_CLIENT_ID=anum-web`. Wired by Render: `ANUM_VALKEY_URL` (from `anum-kv`), Keycloak's `KC_DB_URL_HOST`, `KC_DB_URL_PORT`, `KC_DB_URL_DATABASE`, `KC_DB_USERNAME`, `KC_DB_PASSWORD` (from `anum-keycloak-db`), and the cron job's `ANUM_DATABASE_URL` and `ANUM_SECRETS_KEY` (copied from `anum-api`). Generated by Render: `KC_BOOTSTRAP_ADMIN_PASSWORD`.

Render asks for `sync: false` values only when the Blueprint is first applied. Later changes are made in each service's **Environment** page in the Dashboard; a value added to `render.yaml` later with `sync: false` is not asked for.

## Database Bootstrap

ANUM's row-level security needs two separate logins: `anum_migrator` owns the schema and runs migrations, `anum_app` runs the API and is subject to RLS, plus the NOLOGIN roles the migrations grant narrow access to ([Deployment: database roles](deployment.md#database-roles), [Multi-tenancy](multi-tenancy.md)). Render creates one database user per database (here `anum_admin`), which is not a superuser. `infra/helm/bootstrap-database.sql` works with such an admin: it creates the roles, grants the admin membership in `anum_migrator` (PostgreSQL 16+ gives a role's creator the right to grant it), hands the database and the `public` schema to `anum_migrator`, and creates the `vector` extension (Render supports pgvector on PostgreSQL 13 and later: [Render Postgres extensions](https://render.com/docs/postgresql-extensions)). No login gets `BYPASSRLS` or superuser, nothing is `SECURITY DEFINER`, and the maintenance and membership-reader roles are granted `WITH INHERIT FALSE, SET TRUE`. The `anum_admin` login is used only for this step; no service receives it.

1. **Open external access for your IP, temporarily.** Dashboard → `anum-db` → **Info** (or **Networking**) → **Access Control** → **Add source** → your current public IP (the Dashboard offers to fill it in) as `/32`. `render.yaml` keeps `ipAllowList: []`, so nothing else can connect from outside Render.
2. **Copy the External Database URL** from `anum-db` → **Connect** → **External**. It looks like `postgresql://anum_admin:...@dpg-xxxx-a.frankfurt-postgres.render.com/anum`. Treat it as a secret.
3. **Run the bootstrap** from the repository folder, in the PowerShell window that still holds the passwords:

   ```powershell
   cd C:\src\Anum_Mine
   $psql = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
   $env:PGSSLMODE = "require"            # Render requires TLS for external connections
   $AdminUrl = Read-Host "Paste the External Database URL of anum-db"
   & $psql $AdminUrl -v ON_ERROR_STOP=1 `
       -v "migrator_password=$MigratorPassword" `
       -v "app_password=$AppPassword" `
       -v "relay_password=$RelayPassword" `
       -f infra\helm\bootstrap-database.sql
   ```

   The script is idempotent: running it again changes nothing and keeps the passwords (rotate one with `ALTER ROLE anum_app PASSWORD '...'` as `anum_admin`).
4. **Check the result:**

   ```powershell
   & $psql $AdminUrl -c "\du anum_*"
   & $psql $AdminUrl -c "select datname, pg_get_userbyid(datdba) from pg_database where datname = current_database()"
   ```

   Expect logins `anum_migrator`, `anum_app`, `anum_relay` without `Superuser`, `Bypass RLS` or `Create role`; NOLOGIN roles `anum_maintenance`, `anum_membership_reader`, `anum_outbox_relay`; and the database owned by `anum_migrator`.
5. **Close external access again:** delete the IP you added in step 1. Then `Remove-Variable AdminUrl`.

### Set the database URLs and deploy

Dashboard → `anum-db` → **Connect** → **Internal** shows `postgresql://anum_admin:...@dpg-xxxx-a/anum`. Only the host (`dpg-xxxx-a`) and the database name (`anum`) are reused. Build the two URLs in PowerShell:

```powershell
$DbHost = "dpg-xxxx-a"   # the host part of the Internal Database URL
"postgresql+psycopg://anum_app:$AppPassword@${DbHost}:5432/anum"
"postgresql+psycopg://anum_migrator:$MigratorPassword@${DbHost}:5432/anum"
```

The scheme must be `postgresql+psycopg://` (the API's driver); a plain `postgresql://` URL fails. Then Dashboard → `anum-api` → **Environment** → edit `ANUM_DATABASE_URL` (the `anum_app` URL) and `ANUM_MIGRATION_DATABASE_URL` (the `anum_migrator` URL) → **Save, rebuild, and deploy**. The cron job copies `ANUM_DATABASE_URL` from the API, so it needs no change.

Watch `anum-api` → **Events** / **Logs**: the pre-deploy step prints `Running upgrade ... -> 0016_retrieval_hnsw` (or the newest revision), then the new instance passes `/health` and takes traffic. If the pre-deploy step fails, the deploy is cancelled and the message names the problem (a missing `ANUM_MIGRATION_DATABASE_URL` stops it before Alembic runs).

Copy the passwords into your password manager if you have not, then close the PowerShell window.

## Keycloak

Keycloak imports the `anum` realm on its first start from `infra/keycloak/anum-realm.json`, transformed at image build by `infra/render/keycloak_realm.py`: the development user `dev` is removed, the `anum-web` client's redirect URIs, web origins and post-logout redirects become `<WEB>` (from `ANUM_WEB_ORIGIN`), and the desktop client loses the Vite dev-server URLs (it keeps the Tauri origins and the `http://127.0.0.1` loopback redirect). Clients, roles, claim mappers and the user profile are unchanged ([Identity and sign-in](identity.md)). The import is skipped once the realm exists, so later changes to the realm file are made in the admin console.

Day one, before anyone else uses it:

1. Open `<AUTH>/admin/`. User `anum-bootstrap-admin`; the password is `anum-keycloak` → **Environment** → `KC_BOOTSTRAP_ADMIN_PASSWORD` (generated by Render; click to reveal).
2. Realm **master** → **Users** → **Add user** with your own name and e-mail → **Credentials**: set a long password (not temporary) → **Role mapping** → **Assign role** → realm role `admin` → **Required user actions**: `Configure OTP`.
3. Sign out, sign in as the new user, set up OTP, then **Users** → `anum-bootstrap-admin` → **Delete**.
4. Render → `anum-keycloak` → **Environment**: delete `KC_BOOTSTRAP_ADMIN_USERNAME` and `KC_BOOTSTRAP_ADMIN_PASSWORD` → **Save and deploy**. Keycloak creates the bootstrap admin only on its first start against an empty database, so these variables do nothing afterwards; if a later Blueprint sync adds them back, delete them again.
5. Realm **anum** → **Realm settings**: confirm "User registration" is off (it is in the realm file) and set the e-mail settings if you want password-reset mail.

Create ANUM users (realm **anum** → **Users** → **Add user**):

- Username and e-mail; **Attributes** (admin-only in the user profile): `tenant_id` (for example `tenant_acme`, letters, digits, `_` or `-`, 3 to 80 characters) and optionally `default_workspace_id` (for example `workspace_main`).
- **Role mapping**: realm role `owner` for the first person of a tenant (it lets them create the tenant and workspace through onboarding); everyone else joins through an invitation from inside ANUM.
- **Credentials**: a temporary password, sent to the person out of band.

Check the realm after the first start: realm **anum** → **Clients** → `anum-web` → **Valid redirect URIs** must show `<WEB>/*`. If it still shows `${ANUM_WEB_ORIGIN}/*`, the variable was empty at the first start: correct it there by hand.

## Custom Domains

For each of `anum-web`, `anum-api` and `anum-keycloak`: Dashboard → service → **Settings** → **Custom Domains** → **Add Custom Domain** → for example `app.staging.example.com`. At your DNS provider, add a `CNAME` record from that name to the service's `onrender.com` host shown by Render. Render verifies the record and issues and renews the TLS certificate. Apex domains (`example.com` without a subdomain) need an `ALIAS`/`ANAME` record or the address Render shows. Custom domains are not written into `render.yaml`, so the repository carries no environment's host names.

### Changing an address later

An address is in several places. After changing one, update all of these, then redeploy each touched service (**Save, rebuild, and deploy** for `anum-web`, because its values are compiled in):

| Changed | Update |
|---|---|
| Web `<WEB>` | `anum-api` `ANUM_CORS_ORIGINS`; `anum-keycloak` `ANUM_WEB_ORIGIN`; Keycloak admin console → realm `anum` → client `anum-web` → Valid redirect URIs, Valid post logout redirect URIs, Web origins. |
| API `<API>` | `anum-web` `VITE_ANUM_API_URL` and `ANUM_CSP_CONNECT_SRC`. |
| Keycloak `<AUTH>` | `anum-keycloak` `KC_HOSTNAME`; `anum-api` `ANUM_KEYCLOAK_ISSUER`; `anum-web` `VITE_ANUM_OIDC_ISSUER` and `ANUM_CSP_CONNECT_SRC`; desktop and Flutter release builds (`ANUM_PRODUCTION_OIDC_ISSUER`). Existing sessions end, because the token issuer changes. |

## AI Model

Render has no GPUs. Staging starts with `ANUM_MODEL_PROVIDER=mock` (placeholder answers, no key; refused in production by the release gate). For real answers, choose one and set the variables in `anum-api` → **Environment** (then **Save and deploy**):

**A hosted OpenAI-compatible provider** (OpenAI or any service with the same API):

| Variable | Value |
|---|---|
| `ANUM_MODEL_PROVIDER` | `openai-compatible` |
| `ANUM_MODEL_BASE_URL` | `https://api.openai.com/v1` or the provider's `https://.../v1` |
| `ANUM_MODEL_NAME` | for example `gpt-4.1-mini` |
| `ANUM_MODEL_API_KEY` | the provider key: **Add Environment Variable** in the Dashboard (it is not in `render.yaml`); never a value in git |

Set monthly budgets through the owner screens or `/api/v1/model-budgets` ([Model gateway](model-gateway.md#monthly-budgets)). Workspace owners can also save their own provider and key per workspace; those keys are encrypted with `ANUM_SECRETS_KEY`.

**An external Ollama.** Ollama on your Windows PC is not reachable from Render (no tunnel or VPN is set up here, and exposing a home PC is not recommended). Options that work:

- Ollama on a GPU server you rent elsewhere, behind an HTTPS reverse proxy on port 443 with a public certificate: `ANUM_MODEL_PROVIDER=ollama`, `ANUM_MODEL_BASE_URL=https://ollama.example.com/v1`, `ANUM_MODEL_NAME=llama3.2`. A public HTTPS host on 443 passes the outbound guard without an allow-list entry, but Ollama has no authentication of its own: put authentication or an IP allow-list for Render's outbound addresses in the proxy.
- Ollama inside Render as a private service (CPU only, slow; needs a large instance and a disk for the models; not in `render.yaml`): reachable only on the private network, for example `anum-ollama:11434`. Then `ANUM_MODEL_BASE_URL=http://anum-ollama:11434/v1` and `ANUM_MODEL_ALLOWED_HOSTS=anum-ollama:11434`, the operator allow-list for a private model host ([Model gateway](model-gateway.md#outbound-guard-ssrf)). Every workspace owner can then point a workspace at that host.

Never list a host in `ANUM_MODEL_ALLOWED_HOSTS` that you do not operate.

## Check the Deployment

In PowerShell (replace the addresses):

```powershell
$Api = "https://api.staging.example.com"
Invoke-RestMethod "$Api/health"                                   # status ok, environment staging
(Invoke-WebRequest "$Api/health").Headers["Strict-Transport-Security"]   # HSTS is sent
try { Invoke-WebRequest "$Api/api/v1/tasks" } catch { $_.Exception.Response.StatusCode }   # Unauthorized (401)
try { Invoke-WebRequest "$Api/docs" } catch { $_.Exception.Response.StatusCode }           # NotFound (404)
Invoke-RestMethod "https://auth.staging.example.com/realms/anum/.well-known/openid-configuration" | Select-Object issuer
(Invoke-WebRequest "https://app.staging.example.com/").Headers["Content-Security-Policy"]
```

The issuer must equal `ANUM_KEYCLOAK_ISSUER` exactly, and the web CSP's `connect-src` must list `<API>` and `<AUTH>`. Then the staging smoke test of the [release gate](../.claude/skills/anum-release-gate/SKILL.md): sign in through Keycloak in the browser, create a task, approve a tool call, write and read a memory, upload and download a file (proves the bucket), and watch the live event stream. Open the browser's developer tools on the web app once: requests must go to `<API>`, not `http://localhost:8000`. If they go to localhost, the build did not receive the `VITE_*` values as build arguments: check `anum-web` → **Environment**, then **Manual Deploy** → **Clear build cache & deploy**.

**Client IPs for rate limiting.** `FORWARDED_ALLOW_IPS` trusts `X-Forwarded-For` only from private addresses (Render's proxy reaches the container from inside its network). This was not verifiable from Render's documentation. After the first deploy, open `anum-api` → **Logs** and load the site from your PC: the access log lines must show your public IP, not a `10.x` address. If they show a private address, every client shares one rate-limit bucket; if a request with a forged header (`Invoke-WebRequest "$Api/health" -Headers @{"X-Forwarded-For"="203.0.113.9"}`) shows `203.0.113.9` instead of your IP, the setting is too trusting. Report either case before relying on per-client limits.

## Day-2 Operations

- **Deploys.** A push to `main` deploys `anum-api`, `anum-web`, `anum-keycloak` and the cron job once every GitHub check on that commit passes (`autoDeployTrigger: checksPass`), and only the services whose files changed (`buildFilter`). Migrations run first in the pre-deploy step. `deploy-staging.yml` keeps building and pushing images to GHCR; it does not deploy anywhere while `STAGING_DEPLOY_TARGET` is unset.
- **Migrations and rollback.** Dashboard → service → **Events** → a previous deploy → **Rollback**. A rollback never reverses migrations, so every migration must stay expand/contract, exactly as on Kubernetes ([Migrations](deployment.md#migrations)).
- **`render.yaml` changes.** Render re-syncs the Blueprint when the file changes on `main`. Values marked `sync: false` and generated values are not overwritten. `services/api/tests/test_render_blueprint.py` runs in the API unit tests and fails on a literal secret, a missing required setting, the migration URL outside the API, or a value the API refuses at startup.
- **Rotating secrets.** `ANUM_SECRETS_KEY`: put a new key first, comma-separated before the old one, deploy, re-encrypt (`python -m anum_api.rotate_secrets` from `anum-api` → **Shell**), then remove the old key ([Runbooks](runbooks.md#rotating-anum_secrets_key)). Database passwords: `ALTER ROLE ... PASSWORD` as `anum_admin` (open external access briefly as in the bootstrap), then update the URL in `anum-api`. S3 and model keys: create the new key at the provider, update the variable, deploy, delete the old key.
- **Backups.** Render backs up paid Postgres instances and offers point-in-time recovery on plans that include it (Dashboard → `anum-db`, the backup or recovery page); backups are deleted with the database, so export before deleting one. Run the restore drill against a copy: `pg_dump` with the external URL from Windows (`C:\Program Files\PostgreSQL\17\bin\pg_dump.exe -Fc -f anum.dump $AdminUrl`, as the admin, during a short external-access window) and `pg_restore` into a scratch database ([Runbooks](runbooks.md#backup-and-restore)). Bucket versioning covers file bytes.
- **Logs and metrics.** Each service's **Logs** and **Metrics** tabs in the Dashboard. For OpenTelemetry, set `ANUM_OTEL_EXPORTER_OTLP_ENDPOINT` (and the provider's headers through the standard `OTEL_EXPORTER_OTLP_HEADERS`, as a secret variable) on `anum-api`.

## Production on Render

Create production as a second, separate set of resources: copy `render.yaml` to a second Blueprint (rename every resource, for example `anum-prod-api`, and set `ANUM_ENVIRONMENT=production`), apply it as its own Blueprint with its own secrets, bucket, Keycloak and database, and repeat the bootstrap. Additionally:

- `ANUM_MODEL_PROVIDER` other than `mock` and `ANUM_OBJECT_STORAGE_BACKEND=s3` (hard blockers of the release gate). `test_render_blueprint.py` refuses both in `render.yaml` when its `ANUM_ENVIRONMENT` is `production`; point the test at the production Blueprint file too.
- `autoDeployTrigger: off` on production services, and deploy by hand (**Manual Deploy** → the commit staging already runs), so production never deploys a commit nobody chose.
- Larger Postgres and Keycloak plans with high availability where the plan offers it, more than one API instance, and Key Value on a paid plan.
- Decide whether the trade-offs in [What You Give Up](#what-you-give-up-compared-with-kubernetes) are acceptable for production data; the Kubernetes path closes them.

## Sources

Render's documentation site could not be fetched from the environment where this guide was written. The Render facts above come from Render's own agent skills repository, [github.com/render-oss/skills](https://github.com/render-oss/skills) (commit `3f2aa30`, August 2026: `render-blueprints` field reference, wiring patterns and common mistakes; `render-web-services` deploy lifecycle; `render-postgres`; `render-keyvalue`; `render-cron-jobs`; `render-docker`; `render-env-vars`; `render-disks`; `render-scaling` instance types; `render-deploy` Blueprint spec), and from search summaries of [Blueprint YAML reference](https://render.com/docs/blueprint-spec), [Monorepo support](https://render.com/docs/monorepo-support), [Render Postgres extensions](https://render.com/docs/postgresql-extensions) and the Blueprint JSON schema (`https://render.com/schema/render.yaml.json`). Check these pages before the first apply; in particular: that `fromService` with `envVarKey` copies a `sync: false` value, that a service's environment variables reach a Docker build as build arguments, the exact behaviour of `FORWARDED_ALLOW_IPS` behind Render's proxy, and current plan names and prices. What was verified locally: the realm transformation and its import into Keycloak 26.4 (no users, redirect URIs filled from `ANUM_WEB_ORIGIN`, issuer from `KC_HOSTNAME`), the bootstrap as a non-superuser `CREATEROLE` admin on PostgreSQL 16 followed by the migrations as `anum_migrator` with RLS intact for `anum_app`, the pre-deploy and start commands of `render.yaml` with `/bin/sh` (missing migration URL refused; the running API process without it; `/health`, HSTS, 401 without a token, no `/docs`), and the cron command as `anum_app`.
