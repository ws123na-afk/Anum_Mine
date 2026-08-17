# ANUM dev/test Keycloak realm

`anum-realm.json` is imported automatically when the `keycloak` service in
`infra/docker/compose.yaml` starts (`start-dev --import-realm`). It exists so
`ANUM_AUTH_MODE=oidc` (see `services/api/anum_api/oidc_auth.py` and
`docs/deployment.md`) has something real to validate tokens against locally,
without hand-configuring Keycloak through its admin console every time.

**This realm is for local development and testing only.** Seeded users have
plaintext, well-known passwords and the realm has `sslRequired: none`. Do not
reuse this file, or these credentials, for any real deployment — a
production realm needs to be provisioned separately, with its own users and
secrets.

## What it configures

- Realm `anum` (matching the default `ANUM_KEYCLOAK_ISSUER` of
  `http://localhost:8080/realms/anum`).
- Realm roles `owner` / `member` / `viewer`, matching
  `anum_api.authorization.Role`.
- Client `anum-api`: bearer-only, exists only so it can be referenced as an
  audience target (see below) — nothing logs in as this client.
- Client `anum-web`: public, with `directAccessGrantsEnabled` so a token can
  be fetched with a plain `curl` (Resource Owner Password Credentials grant)
  instead of needing a browser login flow. It carries three protocol
  mappers that put exactly the claims `oidc_auth.py` expects onto the access
  token:
  - `tenant_id` / `workspace_id` — copied from matching user attributes.
  - `roles` — the user's realm roles, as a flat `roles` claim (Keycloak's
    default role-mapper nests these under `realm_access.roles`; this one is
    configured with `claim.name: roles` instead, deliberately, to match
    what `oidc_auth.py` reads).
  - An audience mapper that adds `anum-api` to the token's `aud` claim,
    matching the default `ANUM_OIDC_AUDIENCE`.
- Two seeded users:
  - `user_local` / `anum-dev-password` — `tenant_id=tenant_local`,
    `workspace_id=workspace_foundation` (the same tenant/workspace the
    stub-header dev flow uses by default), roles `owner`+`member`.
  - `user_beta_viewer` / `anum-dev-password` — a second tenant
    (`tenant_beta`/`workspace_beta`), role `viewer` only — useful for
    exercising tenant isolation and read-only permission checks.

## Getting a test token

With the compose stack's `keycloak` service running:

```bash
curl -s -X POST \
  http://localhost:8080/realms/anum/protocol/openid-connect/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d grant_type=password \
  -d client_id=anum-web \
  -d username=user_local \
  -d password=anum-dev-password \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
```

Use the resulting token as a Bearer token against the API once it's running
with `ANUM_AUTH_MODE=oidc`:

```bash
curl http://localhost:8000/api/v1/tasks \
  -H "Authorization: Bearer $TOKEN"
```
