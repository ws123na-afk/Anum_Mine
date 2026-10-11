"""Developer-mode projects: target repositories per workspace (docs/developer-mode.md#projects).

``developer_projects`` holds the GitHub repositories a workspace's owners let the developer
change: name, ``github_owner``/``github_repo``, the branch requests build on, a short
description that routes spoken and typed requests, an enabled switch, extra protected
paths, the workflow file, the token source and, for a per-project token, its Fernet
ciphertext (``token_ciphertext``; never a plaintext token). Rows belong to a tenant and
workspace under forced RLS. Names and repositories are unique per workspace
(case-insensitive).

``anum_maintenance`` may discover which projects hold a ciphertext (``id``, ``tenant_id``
and ``workspace_id`` only, rows with a ciphertext only) so ``python -m
anum_api.rotate_secrets`` re-encrypts them inside each tenant's RLS context, as it does
for model provider keys (migration 0011). Grant ``anum_maintenance`` to the API login
``WITH INHERIT FALSE, SET TRUE`` as before.

``developer_requests`` gains ``project_id``, ``project_name`` and ``repository``
(nullable; the previous release never writes them, and its rows resolve to the
pre-projects repository).

Data migration: when this runs with ``ANUM_DEVELOPER_GITHUB_REPO`` and
``ANUM_DEVELOPER_TENANT_ID`` in its environment (the API's environment), that repository
becomes the first project of every workspace of that tenant (only
``ANUM_DEVELOPER_WORKSPACE_ID`` when it is set), using the API's
``ANUM_DEVELOPER_GITHUB_TOKEN`` (token source ``environment``: no token is read or
written here), and that workspace's existing requests are attached to it. It runs inside
each tenant and workspace's RLS context (``set_config`` in the migration transaction), so
RLS stays forced. Without those settings nothing is inserted and the owner adds the
project in the Developer view. Re-running inserts nothing twice.

"One queued or coding request" becomes one per project: the partial unique index
``uq_developer_requests_one_active_project`` on ``(tenant_id, workspace_id,
coalesce(project_id, ''))`` is built ``CONCURRENTLY``, then the per-workspace
``uq_developer_requests_one_active`` (0023) is dropped ``CONCURRENTLY``. The previous
release keeps working against this schema: it still admits under the same advisory lock
and its own check stays per workspace (stricter), so it never relied on the dropped index
to function. Everything else is expand only.

Chained after ``0030_integration_health``.
"""

import hashlib
import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_developer_projects"
down_revision = "0030_integration_health"
branch_labels = None
depends_on = None

