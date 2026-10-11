"""Anthropic as a workspace model provider, with per-workspace model routing
(docs/model-gateway.md#per-workspace-model-configuration).

* ``workspace_model_configs.provider`` may now be ``anthropic``; like
  ``openai_compatible`` it needs a stored (encrypted) API key.
* Four nullable routing columns mirror the server's ``ANUM_MODEL_FAST_NAME``,
  ``ANUM_MODEL_CAPABLE_NAME``, ``ANUM_MODEL_FAST_EFFORT`` and
  ``ANUM_MODEL_CAPABLE_EFFORT``: ``fast_model``, ``capable_model``, ``fast_effort``,
  ``capable_effort``. Null means "not set" (the saved ``model`` serves that route; no
  effort is sent).

Expand only: nullable columns and wider check constraints. The previous release ignores
the new columns, and it already builds the ``anthropic`` gateway the server default
uses, so an ``anthropic`` row saved by this release still answers there (with its one
saved model). The existing RLS policy covers the new columns.

Numbered 0023 on a parallel branch: re-chain ``down_revision`` onto the newest
migration when this lands (CLAUDE.md, parallel migrations).
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_model_config_routes"
down_revision = "0022_developer_requests"
branch_labels = None
depends_on = None

TABLE = "workspace_model_configs"
EFFORTS = "('low', 'medium', 'high', 'xhigh', 'max')"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("fast_model", sa.String(160), nullable=True))
    op.add_column(TABLE, sa.Column("capable_model", sa.String(160), nullable=True))
    op.add_column(TABLE, sa.Column("fast_effort", sa.String(10), nullable=True))
    op.add_column(TABLE, sa.Column("capable_effort", sa.String(10), nullable=True))
    op.drop_constraint("ck_workspace_model_configs_provider", TABLE, type_="check")
    op.create_check_constraint(
        "ck_workspace_model_configs_provider",
        TABLE,
        "provider in ('mock', 'openai_compatible', 'ollama', 'anthropic')",
    )
    op.drop_constraint("ck_workspace_model_configs_credential", TABLE, type_="check")
    op.create_check_constraint(
        "ck_workspace_model_configs_credential",
        TABLE,
        "provider not in ('openai_compatible', 'anthropic') or api_key_ciphertext is not null",
    )
    op.create_check_constraint(
        "ck_workspace_model_configs_effort",
        TABLE,
        f"(fast_effort is null or fast_effort in {EFFORTS}) "
        f"and (capable_effort is null or capable_effort in {EFFORTS})",
    )


def downgrade() -> None:
    # Rows are hidden from the migration role by forced RLS, so existing ``anthropic``
    # rows cannot be cleaned up here: the narrower constraints come back NOT VALID
    # (enforced for new writes, existing rows left alone).
    op.drop_constraint("ck_workspace_model_configs_effort", TABLE, type_="check")
    op.drop_constraint("ck_workspace_model_configs_credential", TABLE, type_="check")
    op.execute(
        f"alter table {TABLE} add constraint ck_workspace_model_configs_credential "
        "check (provider <> 'openai_compatible' or api_key_ciphertext is not null)"
    )
    op.drop_constraint("ck_workspace_model_configs_provider", TABLE, type_="check")
    op.execute(
        f"alter table {TABLE} add constraint ck_workspace_model_configs_provider "
        "check (provider in ('mock', 'openai_compatible', 'ollama')) not valid"
    )
    op.drop_column(TABLE, "capable_effort")
    op.drop_column(TABLE, "fast_effort")
    op.drop_column(TABLE, "capable_model")
    op.drop_column(TABLE, "fast_model")
