# Deploying ANUM's production Keycloak on Fly.io

This is the runbook for standing up a **real, production** Keycloak identity
provider for ANUM on Fly.io, importing the production realm template into
it, and pointing the already-deployed API at it. It complements, and does
not duplicate, two documents you should already be familiar with:

- `infra/docker/keycloak/README.md` — what the realm shape *is* (roles,
  clients, claim mappers) and what manual edits the production template
  needs before it's importable. This runbook tells you *how to actually
  run* the production edits it describes, against a real server, over the
  Admin REST API.
- `docs/deployment.md` — the API's own production checklist and the full
  `ANUM_*` environment variable reference. This runbook only covers the
  Keycloak side and the handful of `ANUM_*` variables that point the API at
  it (section 5 below).

Nothing here modifies `infra/docker/keycloak/` — that directory stays as
the source of truth for the realm *shape*; this directory is purely about
*running an instance* and getting that shape into it.

## Contents

1. [Deploying Keycloak itself](#1-deploying-keycloak-itself)
2. [Importing the production realm](#2-importing-the-production-realm)
3. [Manual edits required before import](#3-manual-edits-required-before-import)
4. [Creating real users](#4-creating-real-users)
5. [Pointing the API at this Keycloak](#5-pointing-the-api-at-this-keycloak)
6. [What remains a human judgment call](#6-what-remains-a-human-judgment-call)

---

## 1. Deploying Keycloak itself

`keycloak.fly.toml` in this directory is the Fly app config: app name
`anum-keycloak`, region `iad` (matching `anum-api`), running the stock
`quay.io/keycloak/keycloak:25.0` image (the same tag pinned in
`infra/docker/compose.yaml`) in **production mode** — `start`, not
`start-dev`. `start-dev` is explicitly a development convenience (enables
things like HTTP without warnings, and — combined with `--import-realm` —
is what the local compose `keycloak` service uses); Keycloak's own docs
call it out as unsuitable for production. See the comments at the top of
`keycloak.fly.toml` for why this uses plain `start` rather than
`start --optimized` (short version: `--optimized` requires build-time
options like the DB vendor to have been baked into a custom image via
`kc.sh build` ahead of time, which we haven't done — there's no Dockerfile
for Keycloak in this repo, only the stock image pulled straight from
`quay.io`).

### 1a. Create the app

```bash
fly apps create anum-keycloak
```

### 1b. Provision Keycloak's own Postgres database

Keycloak needs a Postgres database for **its own internal state** —
realms, clients, users, sessions, offline tokens. This must be a
**separate** database from ANUM's own `anum` app database (the one
`ANUM_DATABASE_URL` points at) — they are different schemas owned by
different applications, and Keycloak's migrations should never run against
ANUM's tables or vice versa:

```bash
fly postgres create --name anum-keycloak-db --region iad \
  --vm-size shared-cpu-1x --volume-size 1 --initial-cluster-size 1
```

This prints a one-time connection string like
`postgres://anum_keycloak_db:PASSWORD@anum-keycloak-db.flycast:5432/anum_keycloak_db`
— **save it now**, Fly does not show the password again. Do **not** run
`fly postgres attach --app anum-keycloak`: that sets a generic
`DATABASE_URL` secret in `postgres://user:pass@host/db` form, which
Keycloak does not read. Keycloak wants the connection split across
`KC_DB_URL` (a JDBC URL, no credentials embedded), `KC_DB_USERNAME`, and
`KC_DB_PASSWORD` (see Keycloak 25's
["Configuring the database"](https://www.keycloak.org/server/db) guide for
the authoritative list of `KC_DB_*` variables). Translate the connection
string Fly printed into:

```bash
fly secrets set --app anum-keycloak \
  KC_DB_URL="jdbc:postgresql://anum-keycloak-db.flycast:5432/anum_keycloak_db" \
  KC_DB_USERNAME="anum_keycloak_db" \
  KC_DB_PASSWORD="<the password Fly printed>"
```

(`KC_DB=postgres`, the vendor selector, is already set as a non-secret in
`keycloak.fly.toml`'s `[env]` block — it's not a credential, so it doesn't
need to go through `fly secrets set`.)

### 1c. Set the admin bootstrap credentials

Keycloak creates its first `master`-realm admin user from the
`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` environment variables **only
on first boot against an empty database** — exactly like the
`KEYCLOAK_ADMIN`/`KEYCLOAK_ADMIN_PASSWORD` pair already used in
`infra/docker/compose.yaml`'s dev `keycloak` service, except here they must
be real, unguessable, and never committed:

```bash
fly secrets set --app anum-keycloak \
  KEYCLOAK_ADMIN=admin \
  KEYCLOAK_ADMIN_PASSWORD="$(openssl rand -base64 24)"
```

Once Keycloak has booted once against a non-empty database, changing these
secrets again has **no effect** — the master-realm admin user already
exists in the database at that point. To change the admin password later,
do it through the admin console (`https://anum-keycloak.fly.dev/admin/`)
or the Admin REST API, not by re-setting this secret.

### 1d. Deploy

```bash
fly deploy --config infra/fly/keycloak.fly.toml
```

Watch `fly logs --app anum-keycloak` on first boot — you're waiting for
Keycloak to finish its schema migration against `anum-keycloak-db` and
report it's listening. The health check in `keycloak.fly.toml`
(`GET /health/ready` with a 60s grace period) is tuned for this.

Once healthy, confirm the master realm is reachable:

```bash
curl -sf https://anum-keycloak.fly.dev/realms/master/.well-known/openid-configuration | head -c 200
```

---

## 2. Importing the production realm

Unlike the local dev stack — where `start-dev --import-realm` reads
`infra/docker/keycloak/anum-realm.json` off disk once at container startup
— a long-running production Keycloak has no equivalent "import this file on
boot" flag wired up here (nothing mounts a realm JSON into
`keycloak.fly.toml`, on purpose: the production template needs manual edits
first, per section 3 below, and those edits shouldn't happen by hand-editing
a file that then gets baked into infra). Instead, import it once, after
deploy, via the **Keycloak Admin REST API**.

### 2a. Get an admin access token

```bash
KEYCLOAK_URL=https://anum-keycloak.fly.dev

ADMIN_TOKEN=$(curl -sf -X POST \
  "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d grant_type=password \
  -d client_id=admin-cli \
  -d username="$KEYCLOAK_ADMIN" \
  -d password="$KEYCLOAK_ADMIN_PASSWORD" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])')
```

(`$KEYCLOAK_ADMIN` / `$KEYCLOAK_ADMIN_PASSWORD` here are the values you set
as Fly secrets in step 1c — export them into your shell from wherever you
stored them, e.g. a password manager; don't hardcode them in a script.)

### 2b. Prepare the edited realm file

See section 3 below for what needs editing. Once you have a fully edited
working copy (call it `/tmp/anum-realm.production.json`), import it:

### 2c. Import

```bash
curl -sf -X POST \
  "$KEYCLOAK_URL/admin/realms" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d @/tmp/anum-realm.production.json
```

A `201 Created` (empty body, `curl -sf` will just exit 0) means the `anum`
realm now exists on this Keycloak instance, with the `owner`/`member`/
`viewer` roles, the `anum-api` bearer-only client, and the `anum-web`
client with its three claim mappers (`tenant_id`, `workspace_id`, `roles`)
and audience mapper — the same shape `oidc_auth.py` expects, per
`infra/docker/keycloak/README.md`.

Verify:

```bash
curl -sf "$KEYCLOAK_URL/realms/anum/.well-known/openid-configuration" | head -c 200
```

If you need to re-import after fixing something, either delete the realm
first (`curl -X DELETE "$KEYCLOAK_URL/admin/realms/anum" -H "Authorization: Bearer $ADMIN_TOKEN"`)
or `PUT` to `/admin/realms/anum` instead of `POST`ing to `/admin/realms` to
update the existing one in place.

---

## 3. Manual edits required before import

This is the same list as `infra/docker/keycloak/README.md#production`,
spelled out as concrete steps against
`infra/docker/keycloak/anum-realm.production-template.json`. Work on a
**copy** — never edit the template in place, and never commit the edited
copy (it's specific to one deployment and will accumulate real,
deployment-specific values):

```bash
cp infra/docker/keycloak/anum-realm.production-template.json \
   /tmp/anum-realm.production.json
```

1. **Redirect URIs / web origins.** Replace every
   `REPLACE_WITH_YOUR_WEB_APP_URL` placeholder in the `anum-web` client's
   `redirectUris` and `webOrigins` with the real, deployed `anum-web` Fly
   URL (e.g. `https://anum-web.fly.dev` — confirm the actual URL against
   whatever the `anum-web` `fly.toml`/deploy actually produced; don't
   assume). Get this wrong and login redirects will be rejected by
   Keycloak with an `invalid redirect_uri` error.

2. **The login flow is already built** — `apps/web` implements real
   Authorization Code + PKCE login via `keycloak-js`
   (`apps/web/src/lib/auth.ts`), opt-in at build time through
   `VITE_ANUM_AUTH_MODE=oidc` (see the `fly.web.toml` build-arg comments and
   `docs/deployment.md`). The template's `anum-web` client
   (`"publicClient": true`, `"standardFlowEnabled": true`,
   `"directAccessGrantsEnabled": false`,
   `"attributes": {"pkce.code.challenge.method": "S256"}`) is exactly what
   that flow expects — no client-shape decision needed here, just deploy
   `anum-web` with those four `VITE_ANUM_*` build args set. The only
   remaining decision is if tokens will instead be minted by a
   backend/service process rather than an end-user's browser, in which
   case switch `anum-web` (or add a new client) to a confidential client:
   `"publicClient": false`, a real client secret generated by Keycloak (not
   put in this JSON — see below), and `"serviceAccountsEnabled": true`
   instead of `"standardFlowEnabled": true`.

3. **Remove every `_comment` field** — both the top-level one and the one
   inside the `anum-web` client entry. These are informational-only,
   explaining the template to a human reader; they are not real
   `RealmRepresentation`/`ClientRepresentation` fields Keycloak expects.
   With `jq` installed:

   ```bash
   jq 'del(._comment) | .clients |= map(del(._comment))' \
     /tmp/anum-realm.production.json > /tmp/anum-realm.production.clean.json
   mv /tmp/anum-realm.production.clean.json /tmp/anum-realm.production.json
   ```

   (Without `jq`, delete the two `"_comment": "..."` lines by hand in an
   editor — there are exactly two in the template as of this writing.)

4. Confirm `"users": []` is still empty before importing — the whole point
   of the template is that no production credentials ever pass through
   source control or a JSON file on disk longer than necessary. If you find
   yourself tempted to add a user object here with a real password, stop —
   see section 4 instead.

Once these are done, the file at `/tmp/anum-realm.production.json` is what
section 2c's `curl` imports.

---

## 4. Creating real users

**Do not add users to the realm JSON.** The production template ships with
`"users": []` deliberately (see `infra/docker/keycloak/README.md`) — real
credentials must never pass through a file that could end up committed,
logged, or shared. After the realm is imported, create users through
either:

- **The admin console**: `https://anum-keycloak.fly.dev/admin/` → select
  the `anum` realm → Users → Add user. Set the `tenant_id` and
  `workspace_id` user attributes (Attributes tab) to match what that user
  should see — these feed directly into the `tenant_id`/`workspace_id`
  claim mappers already configured on `anum-web`, which `oidc_auth.py`
  reads. Assign realm roles (`owner`/`member`/`viewer`) under Role mapping.
  Set a password under Credentials (uncheck "Temporary" only if you don't
  want to force a reset on first login).

- **The Admin REST API**, for scripted/bulk provisioning:

  ```bash
  curl -sf -X POST "$KEYCLOAK_URL/admin/realms/anum/users" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{
      "username": "jane.doe",
      "enabled": true,
      "attributes": { "tenant_id": ["tenant_acme"], "workspace_id": ["workspace_main"] },
      "credentials": [{ "type": "password", "value": "<temp-password>", "temporary": true }]
    }'
  ```

  Then look up the created user's ID and assign realm roles via
  `POST /admin/realms/anum/users/{id}/role-mappings/realm` — see the
  [Keycloak Admin REST API docs](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
  for the full shape.

This is **not a one-time setup script** — it's an ongoing administrative
task for as long as this Keycloak instance runs. New users, offboarding,
role changes, and password resets all go through this same path (console
or REST API) for the lifetime of the deployment; nothing in this repo
automates or should automate real user lifecycle management.

---

## 5. Pointing the API at this Keycloak

Once the realm is imported and at least one real user exists, point the
already-deployed `anum-api` Fly app at it:

```bash
fly secrets set --app anum-api \
  ANUM_KEYCLOAK_ISSUER=https://anum-keycloak.fly.dev/realms/anum \
  ANUM_OIDC_AUDIENCE=anum-api \
  ANUM_AUTH_MODE=oidc
```

These are exactly the settings `services/api/anum_api/settings.py` reads
(`keycloak_issuer` / `oidc_audience` / `auth_mode`, prefix `ANUM_`) — see
`docs/deployment.md`'s environment variable reference and production
checklist for what each one does and the rest of that checklist (CORS
origins, rate limiting, HSTS) that a real deployment also needs, none of
which is specific to Keycloak. `ANUM_OIDC_AUDIENCE=anum-api` matches the
`anum-api` client ID / audience mapper already baked into the realm
template (section 3), so it doesn't need to change unless that client ID
changes.

`ANUM_KEYCLOAK_ISSUER` doubles as the expected `iss` claim on every token
the API validates and is used to derive the JWKS endpoint
(`{issuer}/protocol/openid-connect/certs`) unless `ANUM_OIDC_JWKS_URL` is
set explicitly — so it must match `KC_HOSTNAME` in `keycloak.fly.toml`
exactly, including the `https://` scheme and the `/realms/anum` suffix.

`fly deploy --app anum-api` is **not** required for this — `fly secrets
set` restarts the app's machines with the new environment on its own.

---

## 6. What remains a human judgment call

Everything above is mechanical — commands you can run and get a
deterministic result. One thing in this runbook is deliberately *not*
automated, because no amount of tooling substitutes for a real decision
here:

- **Ongoing user provisioning.** Section 4's REST API calls create *one*
  user. Deciding who gets an account, what `tenant_id`/`workspace_id` they
  belong to, what role they hold, how offboarding works, and whether this
  ever grows into SCIM/an upstream identity provider federation are
  standing operational responsibilities for whoever administers this
  Keycloak instance, not a script that gets run once and forgotten.
