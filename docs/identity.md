# Identity and Sign-In

This document is the contract between Keycloak, the ANUM clients, and the API's authentication layer (`services/api/anum_api/identity.py` and `dependencies.py`). Change it together with the realm file (`infra/keycloak/anum-realm.json`) and the validator.

## Authentication Modes

`ANUM_AUTH_MODE` selects how the API establishes tenant context:

| Mode | Allowed `ANUM_ENVIRONMENT` | Tenant context comes from |
| --- | --- | --- |
| `headers` | `local`, `test` only | `x-tenant-id`, `x-workspace-id`, `x-user-id`, `x-user-roles` headers, or an `anum_local_*` bearer session from `/api/v1/auth/local/*` |
| `oidc` | any | A Keycloak access token plus the persisted workspace membership |

The API refuses to start (`AuthConfigurationError` from `validate_auth_configuration`, called when `anum_api.main` is imported) when `ANUM_AUTH_MODE` is not `oidc` outside `local`/`test`, or when the mode is unknown. Header-asserted context and `anum_local_*` sessions are development conveniences and must never serve a shared environment. A runtime guard in the header path repeats that check. In `oidc` mode the `/api/v1/auth/local/*` endpoints return `404` and `anum_local_*` tokens are rejected with `401`.

## Keycloak Realm

The realm is code: `infra/keycloak/anum-realm.json`, imported by `infra/docker/compose.yaml` with `start-dev --import-realm`. An import is skipped when the realm already exists. Keycloak's dev database lives inside the container (no named volume), so after changing the file run `docker compose -f infra/docker/compose.yaml up -d --force-recreate keycloak` to re-import it.

| Client | Type | Redirect URIs |
| --- | --- | --- |
| `anum-web` | public, authorization code + PKCE (S256) | `http://localhost:5173/*`, `http://127.0.0.1:5173/*` |
| `anum-desktop` | public, PKCE | `tauri://localhost/*`, `http://tauri.localhost/*`, `http://localhost:5173/*` (Tauri dev URL), `http://127.0.0.1/*` (RFC 8252 loopback, any port) |
| `anum-android` | public, PKCE | `com.anum.mobile:/oauth2redirect` (matches the Android `applicationId`) |
| `anum-flutter` | public, PKCE | `com.anum.app:/oauth2redirect` (the Flutter platform projects must register this scheme) |
| `anum-api` | bearer-only | none; it is the token audience, never a sign-in client |

All public clients disable the implicit flow, direct access (password) grants, service accounts, and device grants. Each carries three protocol mappers:

- `anum-api-audience` (`oidc-audience-mapper`) adds `aud: anum-api` to access tokens.
- `anum-tenant-id` maps the user attribute `tenant_id` to the `tenant_id` claim.
- `anum-default-workspace-id` maps the user attribute `default_workspace_id` to the `workspace_id` claim.

The declarative user profile makes `tenant_id` and `default_workspace_id` admin-view and admin-edit only, with the same `^[A-Za-z0-9_-]{3,80}$` pattern the API enforces, so users cannot move themselves into another tenant through the account console.

Realm roles `owner`, `member`, and `viewer` mirror `anum_api.authorization.Role`. They are coarse identity-provider grants: they let a caller bootstrap a tenant (see below), but they never override the persisted workspace membership.

The realm ships one user, `dev` / `anum-dev-only-password` (tenant `tenant_local`, default workspace `workspace_foundation`, realm role `owner`). That password and the compose `admin`/`admin` admin account are DEV-ONLY placeholders. Shared environments must use their own realm configuration, secrets from the deployment secret store, and no seeded users.

## Token Contract

The API accepts an access token only when all of these hold:

