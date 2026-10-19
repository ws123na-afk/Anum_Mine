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

The realm role `org_admin` mirrors `anum_api.authorization.OrganizationRole`: the organization administrator of the user's `tenant_id`, the only role that may make tenant-wide writes: policy packs, role templates, organization approval rules, memory governance, the organization model budget (`PUT /model-budgets/tenant`), routing targets, the marketplace catalog and skill version publishing ([Governance](governance-and-scale.md#who-may-change-what)). Workspace-scoped writes (this workspace's budget, installing a package or skill here, the approval policy) stay with the workspace owner. It is authoritative and is kept in `TenantContext.organization_roles` next to the membership role; it grants no workspace permission and never works without an active membership. Grant it only to the tenant's administrators; the DEV-ONLY `dev` user has it. Nobody gets it implicitly, not even a tenant's first owner: see [Organization administrators](#organization-administrators).

The realm ships one user, `dev` / `anum-dev-only-password` (tenant `tenant_local`, default workspace `workspace_foundation`, realm role `owner`). That password and the compose `admin`/`admin` admin account are DEV-ONLY placeholders. Shared environments must use their own realm configuration, secrets from the deployment secret store, and no seeded users. `infra/render/keycloak_realm.py` derives such a realm from this file at image build for the Render path: it drops `users`, sets the `anum-web` redirect URIs, web origins and post-logout redirects to `${ANUM_WEB_ORIGIN}` (resolved by Keycloak from its environment at import), removes the Vite dev-server URLs from `anum-desktop` and adds the two-factor policies ([Two-factor sign-in](#two-factor-sign-in), [Deploying on Render](deploy-render.md#keycloak)).

### Organization administrators

Without `org_admin` a caller can read tenant-wide policy but not change it: `403 Organization administrator role required: ...`, and the web Governance view is read-only with a notice. Both say how to get the role.

**Upgrading an existing Keycloak.** Keycloak imports the realm file only on its first start, so a realm created before the role was added to `anum-realm.json` has no `org_admin`, and after the upgrade every administrator, workspace owners included, is refused on policy packs, role templates, organization approval rules and memory governance until someone creates the role and assigns it. `infra/keycloak/realm_roles.py` does that through `kcadm`:

- `ensure-roles` creates the ANUM realm roles that are missing (`owner`, `member`, `viewer`, `org_admin`) with the realm file's descriptions; it leaves existing roles alone and grants nothing.
- `grant-org-admin --username <name>` ensures the roles and assigns `org_admin` to that one existing user; the user must have a `tenant_id` attribute (the role applies to that tenant only). Running it again changes nothing.
- `status` says whether the role exists and lists who holds it directly, with their `tenant_id`.

Who gets it is a decision for the person running the deployment, never a side effect of an upgrade: the role lets its holder change policy for every workspace of the tenant, and Keycloak cannot tell which of a tenant's owners should have that. Grant it to the people who administer each organization (on a one-owner server, the owner's own user). It takes effect at the user's next sign-in; a session that is already open keeps the old token until it refreshes, so they sign out and in again.

On the Oracle VM: `bash ~/anum/infra/oracle/keycloak-2fa.sh grant-org-admin <username>` (its `apply` also runs `ensure-roles`; `update.sh` prints a notice while nobody holds the role) ([Deploying on Oracle: Organization administrators](deploy-oracle.md#organization-administrators)). On Render, and anywhere without the script, in the admin console, realm **anum**: **Realm roles** → **Create role** `org_admin` if it is missing (description from `anum-realm.json`), then **Users** → the user → **Role mapping** → **Assign role** → filter by realm roles → `org_admin` ([Deploying on Render](deploy-render.md#keycloak)). Removing it: the same **Role mapping** tab → **Unassign**. `services/api/tests/test_keycloak_realm_roles.py` checks the script against a stand-in of Keycloak's admin API; it has not been run against a live Keycloak.

## Two-Factor Sign-In

Shared Keycloaks (Oracle, Render; Keycloak 26.4, which passkeys need) ask **every** user who signs in with a password for a second factor. Requiring it for everyone rather than only owners is a deliberate choice: the realm role is coarse (the authoritative role is the ANUM membership, which Keycloak cannot see), any member can read tenant data, and one rule is simpler to explain and harder to get wrong. The development realm (`infra/keycloak/anum-realm.json` as imported by `infra/docker/compose.yaml`, Keycloak 25) stays password-only so the CI journey can post the login form.

| What | Setting |
| --- | --- |
| Browser flow | `anum-browser-2fa`, bound as the realm's browser flow (below) |
| Authenticator app (OTP) | TOTP, HmacSHA1, 6 digits, 30 seconds, look-ahead 1, codes not reusable: what Google Authenticator, Microsoft Authenticator, FreeOTP, 1Password and Bitwarden accept |
| Passkeys | WebAuthn passwordless policy: relying party = the auth host (`ANUM_AUTH_HOST`), user verification **required**, discoverable credential required, ES256/RS256, "Enable passkeys" on, so the username and password page offers "Sign in with Passkey" and the browser's passkey autofill (conditional UI) |
| Recovery codes | Recovery Authentication Codes, an alternative to the app code on the second step |
| Required actions | "Configure OTP" enabled and a **default action** (every new user sets up an app at first sign-in); "Webauthn Register Passwordless" and "Recovery Authentication Codes" enabled |

`anum-browser-2fa` is a copy of Keycloak's `browser` flow (cookie, identity-provider redirector, organizations, username and password with passkeys) whose second step is replaced by `anum-2fa`:

```text
anum-2fa                               CONDITIONAL
  Condition - credential               REQUIRED   webauthn-passwordless not used
  anum-2fa-verify                      CONDITIONAL
    Condition - user configured        REQUIRED
    OTP Form                           ALTERNATIVE
    Recovery Authentication Code Form  ALTERNATIVE
  anum-2fa-enrol                       CONDITIONAL
    Condition - sub-flow executed      REQUIRED   anum-2fa-verify not-executed
    OTP Form                           REQUIRED
```

- **Password + app code** (or one recovery code through "Try another way"). Keycloak's default flow skips the second step for users who never set one up; `anum-2fa-verify` keeps that condition only to choose between asking and enrolling.
- **Password, no app yet** (every existing user, the first time): `anum-2fa-enrol` runs and the user must scan the QR code and enter a code before the sign-in completes. Nobody is locked out and nobody skips it. Existing users therefore need no preparation: they enrol on their next sign-in.
- **Passkey**: the whole second step is skipped. A passkey with user verification (fingerprint, face or device PIN) is already two factors.

People add a passkey or recovery codes themselves in the account console (`https://<AUTH>/realms/anum/account` → Account security → Signing in), or the owner sets the "Webauthn Register Passwordless" or "Recovery Authentication Codes" required action on them.

Clients need no change. Web, desktop, Flutter (`flutter_appauth`, the system browser) and Android all use authorization code + PKCE (S256) against Keycloak's hosted login pages, and the second step, passkeys and enrolment all happen on those pages; the tokens and claims the API checks are the same. No client uses the password grant (direct access grants stay off), which could not carry a second factor.

**How it gets there.** A realm file that lists any authentication flow must list all of them, so the flow is not in the import. `infra/render/keycloak_realm.py` adds the OTP and passkey policies to the imported realm (relying party left to Keycloak's host name), and `infra/keycloak/two_factor.py` converges a running Keycloak through `kcadm`: it creates and checks the flow, sets the relying party, enables the required actions and binds the flow, changes nothing when all is in place, and rebuilds `anum-2fa` if someone changed it in the console. On the Oracle VM run `infra/oracle/keycloak-2fa.sh` ([Deploying on Oracle: Two-factor sign-in](deploy-oracle.md#two-factor-sign-in)); on Render, follow the console steps below. `services/api/tests/test_keycloak_two_factor.py` checks the convergence against a stand-in of Keycloak's admin API; the flow was also exercised against Keycloak 26.4.0 (enrolment of an existing user, code, wrong code, recovery code, passkey registration and passkey-only sign-in).

**In the admin console** (Render, or to check by hand), realm **anum**: **Authentication** → `browser` → **Duplicate** as `anum-browser-2fa`; in the copy delete "Browser - Conditional 2FA" and add the `anum-2fa` sub-flow under the forms sub-flow exactly as above (the gear icon of each condition sets its options); **Action** → **Bind flow** → Browser flow. **Authentication** → **Policies** → OTP Policy and Webauthn Passwordless Policy as in the table. **Authentication** → **Required actions**: the three above on, "Configure OTP" also "Set as default action".

### Resetting a user's second factor

A user who lost the phone and the recovery codes: realm **anum** → **Users** → the user → **Credentials** → delete `otp` and `recovery-authn-codes` (keep the password). At the next sign-in they set up a new app. If repeated wrong codes locked them out, the same page offers **Unlock** (or **Realm settings** → **Security defenses** → brute force detection). A lost passkey: delete its `webauthn-passwordless` credential; the user signs in with password and app code.

On the Oracle VM the same without the console: `bash ~/anum/infra/oracle/keycloak-2fa.sh reset-user <username>` removes the app and the recovery codes and clears the lockout, nothing else.

**Break-glass** (the owner cannot reach the admin console, for example lost the master-realm admin's OTP): every `keycloak-2fa.sh` action takes `--temporary-admin`, which creates a temporary master-realm service account inside the Keycloak container with `kc.sh bootstrap-admin service`, uses it through `kcadm` and deletes it at the end. The secret is generated on the VM and reaches the container on stdin. To get back into the console itself, the same command creates a temporary user instead (it asks for the password): `cd ~/anum/infra/oracle && docker compose exec keycloak /opt/keycloak/bin/kc.sh bootstrap-admin user --optimized --username temporary-admin`, then sign in, fix the account (master realm → Users → your admin → Credentials → delete OTP; it asks for a new one at the next sign-in) and delete `temporary-admin`.

## Google and Apple Sign-In

"Continue with Google" and "Continue with Apple" are Keycloak identity brokering: the clients never talk to Google or Apple. Every button starts the same authorization code + PKCE (S256) request against realm `anum` as before, with one extra parameter, `kc_idp_hint=google` or `kc_idp_hint=apple`, which makes Keycloak skip its own page and send the person straight to the provider. The provider returns to Keycloak (`https://<AUTH>/realms/anum/broker/<google|apple>/endpoint`), Keycloak runs ANUM's flows below and issues the usual ANUM tokens. The API sees no difference: the token still has to carry `tenant_id`, and the membership still decides the role.

| Piece | Where |
| --- | --- |
| Provider and flow setup | `infra/keycloak/social_login.py` through `kcadm` (Oracle: `keycloak-2fa.sh google`, `apple`, `remove-provider`, and its plain run converges the flows; Render: the admin console, [Deploying on Render](deploy-render.md#google-and-apple-sign-in)) |
| Which buttons the clients show | `GET /api/v1/auth/sign-in-options` (public, no token): `identity_providers` from `ANUM_SIGN_IN_PROVIDERS` (`google`, `apple`; anything else refuses API startup), `self_sign_up` from `ANUM_SELF_SIGN_UP`, `privacy_policy_url` from `ANUM_PRIVACY_POLICY_URL`, or when that is empty the web host's `/legal/privacy` page from the first public `https` CORS origin ([Public pages](legal-pages.md#anum_privacy_policy_url); with Google or Apple configured this offers Create account against that page, which shows a DRAFT banner until published). Deployment configuration only, cached for 60 seconds; Keycloak still decides every sign-in, and a button for a provider Keycloak lacks just lands on Keycloak's own page. `social_login.py status` prints the values to set. |
| Web | `src/SignInView.tsx`, `src/lib/signIn.ts`, `src/lib/oidc.ts` (`beginSignIn(..., { idpHint, create })`) |
| Flutter | `lib/features/auth/welcome_screens.dart` (the Login and Create account screens, `OidcSignInView`, drawn from the options: only offered providers, Create account only with a privacy policy, the email sign-up only with self sign-up; and `PendingAccountScreen`), `sign_in_options.dart`, `oidc.dart` (`OidcSignInOptions`: `kc_idp_hint` as an additional parameter, `prompt=create` as a prompt value, `login_hint`; `pendingAccountOf`). English and Arabic. |

**What Keycloak runs** (converged by `social_login.py apply`, rebuilt in place if someone edits it in the console, so the providers keep pointing at the same flows):

```text
anum-first-broker-login                        first sign-in of a Google or Apple identity
  Review Profile                    REQUIRED   only fields the provider did not send
  anum-broker-user                  REQUIRED
    Create User If Unique           ALTERNATIVE   new email: a new account, with no tenant_id
    anum-broker-existing            ALTERNATIVE   the email belongs to an account already
      Confirm link existing account REQUIRED
      anum-broker-verify            REQUIRED
        Verify existing account by Email     ALTERNATIVE   a link to that account's address
        anum-broker-reauth          ALTERNATIVE
          Username Password Form    REQUIRED   that account's password
          anum-broker-reauth-2fa    CONDITIONAL
            Condition - user configured  REQUIRED
            OTP Form / Recovery Authentication Code Form  ALTERNATIVE

anum-post-broker-2fa                           after every Google or Apple sign-in
  anum-broker-2fa-verify            CONDITIONAL
    Condition - user configured     REQUIRED
    OTP Form / Passkey (WebAuthn Passwordless) / Recovery Authentication Code Form  ALTERNATIVE
  anum-broker-2fa-enrol             CONDITIONAL
    Condition - sub-flow executed   REQUIRED   anum-broker-2fa-verify not executed
    OTP Form                        REQUIRED   set up an authenticator app now
```

- **Linking by verified email.** Keycloak never links on the provider's word alone: Keycloak's "Automatically set existing user" is not in the flow (`apply` removes it if added). When the provider's email belongs to an existing ANUM account, the person confirms the link and proves they own that account, either through the link Keycloak emails to that account's address (when the realm has an email server) or with that account's password and its second factor. Providers are created with **Trust email** on (Google's `email_verified`; Apple only returns verified addresses), so a new account's email counts as verified and email-bound [invitations](#invitations-and-membership-management) work.
- **Two-factor rule kept.** A Google or Apple sign-in is one factor as far as ANUM is concerned. The post-login flow asks for the person's authenticator app, passkey or a recovery code, and someone who has none sets up an authenticator app before the sign-in completes, the same rule as [Two-factor sign-in](#two-factor-sign-in).
- **Provider settings.** Sync mode **Import** (the profile is copied on the first sign-in only, so the provider cannot later change the email), no stored provider tokens, Google with PKCE. Secrets reach Keycloak in a request body on `kcadm`'s stdin; the script reads them from its own stdin and never prints them; `GET` answers mask them.
- **No tenant from a provider.** Nothing in either flow sets `tenant_id`, `default_workspace_id` or a role. The user profile keeps those attributes admin-only, so "Review Profile" cannot show them either.

### Pending accounts

A Google or Apple sign-in of a new email, and every [self sign-up](#self-sign-up), creates a Keycloak account **without** `tenant_id`. The decision: no self-created account reaches any company's data without an invitation, and ANUM does not invent personal tenants (a tenant per stranger would be an unbounded, unowned set of organizations).

- **API.** A correctly signed token without `tenant_id` (absent or empty) is refused on every tenant-scoped route with `403` and code `account_pending` ("Your account is not part of an organization yet ..."), never `401`, so clients do not loop back to sign-in. A header cannot supply the tenant, and onboarding cannot create one for it. A malformed `tenant_id` stays a `401`.
- **Clients.** Web and Flutter read the claim before calling the API and show "Your account is waiting for access" with the account's email and account ID (copyable, which is what an administrator needs), **Check again** (refreshes the token: Keycloak reads the attributes again, so an assignment shows at once) and **Sign out**. Flutter keeps a pending account's tokens in memory only and stores no session.
- **Getting access.** An administrator lists such accounts with `keycloak-2fa.sh pending-users` and assigns one with `keycloak-2fa.sh assign-tenant <username> <tenant_id> [<workspace_id>]` (or the admin console: realm **anum** → **Users** → the user → **Attributes**: `tenant_id`, `default_workspace_id`). That grants nothing by itself: a workspace owner then invites the person in **Members** (bound to their email, which is verified for Google and Apple accounts, or to their account ID). `assign-tenant` refuses to move a user who already has another tenant; that stays a deliberate console change.

### Self sign-up

`keycloak-2fa.sh sign-up` (`social_login.py enable-sign-up --privacy-url ...`) turns on Keycloak's own registration page, which the clients open with `prompt=create` ("Sign up with email"):

- email as username, **Verify email** on, duplicate emails refused; it refuses to run while the realm has no email server (realm settings, **Email**), since nobody could verify their address;
- password policy `length(12) and maxLength(128) and lowerCase(1) and upperCase(1) and digits(1) and notUsername and notEmail and passwordHistory(3)`, enforced by Keycloak on submit;
- the **ANUM login theme** (`infra/keycloak/themes/anum`, a child of Keycloak 26's `keycloak.v2`, installed by `infra/render/keycloak.Dockerfile`): under the password field of the sign-up and change-password pages it lists every rule of the realm's policy, read from the policy at render time, and ticks each one while the person types (each item also says "Met" or "Not met yet" to screen readers). English and Arabic texts;
- the **Terms and Conditions** required action, enabled and a default action for every new account (sign-up, Google, Apple, or created by an admin): one page with a link to `ANUM_PRIVACY_POLICY_URL`, set as the realm's English and Arabic `termsText`; accepting records the time in the user's `terms_and_conditions` attribute; declining ends the sign-in.

Mobile-number sign-up is not offered: Keycloak has no SMS sender, and adding one means choosing and paying for an SMS provider ([Production plan](production-plan.md)). `keycloak-2fa.sh sign-up-off` turns registration off again; accounts already created stay. The client screens are the in-app front door: "Create account" shows the providers and "Sign up with email" only after a required "I have read and accept the privacy policy" checkbox (the link opens `privacy_policy_url`), and only when the API reports a privacy policy and some way to create an account.

### Apple

Keycloak has no built-in Apple provider: Apple answers with `form_post` and wants a client secret that is a JWT signed with the team's key, which Keycloak's generic OIDC provider cannot do. ANUM uses a provider extension (provider id `apple`; configuration `clientId` = Services ID, `teamId`, `keyId`, `p8Content`), installed into the image only when `APPLE_PROVIDER_URL` (the extension's release jar, https) and `APPLE_PROVIDER_SHA256` are given as build arguments (Oracle: `ANUM_KEYCLOAK_APPLE_PROVIDER_URL` and `..._SHA256` in `.env`; Render: the `anum-keycloak` environment). The build refuses a URL without a checksum or a jar that does not match it. Without the extension `set-provider --provider apple` stops with that explanation and nothing changes. **Not verified here:** the extension's jar could not be downloaded in the environment this was built in, so the configuration keys come from the extension's documentation and the Apple path has not run against a real Keycloak; check the first Apple sign-in on staging.

**App Store rule.** Apple's App Store Review Guideline 4.8 requires "Sign in with Apple" in an iOS app that offers another social sign-in. The Flutter app therefore hides "Continue with Google" on iOS unless Apple is offered too (`SignInOptions.visibleProviders`).

**Tests.** `services/api/tests/test_keycloak_social_login.py` (flows, providers, secrets on stdin, sign-up, pending accounts, assignment, the theme's rule list and the image, against the stand-in of Keycloak's admin API), `test_sign_in_options.py` and `test_identity.py` (pending tokens), `apps/web/test/signIn.test.ts` (options, pending claims, `kc_idp_hint` and `prompt=create` on the PKCE request, the theme's rule checks), `apps/web/e2e/social-sign-in.spec.ts` (mocked Keycloak redirect: Google button, create account with consent, pending screen and Check again, options unavailable, axe and keyboard), `apps/mobile/test/social_sign_in_test.dart` (options, iOS rule, hints, create account, pending screen in English and Arabic, layouts at 320 to 1440 wide and 200% text) and `test/auth/welcome_screens_test.dart`. The script, both flows and the theme were also run against a real Keycloak 26.4.0: `apply` twice (second run reads only), Google provider creation (`kc_idp_hint=google` redirects to Google with the client ID and the broker endpoint), a second realm brokered as an OIDC provider standing in for Google (new account created without `tenant_id`, authenticator app enrolment, privacy policy page with its link, code on the next sign-in, linking an existing account only after its password), and the sign-up page's live rules at 1280 and 390 pixels wide.

## Token Contract

The API accepts an access token only when all of these hold:

- Header `alg` is `RS256` and `kid` names a signature key (`use: sig`, `kty: RSA`) in the issuer's JWKS. Encryption keys Keycloak also publishes are ignored.
- The signature verifies; `iss` equals `ANUM_KEYCLOAK_ISSUER`; `aud` contains `ANUM_OIDC_AUDIENCE` (`anum-api`); `exp`, `iat`, `iss`, `sub`, `aud` are present; `exp`/`nbf` pass with `ANUM_OIDC_LEEWAY_SECONDS` (default 30) of clock skew.
- `tenant_id` is present and matches `^[A-Za-z0-9_-]{3,80}$`. `workspace_id` is optional (a default workspace) and must match the same pattern when present.
- `tenant_ids` is optional: further organisations the user may act in (a list, or one string; invalid entries are ignored, at most 50). See [Customer organisations](customer-organisations.md#switching-organisations).

Roles are read from `realm_access.roles` and a top-level `roles` array, lowercased, and filtered to the ANUM roles.

## JWKS Caching and Rotation

`JwksCache` fetches `<issuer>/protocol/openid-connect/certs` (override with `ANUM_OIDC_JWKS_URL`, for example `http://keycloak:8080/realms/anum/protocol/openid-connect/certs` when the API runs in Docker but tokens are issued for `localhost`). Keys are cached for `ANUM_OIDC_JWKS_CACHE_SECONDS` (300). A token with an unknown `kid` forces one refetch so a Keycloak key rotation is picked up immediately; forced refetches are limited to one per `ANUM_OIDC_JWKS_MIN_REFRESH_SECONDS` (30) so random `kid` values cannot flood Keycloak. When a refresh fails the last known key set keeps serving. When no keys were ever fetched the API answers `503 service_unavailable`.

## Tenant and Workspace Resolution

1. The tenant is the token's `tenant_id`. An `x-tenant-id` header is optional; when sent it must equal the token tenant or be listed in its `tenant_ids` claim, otherwise the request fails with `403`. In another listed organisation the organisation roles (`org_admin`, `platform_operator`) are dropped.
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
| `GET /api/v1/workspace-invitations?status=` | owners | Lists the workspace's invitations, newest first; a pending invitation past `expires_at` shows as `expired`. `status` (`all` default, `pending`, `accepted`, `revoked`, `expired`) filters on the shown status. |
| `POST /api/v1/workspace-invitations/{id}/resend` `{ttl_hours?}` | owners | Issues a new token for a pending or expired invitation and restarts its expiry (default 168 hours). The previous token stops working at once (only the new hash is kept). Returns the invitation and the new token, once. Accepted or revoked invitations answer `409`. ANUM does not send email: the owner passes the new token or link on. |
| `POST /api/v1/workspace-invitations/{id}/revoke` | owners | Revokes a pending invitation. |
| `POST /api/v1/workspace-invitations/accept` `{token}` | any authenticated caller, membership not required | Redeems the token for a membership in the workspace selected by `x-workspace-id` (or the token's default workspace). |
| `GET /api/v1/workspace-members?q=&status=&role=&limit=&cursor=` | owners | Lists memberships, active first, then owners, members and viewers, then by user id. Each carries `email`: the verified email of the invitation the member accepted, or null. `q` matches the user id or that email (case-insensitive), `status` is `all`, `active` or `deactivated`, `role` one role. Without `limit` every match is returned; with `limit` (1 to 200) one page, and the `X-Next-Cursor` response header (exposed to browsers by CORS) holds the cursor of the next one. |
| `GET /api/v1/workspace-members/summary` | owners | `{total, active, deactivated, active_owners, pending_invitations}` for the whole workspace, independent of search and paging. Clients use `active_owners` for the last-owner protection. |
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
- An invitation cannot be accepted into an [archived](#workspace-lifecycle) workspace (`409`).
- Every change writes an append-only audit record (`workspace_invitation.create|accept|resend|revoke`, `workspace_member.add|role_change|deactivate|reactivate`; the actor is the caller; RLS allows only select and insert on `audit_records`) and a canonical event ([Events](events.md#membership-events)), both in the request's transaction. With PostgreSQL the event is a durable outbox row, so it reaches NATS only if the change commits.
- Refused attempts are not audited yet (the transaction rolls back).

### Client screens

Web (`Members` in the navigation, `apps/web/src/AdminViews.tsx`) and Flutter (Settings › Workspace administration, `apps/mobile/lib/features/admin/`) show live data from these routes only:

- **Members:** summary counts from `/workspace-members/summary`; the list with user id, email (when known), role, active or deactivated. The web list is searched (user id or email, debounced) and filtered by status and role on the server and paged with "Load more members" (50 at a time); Flutter loads the list and searches it on the phone. A role change or a deactivation opens a confirmation that says what the new role may do (web: a modal dialog with focus moved in and returned, Escape to cancel; Flutter: an alert dialog). The only active owner's other roles and Deactivate are disabled with the reason next to them; the API's `409` last-owner refusal still stands behind it and is shown in its own words.
- **Invitations:** create with a user id, an email or both, a role and an expiry (1 to 720 hours, default 168), validated like the API before sending. The token is shown once, with a copy action, the expiry and a warning that it cannot be shown again; it is kept only in memory and dropped on Done. The web client also shows a link, `<web app URL>#invitation=<token>&workspace=<workspace_id>`: the token travels in the URL fragment, which browsers never send to a server. Invitations are filtered by status (web: Pending, Expired, Accepted, Revoked, All; Pending by default). Pending invitations can be resent (a new token, shown once like a new invitation; "Renew" for an expired one) or revoked after a confirmation; accepted, revoked and expired ones stay listed with their dates.
- **Accept an invitation** is open to everyone signed in: paste the token or the link (Flutter also offers paste from the clipboard). Opening a link in the web app reads it once, removes it from the address bar and history, and pre-fills the form. The workspace from the link (or typed in) is sent as `x-workspace-id` for the accept call only, so an invitee can join a workspace other than the one the client has selected; the client then says to switch workspace. The web client also remembers the joined workspace for its [workspace switcher](#workspace-switcher) and offers a "Switch to <workspace>" button.
- **Invitation link in Flutter:** with `--dart-define=ANUM_WEB_APP_URL=<web app URL>` (an absolute `http(s)` URL), the one-time reveal also shows and copies the same `<web app URL>#invitation=<token>&workspace=<workspace_id>` link as the web client. Without it the app shows the token and the workspace id only, because it does not know where the web app lives.
- **Approval policy:** the owner screen for the workspace's two-person rule and medium-risk approval sits next to these ([Approvals and risk](approvals-and-risk.md#policy-screens)).
- **Controllers:** in Flutter every screen opened from Settings › Workspace administration owns its controller and disposes it when the screen is closed (`OwnedController`, `apps/mobile/lib/features/admin/owned_controller.dart`).
- **Permissions are the API's answer.** Clients do not guess roles from the token: listing members or invitations answers `403` for non-owners, and the screens then explain that the owner role is needed and quote the API's message. A `403` on a change is shown in place the same way.
- **Audit log:** owners and organization administrators read the workspace's [audit log](governance-and-scale.md#audit-log) in the web app (Audit log in the navigation) and in Flutter (Settings › Workspace administration › Audit log, read-only).
- Tests: `apps/web/test/admin.test.ts` and `apps/web/test/audit.test.ts` (mapping, validation, links, member query, last-owner check, error envelope), `apps/web/e2e/admin.spec.ts` (desktop and mobile projects, mocked API: confirmations, search, resend, status filter) and `apps/mobile/test/admin_test.dart` plus `apps/mobile/test/audit_admin_test.dart` (repository, controllers and screens with a fake transport, search, confirmed role change, last-owner lock, resend, the web link with and without `ANUM_WEB_APP_URL`, and controller disposal on pop). API: `services/api/tests/test_workspace_members.py`, `test_audit_log.py` and `test_postgres_audit_log.py`.

## Workspace lifecycle

Implemented in `services/api/anum_api/workspace_admin.py`; `workspaces.archived_at` and `archived_by` come from migration `0028_workspace_admin` (nullable, expand-only).

| Route | Who | Effect |
| --- | --- | --- |
| `GET /api/v1/workspaces/current` | any member | The workspace, with `archived_at` and `archived_by` (null unless archived). |
| `GET /api/v1/workspaces/current/permissions` | any member | `{workspace_id, role, organization_admin, can_manage_workspace, can_create_workspace, can_manage_members, can_read_audit, can_export_audit}`: the API's answer for the caller, so clients show or hide controls without guessing from the token. |
| `PATCH /api/v1/workspaces/current` `{name}` | owners (`workspace:manage`) or organization administrators with a membership here | Renames the workspace (1 to 160 characters, spaces collapsed). |
| `POST /api/v1/workspaces/current/archive` `{confirm_name}` | same | Archives it. `confirm_name` must equal the current name exactly (`422` otherwise). |
| `POST /api/v1/workspaces/current/restore` | same | Restores an archived workspace; restoring an active one changes nothing. |
| `POST /api/v1/organization/workspaces` `{name, workspace_id?}` | owners of the request's workspace (`workspace:create`) or organization administrators | Creates another workspace in the caller's tenant and makes the caller its first owner, in its own transaction in the new workspace's RLS scope. `workspace_id` follows the `x-workspace-id` pattern; left out, the API generates `ws_<name slug>_<8 hex>`. An id already in use answers `409`, and so does a tenant that has not finished onboarding. Ids starting with `ws_demo_` are reserved for the [sales demo workspace](demo-workspace.md) and answer `422`. |

- **Archived means read-only.** Every request to an archived workspace with a method other than `GET`, `HEAD` or `OPTIONS` is refused with `409` and the message "This workspace is archived, so it is read-only. A workspace owner or an organization administrator can restore it in Settings > Workspace." (`dependencies.refuse_writes_to_archived_workspace`, run by `tenant_context`, so it covers every workspace route), except the restore route. Accepting an invitation into it is refused too. Reads, the audit log and its export keep working. Automation schedules of an archived workspace advance without running (both the PostgreSQL scheduler and the local engine), so restoring does not replay missed fires.
- **Nothing is deleted.** Archiving keeps the data, the memberships and the audit log. Deleting a workspace is a separate, owner-only action with a typed confirmation and a cooling-off period ([Organisation export and deletion](organisation-lifecycle.md)), allowed in an archived workspace too.
- **Audit and events.** `workspace.rename`, `workspace.archive`, `workspace.restore` and `workspace.create` audit records with canonical events `workspace.renamed|archived|restored|created` ([Events](events.md#membership-events)), in the request's transaction. A created workspace records `workspace.create` in the new workspace (with `created_from`) and again in the workspace it was created from.
- **Switchers.** [`GET /api/v1/me/workspace-memberships`](#my-workspaces) marks archived workspaces (`archived: true`); the web switcher labels them "archived (read-only)". An archived workspace can still be switched to, so it can be restored.
- **Clients.** Web: Settings › Workspace › This workspace shows the name, id, organization, creation date and status, and for those allowed the Rename form, Archive (a dialog that asks for the typed name; the confirm button stays disabled until it matches) or Restore, and Create another workspace, with a "Switch to <name>" button after creation. Everyone else sees the facts and who can change them. Every view shows a read-only banner while the current workspace is archived. Flutter: Settings › Workspace administration › This workspace (`apps/mobile/lib/features/admin/workspace_settings_screen.dart`) shows the same facts, the caller's role from the permissions route, and for those allowed Rename, Archive (a dialog with the typed name; Archive stays disabled until it matches) and Restore. Everyone else sees the facts and who can change them; a `403` from the API is shown in its own words. Creating another workspace is web only.
- Tests: `services/api/tests/test_audit_log.py` (permissions, rename, archive with confirmation, write refusal, restore, create and both audit records, CORS for `PATCH` and the exposed headers), `test_postgres_audit_log.py` (the same under RLS as the app role, including the reader role's new column), `test_automation.py` / `test_postgres_automation.py` (schedules of archived workspaces), `apps/web/test/audit.test.ts` and `apps/web/e2e/audit.spec.ts` (rename, archive, restore, create and switch; a viewer's read-only view; layout at the five gate sizes), and `apps/mobile/test/admin/org_admin_test.dart` (rename, typed archive confirmation, restore, a viewer, denied and error, 320 to 1440 px at 200% text in English and Arabic).

## Errors

| Status | When |
| --- | --- |
| `401 unauthorized` | Missing bearer token, malformed token, wrong algorithm, unknown `kid` (after the refresh), bad signature, wrong issuer or audience, expired, missing required claims, or an `anum_local_*` token in `oidc` mode. The response carries `WWW-Authenticate: Bearer realm="anum", error="invalid_token", error_description="..."`. |
| `400 bad_request` | No workspace selected, or `x-workspace-id` is malformed. |
| `403 forbidden` | `x-tenant-id` mismatch, missing or inactive membership, or a permission the membership role does not grant. |
| `403 account_pending` | A valid token without `tenant_id`: a self sign-up or Google/Apple account no administrator has assigned yet ([Pending accounts](#pending-accounts)). |
| `503 service_unavailable` | Keycloak's JWKS cannot be fetched and no keys are cached. |

## Client Sign-In

Every client uses authorization code + PKCE (S256) with its own public Keycloak client from the table above, sends `Authorization: Bearer <access_token>` and `x-workspace-id` on every API call, refreshes before the access token expires, and signs out with RP-initiated logout (`end_session_endpoint` with `id_token_hint` and the client's post-logout redirect). When a client has no issuer configured it keeps the local development sign-in (`/api/v1/auth/local/*`), which only works against an API in `headers` mode; Flutter release builds always have one (the production issuer by default).

The workspace a client sends is the one the user selected, else the token's `workspace_id` claim, else the client's configured default. Clients read claims from the access token without verifying it; the API is the verifier. Clients never send `x-user-id` or `x-user-roles` with an OIDC token, and do not send `x-tenant-id` at all, so the token's `tenant_id` always decides the tenant.

### Web and Desktop

`apps/web` (also packaged by the Tauri shell) implements the flow itself without a library: `src/lib/oidc.ts` holds PKCE, state, nonce, discovery, code exchange, refresh and the logout URL; `src/lib/auth.ts` wires it to the page; `src/lib/api.ts` adds the headers. Build-time configuration:

| Variable | Value |
| --- | --- |
| `VITE_ANUM_OIDC_ISSUER` | Issuer URL, for example `http://localhost:8080/realms/anum`. Unset or empty keeps the local session, except in a desktop release build, which defaults to `https://auth.anum360os.com/realms/anum` and refuses local addresses ([Desktop release builds](desktop.md#release-builds)). |
| `VITE_ANUM_OIDC_CLIENT_ID` | `anum-web` (default) in the browser, `anum-desktop` for the Tauri build. |
| `VITE_ANUM_WORKSPACE_ID` | Optional workspace sent when the token has no `workspace_id` claim. |

- The redirect URI and post-logout redirect are the app's own origin and path (`http://localhost:5173/` in development), which the realm already allows. The desktop build uses a loopback redirect instead (below).
- Discovery must return the configured issuer exactly. The callback must carry the `state` of the one pending request, which is single-use and expires after 10 minutes; the ID token's `nonce` must match. Code and state are removed from the address bar after the callback.
- Storage: access and ID tokens live in memory only. The refresh token and the pending request (state, verifier, nonce) live in `sessionStorage` for the current tab, so a reload restores the session and closing the tab ends it. No token or claim goes to `localStorage`; the only thing kept there is the [workspace switcher](#workspace-switcher)'s list of workspace ids.
- Refresh runs a minute before expiry (halfway through lifetimes shorter than two minutes) and again before any API call that finds the token due. A rejected refresh token (`invalid_grant`) signs the user out; a network failure keeps the current token until it expires.
- The desktop shell runs the login in the system browser with an RFC 8252 loopback redirect (`http://127.0.0.1:<ephemeral port>/callback`, served once by the shell): `beginSignIn` takes the loopback URI as a per-request redirect, accepts only `http://127.0.0.1` or `http://[::1]` with a port, and the code exchange repeats it. Sign-out opens the end-session URL in the system browser without a post-logout redirect. Details in [Desktop](desktop.md#sign-in). Its CSP allows the local Keycloak (`http://localhost:8080`); `pnpm --filter @anum/desktop build:release` adds the production issuer origin to `connect-src`.
- Tests: `apps/web/test/oidc.test.ts` (Node's test runner, part of `pnpm check`) covers PKCE, state, nonce, expiry, refresh and logout; `apps/web/e2e/oidc-sign-in.spec.ts` runs the full browser flow against a build with OIDC enabled and a provider mocked by route interception.

#### Workspace switcher

The workspace in the top bar (`tenant / workspace`) opens the switcher (`apps/web/src/WorkspaceSwitcher.tsx`, logic in `src/lib/workspaces.ts`). Every API call sends the selected workspace as `x-workspace-id` (the local development session also carries it in its body), so the tenant context stays explicit after a switch.

- **What it lists.** Opening the switcher calls [`GET /api/v1/me/workspace-memberships`](#my-workspaces) and lists every workspace of the tenant where the caller has an active membership, with its name and the caller's role ("Finance · your role: viewer"), current workspace first. It also keeps what the client knows by itself: the token's `workspace_id` claim and `VITE_ANUM_WORKSPACE_ID` (or the development default in local mode), and workspaces this browser joined through an invitation or switched to. If the list cannot be loaded the switcher says so and shows only those. Any other workspace can be typed in; a remembered one can be forgotten.
- **Switching asks the API first.** With OIDC or header sessions the client calls `GET /api/v1/workspace-memberships/current` with the target as `x-workspace-id` and switches only on an active membership in that workspace; a `403` is shown in the switcher and the current workspace stays selected. A local development session is re-issued by `POST /api/v1/auth/local/workspace/switch`, which checks the membership itself.
- **After a switch** the app remounts under the new workspace: no task, approval or event stream state from the previous workspace survives, and the next requests name the new one. The choice resolves before the token claim and the configured default, and survives a reload.
- **Storage.** `localStorage` key `anum.workspaces.<tenant>.<user>` holds `{selected, known}` workspace ids (at most 20, each matching the API's id pattern) and nothing else, so two people sharing a browser do not see each other's list. Unreadable or blocked storage reads as empty and the switcher still works for the page.
- Tests: `apps/web/test/workspaces.test.ts` (resolution order, storage, options, the membership list and its labels) and `apps/web/e2e/policy.spec.ts` / `admin.spec.ts` (membership check, refusal of a listed workspace whose membership lapsed, the list from the API and the fallback when it fails, every later request naming the new workspace, switching after accepting an invitation).

### Flutter

`apps/mobile` uses `flutter_appauth` (pinned `12.1.0`), which runs the login in the system browser and applies PKCE, state and nonce. Configuration is by `--dart-define`:

| Define | Value |
| --- | --- |
| `ANUM_OIDC_ISSUER` | Issuer URL. Unset: debug and profile builds keep the local development sign-in; release builds use `https://auth.anum360os.com/realms/anum` and never show the development sign-in ([Release builds](mobile.md#release-builds)). |
| `ANUM_OIDC_CLIENT_ID` | `anum-flutter` (default). |
| `ANUM_OIDC_REDIRECT_URL` | `com.anum.app:/oauth2redirect` (default). |
| `ANUM_WORKSPACE_ID` | Optional workspace sent when the token has no `workspace_id` claim. |
| `ANUM_WEB_APP_URL` | Optional web app address; when set, a new invitation also shows the web invitation link ([Client screens](#client-screens)). |

- The sign-in screens (ANUM 360 Login and Create account, `lib/features/auth/welcome_screens.dart`) never collect a password: the password is typed on Keycloak's page in the system browser. Each button starts the same authorization code + PKCE flow with a hint (`OidcSignInOptions`):

  | Button | Authorization request |
  | --- | --- |
  | Sign In (Email or Mobile tab) | `login_hint=<email or +number>` (prefills Keycloak's username field; empty is allowed) |
  | Continue with Google | `kc_idp_hint=google` |
  | Continue with Apple | `kc_idp_hint=apple` |
  | Create Account | `prompt=create` (Keycloak's registration form), plus `login_hint` when an email was typed; the terms box must be ticked first |
  | Forgot password? | opens `<issuer>/login-actions/reset-credentials?client_id=anum-flutter` in the browser (not a code flow); afterwards the person signs in |

  What Keycloak needs for these to work: identity providers with the aliases **`google`** and **`apple`** in the `anum` realm (an OIDC/Google provider and an Apple provider, each with its client id and secret or key held by Keycloak, never by the app), and a first-broker-login flow that maps a new social account to a user with the `tenant_id` attribute (and optionally `default_workspace_id`), because the API refuses tokens without `tenant_id`; `registrationAllowed: true` for Create Account (it is `false` in the local realm import, so `prompt=create` falls back to the login page there) and the same tenant mapping for self-registered users; `resetPasswordAllowed: true` (already on) and working realm e-mail for Forgot password; and, to match the password guidance on Create account, a password policy such as `length(8) and upperCase(1) and digits(1) and specialChars(1)`. Sign-in with a mobile number works only when Keycloak accepts the number as a username (or has a phone authenticator). Tests: `apps/mobile/test/auth/welcome_screens_test.dart`, `test/oidc_session_test.dart`.
- Tokens (access, refresh, ID) are stored with the session in `flutter_secure_storage`. `OidcSessionStore` refreshes a session read within 60 seconds of expiry, so API calls, file transfer and audit export always send a fresh token; concurrent reads share one refresh. `invalid_grant` clears the session; network failures keep it for the next attempt.
- Workspace switching with an OIDC session first calls `GET /api/v1/workspace-memberships/current` with the target as `x-workspace-id` and switches only on an active membership there; then it changes the stored workspace (and so `x-workspace-id`) without calling the local session API. A local session is re-issued by `POST /api/v1/auth/local/workspace/switch`, which checks the membership itself.
- Settings › Switch workspace (`WorkspaceSwitcherScreen` in `lib/features/auth/account_screens.dart`) is the workspace picker: it lists the caller's memberships from [`GET /api/v1/me/workspace-memberships`](#my-workspaces) (name, id and role, the current one marked) and switches on a tap, after the check above; a refusal stays on the screen with the reason. A workspace ID can still be typed, and if the list cannot be loaded the screen says so. Tests: `apps/mobile/test/workspace_picker_test.dart` and `test/oidc_session_test.dart`.
- First run without a workspace (no `workspace_id` claim and no `ANUM_WORKSPACE_ID`): the app lists the caller's memberships with [`GET /api/v1/me/workspace-memberships`](#my-workspaces) and selects the only one, offers a picker for several (remembering the choice per tenant and user in secure storage), or explains that the account is in no workspace ([Mobile](mobile.md#first-run)). Tests: `apps/mobile/test/first_run_test.dart`.
- Sign-out ends the Keycloak session in the browser, then clears the session and tokens from secure storage even if the browser step is cancelled or fails; the remembered workspace choice (an id) stays.
- Plain-HTTP issuers are accepted only in debug and profile builds. The issuer the app uses must equal the API's `ANUM_KEYCLOAK_ISSUER`; from the Android emulator use `adb reverse tcp:8080 tcp:8080` and `ANUM_OIDC_ISSUER=http://localhost:8080/realms/anum` rather than `10.0.2.2`, which would mint tokens with a different `iss`.
- The redirect scheme `com.anum.app`, which is also the app's applicationId and bundle identifier, is registered in the committed native projects by `tool/configure_native.dart`: the `appAuthRedirectScheme` manifest placeholder in `android/app/build.gradle.kts` (used by AppAuth's redirect activity intent filter) and `CFBundleURLTypes` in `ios/Runner/Info.plist`.

The Kotlin Android app (`anum-android`) does not sign in through Keycloak and is frozen ([Android](android.md#status-frozen)); Flutter is the shipping mobile app.

## My Workspaces

`GET /api/v1/me/workspace-memberships` returns the caller's **active** memberships in every workspace of the caller's tenant, sorted by workspace name, then id:

```json
[{"tenant_id": "tenant_local", "workspace_id": "workspace_finance", "workspace_name": "Finance", "role": "viewer", "status": "active"}]
```

- **Who may call it.** Any authenticated caller. The tenant comes from the token (`tenant_id` claim; an `x-tenant-id` header must agree) and the user from its subject; in local/test header mode from the development headers or the `anum_local_*` session. No membership in the selected workspace is needed: someone deactivated there can still find where they may switch. With OIDC no workspace needs to be selected at all (no `x-workspace-id` and no `workspace_id` claim), so a client whose user has no `default_workspace_id` can list the workspaces to choose from; this is the only route that accepts an unselected workspace (`directory_identity` in `anum_api/dependencies.py`), and a malformed `x-workspace-id` is still refused. Header mode keeps requiring the development workspace header. It reveals only the caller's own memberships, never other users' or other tenants'. Deactivated memberships are not listed; `workspace_name` is null if the workspace row cannot be read. `archived` is true for an [archived workspace](#workspace-lifecycle).
- **Switching still checks.** The list is a convenience. Clients switch only after `GET /api/v1/workspace-memberships/current` (or the local session switch) confirms an active membership in the target, so a membership deactivated after the list was loaded is refused.
- **How it stays inside RLS.** `workspace_memberships` and `workspaces` are under forced RLS that admits one workspace at a time (`anum.tenant_id` and `anum.workspace_id`), so the application role cannot list a user's memberships across workspaces. The route reads them in its own short transaction that starts with `SET LOCAL ROLE anum_membership_reader`, sets `anum.tenant_id` and `anum.user_id` from the authenticated identity (and no workspace), runs one query and always rolls back (`anum_api/membership_directory.py`). The role comes from migration `0013_approvers_and_directory`:

  | Table | Columns granted | Policy (`for select to anum_membership_reader`) |
  | --- | --- | --- |
  | `workspace_memberships` | `tenant_id`, `workspace_id`, `user_id`, `role`, `active` | `tenant_id = anum.tenant_id and user_id = anum.user_id and active` |
  | `workspaces` | `tenant_id`, `id`, `name`, `archived_at` (migration `0028_workspace_admin`) | same tenant, and an active membership of `anum.user_id` in that workspace exists |

  The role is `NOLOGIN`, has no write privilege and no `BYPASSRLS`, and nothing is `SECURITY DEFINER`. Without `anum.user_id` or `anum.tenant_id` it sees nothing. Deployments grant it to the API login **`WITH INHERIT FALSE, SET TRUE`** (`infra/helm/bootstrap-database.sql`): permissive policies `TO` a role also apply to every member that inherits its privileges, so an inheriting grant would widen what the application role's own queries see. With `INHERIT FALSE` the API can only switch to it with `SET ROLE`, and only this route does. See [Multi-tenancy](multi-tenancy.md#membership-directory-role).
- **In-memory backend.** The repository filters the process store by tenant, user and `active`.
- Tests: `services/api/tests/test_membership_directory.py` (in memory, headers and OIDC, including a token without a workspace) and `test_postgres_membership_directory.py` (the route as the non-owner app role; the reader role sees only the caller's rows and the granted columns, cannot write, sees nothing without the settings, and its policies do not apply to the app role granted with `INHERIT FALSE`).

## Local Use

```bash
docker compose -f infra/docker/compose.yaml up keycloak
ANUM_AUTH_MODE=oidc uvicorn anum_api.main:app --reload --port 8000
```

Sign in through a client with authorization code + PKCE against `http://localhost:8080/realms/anum` as `dev`, then call the API with `Authorization: Bearer <access_token>`. The first `PUT /api/v1/onboarding` creates `tenant_local`/`workspace_foundation` and the owner membership. For the web app:

```bash
VITE_ANUM_OIDC_ISSUER=http://localhost:8080/realms/anum pnpm dev:web
```

The API must allow the web origin (`ANUM_CORS_ORIGINS`) and run in `oidc` mode; the web app then shows a Sign in button that opens Keycloak.

`services/api/scripts/run_journey.sh` does all of this end to end and is what the CI job "Authenticated journey" runs: it starts Keycloak, PostgreSQL and NATS from the compose file, runs the API in `oidc` mode against PostgreSQL as a non-superuser role (`anum_app`, so RLS applies) with the NATS event bus, signs in as `dev` through `anum-web` with authorization code + PKCE by posting the Keycloak login form, bootstraps onboarding, then creates, runs and approves a risky task (`services/api/scripts/journey.py`). It also checks that requests without a token, with header-asserted identity or with a tampered signature get `401`, and that Keycloak refuses an `anum-web` authorization request without PKCE.

## Now

Realm as code, `oidc` mode with JWKS rotation, persisted membership resolution, workspace selection by header, workspace invitations and membership management with owner screens in the web and Flutter clients (search, paging, confirmed role changes, resend, last-owner protection), the workspace lifecycle (create, rename, archive, restore) in the API and the web client, and rename, archive and restore in Flutter, the caller's own memberships across the tenant's workspaces (`GET /api/v1/me/workspace-memberships`) listed by the web workspace switcher and the Flutter workspace picker, both checking the membership before switching, fail-fast refusal of development authentication outside local/test, two-factor sign-in on shared Keycloaks (authenticator app or recovery code for every password sign-in, passkeys, enrolment at the next sign-in; [Two-factor sign-in](#two-factor-sign-in)), authorization code + PKCE sign-in with refresh and logout in the web, desktop (shared web build), and Flutter clients, "Continue with Google / Apple" through Keycloak brokering with ANUM's first-login and post-login flows, self sign-up on Keycloak's page with live password rules and privacy-policy acceptance, and pending accounts that wait for an administrator and an invitation ([Google and Apple sign-in](#google-and-apple-sign-in)).

## Later

creating a workspace from Flutter (web only today); Keycloak sign-in in the Kotlin Android app; system-browser plus loopback redirect for desktop (RFC 8252) instead of the in-webview flow; an end-to-end CI journey against a real Keycloak; invitation delivery by email; audit records for refused invitation attempts; per-environment realm configuration with secrets from the deployment secret store; token revocation and session events in the audit log; federation policy; two-factor configuration as part of a declarative realm sync (today a one-time `keycloak-2fa.sh` run or console steps per Keycloak); assigning a pending account to an organization from inside ANUM (today `keycloak-2fa.sh assign-tenant` or the console, because the API's only Keycloak admin client is the optional service account that removes a deleted organisation's accounts, docs/organisation-lifecycle.md); mobile-number sign-up (needs an SMS provider); a verified Apple extension pin in the image.