TABLE = "developer_projects"
REQUESTS = "developer_requests"
NEW_INDEX = "uq_developer_requests_one_active_project"
OLD_INDEX = "uq_developer_requests_one_active"
ACTIVE = "('queued', 'coding')"
PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}\.ya?ml$")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("github_owner", sa.String(100), nullable=False),
        sa.Column("github_repo", sa.String(100), nullable=False),
        sa.Column("default_branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("protected_paths", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("workflow_file", sa.String(110), nullable=False, server_default="anum-developer.yml"),
        sa.Column("token_source", sa.String(20), nullable=False, server_default="project"),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("token_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check", postgresql.JSONB(), nullable=True),
        sa.Column("install_pr_number", sa.Integer(), nullable=True),
        sa.Column("install_pr_url", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_developer_projects_workspace",
        ),
        sa.CheckConstraint("token_source in ('project', 'environment')", name="ck_developer_projects_token_source"),
    )
    op.execute(f"create unique index uq_developer_projects_name on {TABLE} (tenant_id, workspace_id, lower(name))")
    op.execute(
        f"create unique index uq_developer_projects_repository on {TABLE} "
        "(tenant_id, workspace_id, lower(github_owner), lower(github_repo))"
    )
    op.execute(f"alter table {TABLE} enable row level security")
    op.execute(f"alter table {TABLE} force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_{TABLE} on {TABLE}
        using (
          {PREDICATE}
        )
        with check (
          {PREDICATE}
        )
        """
    )
    # Key rotation discovers projects with a stored token: ids only, never the ciphertext.
    op.execute(f"grant select (id, tenant_id, workspace_id) on {TABLE} to anum_maintenance")
    # Permissive policies are OR-ed per role, so this widens access for this role only.
    op.execute(
        f"""
        create policy maintenance_encrypted_project_tokens on {TABLE}
        for select to anum_maintenance
        using (token_ciphertext is not null)
        """
    )

    op.add_column(REQUESTS, sa.Column("project_id", sa.String(80), nullable=True))
    op.add_column(REQUESTS, sa.Column("project_name", sa.String(120), nullable=True))
    op.add_column(REQUESTS, sa.Column("repository", sa.String(200), nullable=True))

    _carry_over_repository()

    with op.get_context().autocommit_block():
        _drop_invalid(NEW_INDEX)
        op.execute(
            f"create unique index concurrently if not exists {NEW_INDEX} "
            f"on {REQUESTS} (tenant_id, workspace_id, coalesce(project_id, '')) where status in {ACTIVE}"
        )
        op.execute(f"drop index concurrently if exists {OLD_INDEX}")


def _drop_invalid(index: str) -> None:
    """An interrupted concurrent build leaves an invalid index behind; drop it first."""
    invalid = not op.get_context().as_sql and op.get_bind().execute(
        sa.text("select not indisvalid from pg_index where indexrelid = to_regclass(:name)"),
        {"name": index},
    ).scalar()
    if invalid:
        op.execute(f"drop index concurrently if exists {index}")


def _carry_over_repository() -> None:
    """ANUM_DEVELOPER_GITHUB_REPO becomes the first project (see the module docstring)."""
    repository = os.environ.get("ANUM_DEVELOPER_GITHUB_REPO", "").strip()
    tenant_id = os.environ.get("ANUM_DEVELOPER_TENANT_ID", "").strip()
    only_workspace = os.environ.get("ANUM_DEVELOPER_WORKSPACE_ID", "").strip()
    if op.get_context().as_sql or not tenant_id or not REPOSITORY_PATTERN.match(repository):
        return
    branch = os.environ.get("ANUM_DEVELOPER_BASE_BRANCH", "").strip() or "main"
    workflow = os.environ.get("ANUM_DEVELOPER_WORKFLOW_FILE", "").strip() or "anum-developer.yml"
    branch = branch if BRANCH_PATTERN.match(branch) and ".." not in branch else "main"
    workflow = workflow if WORKFLOW_PATTERN.match(workflow) else "anum-developer.yml"
    owner, name = repository.split("/", 1)
    bind = op.get_bind()
    # Act inside the tenant's RLS context, as the API would; RLS stays forced.
    bind.execute(sa.text("select set_config('anum.tenant_id', :tenant, true)"), {"tenant": tenant_id})
    workspaces = list(
        bind.execute(sa.text("select id from workspaces where tenant_id = :tenant order by id"), {"tenant": tenant_id}).scalars()
    )
    if only_workspace:
        workspaces = [workspace for workspace in workspaces if workspace == only_workspace]
    for workspace_id in workspaces:
        bind.execute(sa.text("select set_config('anum.workspace_id', :workspace, true)"), {"workspace": workspace_id})
        seed = f"{tenant_id}\x1f{workspace_id}\x1f{repository.lower()}".encode()
        scope = {"tenant": tenant_id, "workspace": workspace_id, "owner": owner, "repo": name}
        bind.execute(
            sa.text(
                """
                insert into developer_projects
                  (id, tenant_id, workspace_id, name, github_owner, github_repo, default_branch, workflow_file,
                   token_source, created_by)
                values (:id, :tenant, :workspace, :name, :owner, :repo, :branch, :workflow, 'environment', 'system:migration')
                -- A project with this repository or name already exists (a re-run): keep it.
                on conflict do nothing
                """
            ),
            {
                **scope,
                "id": "devproj_" + hashlib.sha256(seed).hexdigest()[:32],
                "name": name[:60],
                "branch": branch,
                "workflow": workflow,
            },
        )
        project = bind.execute(
            sa.text(
                "select id, name from developer_projects where tenant_id = :tenant and workspace_id = :workspace "
                "and lower(github_owner) = lower(:owner) and lower(github_repo) = lower(:repo)"
            ),
            scope,
        ).first()
        if project is None:
            continue
        bind.execute(
            sa.text(
                "update developer_requests set project_id = :id, project_name = :name, repository = :repository "
                "where tenant_id = :tenant and workspace_id = :workspace and project_id is null"
            ),
            {"id": project.id, "name": project.name, "repository": repository, **scope},
        )
    bind.execute(sa.text("select set_config('anum.workspace_id', '', true), set_config('anum.tenant_id', '', true)"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"drop index concurrently if exists {NEW_INDEX}")
    # 0023's per-workspace rule again: keep each workspace's newest active request.
    op.execute(f"alter table {REQUESTS} no force row level security")
    op.execute(
        sa.text(
            """
            update developer_requests as stale
               set status = 'failed', failure_reason = :reason, updated_at = now()
              from (
                select id,
                       row_number() over (
                         partition by tenant_id, workspace_id order by created_at desc, id desc
                       ) as position
                  from developer_requests
                 where status in ('queued', 'coding')
              ) as ranked
             where stale.id = ranked.id and ranked.position > 1
            """
        ).bindparams(reason="Not started: another request for this workspace was already queued or coding.")
    )
    op.execute(f"alter table {REQUESTS} force row level security")
    op.drop_column(REQUESTS, "repository")
    op.drop_column(REQUESTS, "project_name")
    op.drop_column(REQUESTS, "project_id")
    op.execute(f"drop policy if exists maintenance_encrypted_project_tokens on {TABLE}")
    op.drop_table(TABLE)
    with op.get_context().autocommit_block():
        op.execute(
            f"create unique index concurrently if not exists {OLD_INDEX} "
            f"on {REQUESTS} (tenant_id, workspace_id) where status in {ACTIVE}"
        )
