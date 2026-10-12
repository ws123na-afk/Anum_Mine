# Customer Organisations

The owner runs ANUM for their own company and offers it to customer companies. Each customer company is its own **organisation** (tenant): its data is isolated by row-level security like every tenant's, and the platform operator who onboarded it sees counts and health, never its contents.

Implemented in `services/api/anum_api/organisations.py`, migration `0039_team_work`, `identity.py`/`dependencies.py` (switching), web `apps/web/src/OrganisationsView.tsx` and the switcher in `WorkspaceSwitcher.tsx`, Flutter Settings › Team work › Customer organisations.

## Roles

| Role | Comes from | Grants |
|---|---|---|
| `platform_operator` | Keycloak realm role (in `infra/keycloak/anum-realm.json` and `realm_roles.py ensure-roles`) | `platform:operate`: the platform console below. Applies in the user's **home** tenant only. |
| `org_admin` | Keycloak realm role | Tenant-wide settings of the user's home tenant, now including the organisation's name and logo. |

Both are organisation roles: a workspace role never grants them, and they are dropped when the user acts in another organisation (below). Without the role the console answers `403` naming the realm role to assign; the web hides the console entry unless the token carries it.

## Onboarding a customer company

`POST /api/v1/platform/organizations {tenant_id, display_name, workspace_name, first_admin_email, plan}` (platform operator) creates, in one transaction inside the **new** tenant's RLS context:

1. the tenant (`tenant_id`: 3 to 80 lowercase letters, digits, `_` or `-`; an id that exists answers `409`);
2. its first workspace, `<tenant_id>_main`;
3. its branding (display name);
4. the `platform_organizations` row: operator, plan placeholder (`trial`, `standard`, `enterprise`; billing is not connected), first admin's email;
5. a single-use, email-bound **owner** invitation for the first admin (7 days), returned once.

An audit record is written in the new tenant and in the operator's own workspace. The operator never becomes a member of the customer organisation.

The web console shows the invitation link (`<web app>#invitation=<token>&workspace=<id>&tenant=<tenant_id>`) once, with copy. Opening it as the first admin pre-fills Members › Accept an invitation, which sends `x-tenant-id` for the new organisation; after accepting, **Switch to <organisation>** moves the app there.

With Keycloak, the first admin's sign-in must be allowed in the new organisation first: in the admin console, realm **anum** › Users › the user › Attributes, add the tenant id to `tenant_ids` (or set it as their `tenant_id` if the customer company is their only organisation). Optionally give them `org_admin` if they should manage their organisation's name and logo and tenant-wide policy; that role then applies to their home `tenant_id` only.