- Header `alg` is `RS256` and `kid` names a signature key (`use: sig`, `kty: RSA`) in the issuer's JWKS. Encryption keys Keycloak also publishes are ignored.
- The signature verifies; `iss` equals `ANUM_KEYCLOAK_ISSUER`; `aud` contains `ANUM_OIDC_AUDIENCE` (`anum-api`); `exp`, `iat`, `iss`, `sub`, `aud` are present; `exp`/`nbf` pass with `ANUM_OIDC_LEEWAY_SECONDS` (default 30) of clock skew.
- `tenant_id` is present and matches `^[A-Za-z0-9_-]{3,80}$`. `workspace_id` is optional (a default workspace) and must match the same pattern when present.

Roles are read from `realm_access.roles` and a top-level `roles` array, lowercased, and filtered to the ANUM roles.

## JWKS Caching and Rotation

`JwksCache` fetches `<issuer>/protocol/openid-connect/certs` (override with `ANUM_OIDC_JWKS_URL`, for example `http://keycloak:8080/realms/anum/protocol/openid-connect/certs` when the API runs in Docker but tokens are issued for `localhost`). Keys are cached for `ANUM_OIDC_JWKS_CACHE_SECONDS` (300). A token with an unknown `kid` forces one refetch so a Keycloak key rotation is picked up immediately; forced refetches are limited to one per `ANUM_OIDC_JWKS_MIN_REFRESH_SECONDS` (30) so random `kid` values cannot flood Keycloak. When a refresh fails the last known key set keeps serving. When no keys were ever fetched the API answers `503 service_unavailable`.

## Tenant and Workspace Resolution

1. The tenant is always the token's `tenant_id`. An `x-tenant-id` header is optional; when sent it must equal the token tenant or the request fails with `403`.
2. The workspace is the `x-workspace-id` header when present, otherwise the token's `workspace_id` claim. With neither, the request fails with `400` asking for `x-workspace-id`.
3. The membership `(tenant, workspace, sub)` is looked up through the repository. With PostgreSQL the lookup runs in its own session after `set_tenant_context`, so row-level security scopes it. A missing or inactive membership fails with `403 Active workspace membership required`.
4. The request's `TenantContext.roles` becomes the membership role. Token realm roles cannot elevate it.

`x-user-id` and `x-user-roles` are ignored in `oidc` mode.

### Bootstrap

Tenant, workspace, membership, and onboarding routes (`POST /api/v1/tenants`, `POST /api/v1/workspaces`, `POST /api/v1/workspace-memberships/current`, `GET`/`PUT /api/v1/onboarding`) use `provisioning_tenant_context`. When the caller has no membership yet, it authorizes with the token's ANUM realm roles, so a realm `owner` can create the tenant and workspace named by its claims and become their first owner. Once a membership exists, its role governs these routes too.

`POST /api/v1/workspace-memberships/current` only bootstraps an empty workspace: a realm `owner` without a membership gets 403 when the workspace already has members, so it cannot take over someone else's workspace. Everyone after the first owner joins through an invitation (below).

## Invitations and Membership Management

Implemented in `services/api/anum_api/workspace_members.py`, persisted in `workspace_invitations` and `audit_records` (migration `0006_workspace_invitations`, both under the same tenant and workspace RLS policy as the other workspace tables).

