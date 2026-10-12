# Skills

Skills are reusable capability packages that guide ANUM agents through a class of work. A skill may include instructions, schemas, example workflows, tool requirements, validation rules, and risk metadata. Skills should improve reliability without bypassing runtime policy.

## Skill Contents

A skill should be declarative where possible. It can define purpose, trigger conditions, required tools, input schema, output schema, allowed memory scopes, approval requirements, and test cases. Implementation code should live in normal application packages or tool adapters, not hidden inside a skill document.

## Loading and Selection

The runtime should select skills based on task intent, tenant policy, user permissions, and tool availability. Skill selection should be recorded in the task trace so users and developers can understand why an agent behaved a certain way.

## Safety

Skills are not trusted code by default. They should not grant access to tools or memory. They can request capabilities, but the runtime and authorization layer decide what is actually available. Skill updates should be versioned and auditable.

## Repository Placement

The monorepo should eventually include a `skills/` workspace for first-party skill definitions, examples, and tests. Skill packages should avoid secrets and environment-specific configuration.

## Implemented Control Plane

The Phase 2 API provides immutable semantic versions, tenant-owned publishing, workspace installations, declared and administrator-approved tool grants, and execution-time resolution. Resolution intersects the installed grants with runtime tool availability and rejects skills above the caller's risk ceiling. Skill instructions never grant tools by themselves.

Endpoints are under `/api/v1/skills`: `POST /versions`, `GET /versions` (`q` matches skill id, name or description; `limit` up to 500 and `offset` page it), `GET /{skill_id}` (details), `POST /installations`, `GET /installations`, `PATCH|DELETE /installations/{skill_id}`, `POST /installations/{skill_id}/rollback`, and `POST /resolve`. A published version belongs to the tenant and can be installed in every workspace, so `POST /versions` needs the organization administrator role `org_admin` (`organization_policy:manage`, [Governance](governance-and-scale.md#who-may-change-what)); a workspace owner without it gets `403` and nothing is published. Installing, enabling, disabling and uninstalling stay workspace-owner actions (`policy:manage`), and every member can read versions, installations and details.

- **Details (`GET /skills/{skill_id}`, any member).** Versions newest first, this workspace's installation, and `tools`: each tool the shown version requires, as this runtime treats it: whether it is registered here, its description, `risk_level`, `approval` (`always` for high risk, `policy` for medium when the workspace approval policy covers it, `rule` for low unless an organization approval rule or policy pack asks, `blocked`, or `unavailable` when the deployment does not have the tool), the integration host it contacts, and whether the installation granted it. `?version=` describes another version (what installing it would grant). `history` is the skill's audit trail in this workspace (newest first, at most 50), and `can_manage` / `can_rollback` are the caller's rights.
- **Rollback (`POST /skills/installations/{skill_id}/rollback {"version"}`, organization administrators: `organization_policy:manage`).** Moves the installation to an earlier published version (`422` for the same or a newer one: install that instead). Grants never widen: the installation keeps only the tools it had already granted that the earlier version also declares. The enabled switch is kept.
- **Audit.** Publishing (`skill.published`), installing (`skill.installed`, with `previous_version` when it replaced one), enabling, disabling, uninstalling and rolling back are each written to `audit_records` in the same transaction as the change, target `skill:{skill_id}`; the audit export includes them.
- **Clients.** The web Skills page (`apps/web/src/SkillsView.tsx`) lists one card per skill (installed version, tools, risk, state, "upgrade available"), with search over id, name, description and tools, an All / Installed / Not installed filter and "Show more" paging (12 at a time). Details open in a dialog with the tools table, versions, history and, for organization administrators, "Roll back to" with confirmation; owners install (a review dialog lists each tool's risk and approval before granting), upgrade, enable, disable and uninstall. Organization administrators publish versions from a form that validates like the API. Members and non-admin owners see why controls are missing. Flutter Resources > Skills installs, enables, disables and uninstalls (with confirmation), shows the installed version, and says who can act when the API refuses. Tests: `services/api/tests/test_skill_catalog.py`, `test_postgres_integration_state.py`, `apps/web/test/catalog.test.ts`, `apps/web/e2e/skills.spec.ts`, `apps/mobile/test/skills_resources_test.dart`.

With `ANUM_REPOSITORY_BACKEND=postgresql` versions live in `skill_versions` (tenant-level, unique per tenant, skill and version) and installations in `skill_installations` (one per workspace and skill, referencing the installed version), both under RLS ([Multi-tenancy](multi-tenancy.md#control-plane-stores)).

## Remaining Distribution Work

Marketplace signing, organization-wide promotion workflows, and quality scoring remain part of the distribution and production-hardening work. A marketplace package of kind `skill` is not linked to the skill registry: installing it records the workspace's adoption only. Flutter has no skill details, rollback or publishing screen yet (web only).
