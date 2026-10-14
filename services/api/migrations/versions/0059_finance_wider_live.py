"""Finance stage 9: wider live use, the framework only, shipped off (docs/finance.md#wider-live).

* ``finance_exec_settings.wider`` (JSONB, default ``{}``): the person's tier above the stage 8
  limits, the action types allowed, and whether widening is frozen after an incident.
* ``finance_live_widenings``: proposals to move up exactly one tier (by the person or an
  assistant), each bound to the payload hash the owner approves once before it expires;
  an approved one takes effect only after the cooling-off (``effective_at``). Database
  checks: one tier at a time, only the owner decides, an approved row has its decision and
  effective time.
* ``finance_live_incidents``: a kill switch, a failed execution or repeated Guardian
  denials, with the tier before and after the automatic step down.

Both tables belong to one tenant and workspace (foreign key to ``workspaces``) under forced
RLS with ``tenant_isolation_<table>`` and a restrictive ``owner_only_<table>`` policy, like
0056 to 0058. No role is granted anything new.

Expand only: one column with a default and new tables. The previous release ignores them.

Chained after ``0058_finance_paper_exec``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0059_finance_wider_live"
down_revision = "0058_finance_paper_exec"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
OWNER_PREDICATE = "owner_user_id = nullif(current_setting('anum.user_id', true), '')"
TABLES = ("finance_live_widenings", "finance_live_incidents")
TS = sa.DateTime(timezone=True)


def _owned() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def upgrade() -> None:
    op.add_column(
        "finance_exec_settings", sa.Column("wider", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))
    )

    op.create_table(
        "finance_live_widenings",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("base_tier", sa.Integer(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column("kinds", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("proposed_by", sa.String(10), nullable=False),
        sa.Column("proposer", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("effective_at", TS, nullable=True),
        _workspace_fk("finance_live_widenings"),
        sa.CheckConstraint("base_tier >= 0 and tier = base_tier + 1 and tier <= 5", name="ck_finance_live_widenings_one_tier"),
        sa.CheckConstraint("proposed_by in ('person', 'agent')", name="ck_finance_live_widenings_by"),
        sa.CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'expired', 'superseded', 'cancelled')", name="ck_finance_live_widenings_status"
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_live_widenings_hash"),
        # Only the person who owns it decides; an approval records when it takes effect.
        sa.CheckConstraint("decided_by is null or decided_by = owner_user_id", name="ck_finance_live_widenings_decider"),
        sa.CheckConstraint(
            "status <> 'approved' or (decided_by is not null and decided_at is not null and effective_at is not null)",
            name="ck_finance_live_widenings_approved",
        ),
    )
    op.create_index(
        "ix_finance_live_widenings_owner", "finance_live_widenings", ["tenant_id", "workspace_id", "owner_user_id", "status", "created_at"]
    )

    op.create_table(
        "finance_live_incidents",
        sa.Column("id", sa.String(80), primary_key=True),
        *_owned(),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("tier_before", sa.Integer(), nullable=False),
        sa.Column("tier_after", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", TS, nullable=True),
        _workspace_fk("finance_live_incidents"),
        sa.CheckConstraint(
            "code in ('kill_switch_person', 'kill_switch_global', 'failed_execution', 'repeated_denials')", name="ck_finance_live_incidents_code"
        ),
        # An incident never raises the tier.
        sa.CheckConstraint("tier_after >= 0 and tier_after <= tier_before", name="ck_finance_live_incidents_step_down"),
    )
    op.create_index("ix_finance_live_incidents_owner", "finance_live_incidents", ["tenant_id", "workspace_id", "owner_user_id", "created_at"])

    for table in TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
        op.execute(
            f"""
            create policy tenant_isolation_{table} on {table}
            using (
              {PREDICATE}
            )
            with check (
              {PREDICATE}
            )
            """
        )
        op.execute(
            f"""
            create policy owner_only_{table} on {table}
            as restrictive
            using (
              {OWNER_PREDICATE}
            )
            with check (
              {OWNER_PREDICATE}
            )
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("finance_exec_settings", "wider")