| Route | Who | Effect |
| --- | --- | --- |
| `POST /api/v1/workspace-invitations` `{role, invitee_user_id?, invitee_email?, ttl_hours?}` | `membership:manage` (owners) | Creates a pending invitation for `owner`, `member` or `viewer`, bound to a user id (the OIDC `sub`), a verified email, or both. `ttl_hours` defaults to 168 (7 days), 1 to 720. Returns the invitation and the token, once. |
| `GET /api/v1/workspace-invitations` | owners | Lists the workspace's invitations; a pending invitation past `expires_at` shows as `expired`. |
| `POST /api/v1/workspace-invitations/{id}/revoke` | owners | Revokes a pending invitation. |
| `POST /api/v1/workspace-invitations/accept` `{token}` | any authenticated caller, membership not required | Redeems the token for a membership in the workspace selected by `x-workspace-id` (or the token's default workspace). |
| `GET /api/v1/workspace-members` | owners | Lists memberships, active and inactive. |
| `PUT /api/v1/workspace-members/{user_id}/role` `{role}` | owners | Changes a member's role. |
| `POST /api/v1/workspace-members/{user_id}/deactivate` / `reactivate` | owners | Deactivates (the member then gets `403 Active workspace membership required`) or reactivates a membership. |

Rules:

- The token is `anum_inv_` plus 256 random bits (`secrets.token_urlsafe(32)`). Only its SHA-256 hash is stored; a token cannot be shown again after creation. Event payloads and audit metadata never contain it.
- Single use: accepting locks the invitation row (`FOR UPDATE`) and marks it `accepted` in the same transaction that writes the membership, so two concurrent accepts cannot both succeed. A reused token gets `409`.
- Expired or revoked tokens get `410`. A token of another tenant or workspace is looked up inside the caller's RLS scope and is simply not found (`404`), as is a malformed token.
- Identity binding: an invitation bound to a user id is accepted only by that `sub` (`403` otherwise). An email-bound invitation needs the token's `email` claim to match (case-insensitive) with `email_verified: true`. In local/test header mode the email is the `x-user-email` development header. A refused attempt does not consume the invitation.
- In `oidc` mode the tenant still comes from the invitee's token, so an invitation can only be accepted by a user whose IdP-managed `tenant_id` is the invitation's tenant.
- The invitee's token realm roles grant nothing: the new membership gets the invitation's role. An already active member gets `409`; a deactivated member is reactivated with the invited role.
- The last active owner of a workspace cannot be demoted or deactivated (`409`). Role changes and deactivations lock all active owner rows (in a fixed order) before the target row, so two owners demoting each other at the same moment cannot leave the workspace without an owner.
- Every change writes an append-only audit record (`workspace_invitation.create|accept|revoke`, `workspace_member.add|role_change|deactivate|reactivate`; the actor is the caller; RLS allows only select and insert on `audit_records`) and a canonical event ([Events](events.md#membership-events)), both in the request's transaction. With PostgreSQL the event is a durable outbox row, so it reaches NATS only if the change commits.
- Refused attempts are not audited yet (the transaction rolls back).

## Errors

| Status | When |
| --- | --- |
| `401 unauthorized` | Missing bearer token, malformed token, wrong algorithm, unknown `kid` (after the refresh), bad signature, wrong issuer or audience, expired, missing required claims, or an `anum_local_*` token in `oidc` mode. The response carries `WWW-Authenticate: Bearer realm="anum", error="invalid_token", error_description="..."`. |
| `400 bad_request` | No workspace selected, or `x-workspace-id` is malformed. |
| `403 forbidden` | `x-tenant-id` mismatch, missing or inactive membership, or a permission the membership role does not grant. |
| `503 service_unavailable` | Keycloak's JWKS cannot be fetched and no keys are cached. |

## Local Use

```bash
docker compose -f infra/docker/compose.yaml up keycloak
ANUM_AUTH_MODE=oidc uvicorn anum_api.main:app --reload --port 8000
```

Sign in through a client with authorization code + PKCE against `http://localhost:8080/realms/anum` as `dev`, then call the API with `Authorization: Bearer <access_token>`. The first `PUT /api/v1/onboarding` creates `tenant_local`/`workspace_foundation` and the owner membership.

## Now

Realm as code, `oidc` mode with JWKS rotation, persisted membership resolution, workspace selection by header, workspace invitations and membership management, and fail-fast refusal of development authentication outside local/test.

## Later

Client sign-in flows in web, desktop, Android, and Flutter; invitation delivery by email and client screens for membership management; audit records for refused invitation attempts; per-environment realm configuration with secrets from the deployment secret store; token revocation and session events in the audit log; MFA and federation policy.
