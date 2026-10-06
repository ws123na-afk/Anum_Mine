"""Approval chains and the caller's own membership directory.

Approval chains (docs/approvals-and-risk.md#approval-chains):

* ``approval_approvers``: one row per distinct user who approved an approval. An
  approval whose organization approval rules require ``minimum_approvers`` above 2
  turns ``approved`` only when enough rows exist. Forced RLS on the tenant and
  workspace, like ``approvals``; no role bypasses it.
* ``uq_approvals_scope_id`` on ``approvals (tenant_id, workspace_id, id)`` so the new
  table's foreign key carries the scope (a row cannot point at another workspace's
  approval).

Membership directory (docs/identity.md#my-workspaces):

* ``anum_membership_reader``, a NOLOGIN role that may read only the columns needed to
  list *one user's* active memberships within *one tenant*: ``tenant_id``,
  ``workspace_id``, ``user_id``, ``role`` and ``active`` of ``workspace_memberships``
  and ``tenant_id``, ``id`` and ``name`` of ``workspaces``. Its policies admit only rows
  of the tenant in ``anum.tenant_id`` and the user in ``anum.user_id`` (and the
  workspaces that user is an active member of). It cannot write anything.
  The API uses it in a short read-only transaction (``SET LOCAL ROLE``), the same way
  as ``anum_maintenance``. Deployments grant it to the API login ``WITH INHERIT
  FALSE``, so its policies never apply to ordinary application-role queries.

Expand only: the previous release ignores the new table, constraint and role.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_approvers_and_directory"
down_revision = "0012_approval_policy"
branch_labels = None
depends_on = None

READER_ROLE = "anum_membership_reader"

WORKSPACE_PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)


def upgrade() -> None:
    # Approval chains ------------------------------------------------------------------
    op.create_unique_constraint(
        "uq_approvals_scope_id", "approvals", ["tenant_id", "workspace_id", "id"]
    )
    op.create_table(
        "approval_approvers",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("approval_id", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(160), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "workspace_id", "approval_id", "user_id", name="pk_approval_approvers"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "approval_id"],
            ["approvals.tenant_id", "approvals.workspace_id", "approvals.id"],
            name="fk_approval_approvers_approval",
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'", name="ck_approval_approvers_payload_hash"
        ),
    )
    op.execute("alter table approval_approvers enable row level security")
    op.execute("alter table approval_approvers force row level security")
    op.execute(
        f"""
        create policy tenant_isolation_approval_approvers on approval_approvers
        using (
          {WORKSPACE_PREDICATE}
        )
        with check (
          {WORKSPACE_PREDICATE}
        )
        """
    )

    # Membership directory -------------------------------------------------------------
    # NOLOGIN; when a DBA created it already (infra/helm/bootstrap-database.sql), no-op.
    op.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'anum_membership_reader') then
            create role anum_membership_reader nologin;
          end if;
        end
        $$
        """
    )
    op.execute("grant usage on schema public to anum_membership_reader")
    # Column-level, read-only grants. The policies below decide which rows.
    op.execute(
        "grant select (tenant_id, workspace_id, user_id, role, active) "
        "on workspace_memberships to anum_membership_reader"
    )
    op.execute("grant select (tenant_id, id, name) on workspaces to anum_membership_reader")
    op.execute(
        """
        create policy membership_directory_own_rows on workspace_memberships
        for select to anum_membership_reader
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and user_id = nullif(current_setting('anum.user_id', true), '')
          and active
        )
        """
    )
    # The subquery runs as anum_membership_reader too, so it sees only the rows the
    # policy above admits; it repeats the user and active checks so the result does not
    # depend on whether anum.workspace_id happens to be set.
    op.execute(
        """
        create policy membership_directory_workspaces on workspaces
        for select to anum_membership_reader
        using (
          tenant_id = nullif(current_setting('anum.tenant_id', true), '')
          and exists (
            select 1 from workspace_memberships m
            where m.tenant_id = workspaces.tenant_id
              and m.workspace_id = workspaces.id
              and m.user_id = nullif(current_setting('anum.user_id', true), '')
              and m.active
          )
        )
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists membership_directory_workspaces on workspaces")
    op.execute("drop policy if exists membership_directory_own_rows on workspace_memberships")
    # The role is cluster-wide and may be granted to logins or used by other databases,
    # so it is left in place without privileges in this database.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_membership_reader') then
            revoke all on workspaces from anum_membership_reader;
            revoke all on workspace_memberships from anum_membership_reader;
            revoke usage on schema public from anum_membership_reader;
          end if;
        end
        $$
        """
    )
    op.drop_table("approval_approvers")
    op.drop_constraint("uq_approvals_scope_id", "approvals", type_="unique")