| Route | Who | What |
|---|---|---|
| `GET /api/v1/platform/organizations` | platform operator | The organisations this operator onboarded: plan, workspaces, active members, pending invitations, open agent tasks, open and overdue work items, last activity, and health (`setting_up`: nobody joined; `active`: work in the last 14 days; `idle`; `attention`: overdue work, or the first admin's invitation lapsed with nobody joined). |
| `PATCH /api/v1/platform/organizations/{tenant_id} {plan?, display_name?}` | the operator who onboarded it | The plan placeholder and the console's name for it. |
| `POST /api/v1/platform/organizations/{tenant_id}/invitation` | the operator who onboarded it | A new owner invitation for the first admin (the old one is revoked), only while nobody has joined (`409` after). |

### Counts without contents

The console's counts are read through `anum_platform_reader` (migration `0039_team_work`), following the [maintenance role](multi-tenancy.md#maintenance-role) pattern:

- `NOLOGIN`, no `BYPASSRLS`, no write privilege, no `SECURITY DEFINER`.
- Column grants only: `platform_organizations (tenant_id, operator_user_id, display_name, plan, first_workspace_id, created_at)`, `workspace_memberships (tenant_id, active)`, `workspaces (tenant_id)`, `workspace_invitations (tenant_id, status, expires_at)`, `tasks (tenant_id, status, updated_at)`, `work_items (tenant_id, status, due_at, updated_at)`. No title, prompt, name, email, body or file is readable.
- Policies `for select to anum_platform_reader`: `platform_organizations` rows whose `operator_user_id` is `anum.user_id`; on the counted tables, rows of those tenants only.
- The API reads in a separate transaction that starts with `SET LOCAL ROLE anum_platform_reader`, sets `anum.user_id` from the authenticated caller, runs one query and rolls back.
- Grant it to the API login **`WITH INHERIT FALSE, SET TRUE`** (`infra/helm/bootstrap-database.sql`): its policies span tenants and must never apply to the application role's own queries.

`services/api/tests/test_postgres_team_work.py` proves the reader sees only the operator's tenants, cannot read content columns or write, and that the `INHERIT FALSE` grant leaves the app role in one tenant.

## Branding

| Route | Who |
|---|---|
| `GET /api/v1/organization/branding` | every member: `{display_name, has_logo, ...}` (the tenant name until one is set) |
| `GET /api/v1/organization/branding/logo` | every member: the image, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'` |
| `PUT /api/v1/organization/branding {display_name}`, `PUT /api/v1/organization/branding/logo` (raw PNG, JPEG or WebP, at most 256 KiB, checked by magic bytes; SVG is refused), `DELETE .../logo` | `org_admin` |

Stored in `tenant_branding` (forced RLS on `anum.tenant_id`). The web sidebar shows the organisation name and logo; Settings › Workspace has the editor. Changes are audited.

## Switching organisations

People who belong to several organisations (an accountant serving two customers, the owner checking a customer's workspace after being invited) switch between them:

- `GET /api/v1/me/organizations` lists every organisation where the caller has an active membership, with those workspaces and roles. It is read through `anum_tenant_directory` (NOLOGIN, column grants on `workspace_memberships (tenant_id, workspace_id, user_id, role, active)`, `tenants (id, name)`, `workspaces (tenant_id, id, name)`, `tenant_branding (tenant_id, display_name)`; policies admit only the caller's own active memberships and those organisations), in its own rolled-back transaction, granted `WITH INHERIT FALSE`. With OIDC only organisations the token allows are listed.
- **OIDC:** the token's `tenant_id` is the home organisation; a multivalued `tenant_ids` claim (Keycloak user attribute mapped into the access token) lists the others (at most 50, each matching the tenant id pattern). A request names another organisation with `x-tenant-id`; the API accepts it only when the token lists it, and the active membership in that tenant and workspace is still required. Organisation roles do not travel: `org_admin` and `platform_operator` are dropped outside the home tenant.
- **Keycloak:** the realm file (`infra/keycloak/anum-realm.json`) declares the admin-only, multivalued user attribute `tenant_ids` and an `anum-tenant-ids` user-attribute mapper (multivalued, access token) on the ANUM clients. A realm imported before this change has neither (Keycloak imports the file only on first start): in the admin console add the attribute under Realm settings › User profile (multivalued, admin view and edit) and, on each ANUM client, Client scopes › the dedicated scope › Add mapper › By configuration › User Attribute with user attribute and token claim name `tenant_ids`, Multivalued on, Add to access token on.
- **Local development:** `POST /api/v1/auth/local/tenant/switch {tenant_id, workspace_id}` re-issues the development session after checking the membership inside the target tenant's RLS scope.
- **Web:** the top-bar switcher lists "Your other organisations"; switching confirms the membership first, then remembers the choice per home tenant and user in `localStorage` (`anum.tenant.<home>.<user>`, ids only), sends `x-tenant-id`, and remounts the app so nothing from the previous organisation survives.

## Not done yet

- Billing: the plan is a label.
- Suspending or deleting a customer organisation, and exporting its data, from the console.
- Keycloak attribute changes for a new first admin are manual (above); ANUM does not call the Keycloak admin API.
- Flutter switches workspaces but not organisations yet; the platform console on Flutter lists and onboards only (no plan change or reissue).
