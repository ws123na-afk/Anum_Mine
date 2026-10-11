"""E-commerce operations (docs/commerce.md).

Eleven new tables hold the unified commerce model: sales-channel connectors (Amazon,
noon, the own store) with their encrypted credentials, products with tyre attributes,
per-channel listings (price and stock), orders and their lines, customers (encrypted
PII, only when the owner turned buyer details on), returns, fees and settlements,
alerts and per-workspace alert rules. Every table belongs to one tenant and workspace
and is under forced RLS on ``anum.tenant_id`` and ``anum.workspace_id``.

``anum_maintenance`` may discover due connectors for the background sync: the ``id``,
``tenant_id``, ``workspace_id``, ``enabled`` and ``next_sync_at`` columns of enabled
connectors whose ``next_sync_at`` has passed, nothing else (no credentials, no data).
Grant ``anum_maintenance`` to the API login ``WITH INHERIT FALSE, SET TRUE`` (as
``infra/helm/bootstrap-database.sql`` does): the policy admits rows of every tenant.

``notification_preferences`` gains ``commerce_alerts`` (default true).

Expand only: new tables and a defaulted column; the previous release ignores them.

Chained after ``0037_tyre_planning`` (parallel branches, CLAUDE.md).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_commerce"
down_revision = "0037_tyre_planning"
branch_labels = None
depends_on = None

PREDICATE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '')\n"
    "          and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
TABLES = (
    "commerce_connectors",
    "commerce_products",
    "commerce_listings",
    "commerce_orders",
    "commerce_order_lines",
    "commerce_customers",
    "commerce_returns",
    "commerce_fees",
    "commerce_alerts",
    "commerce_settings",
)
TS = sa.DateTime(timezone=True)


def _scope(table: str) -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace"
    )


def _connector_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["connector_id"], ["commerce_connectors.id"], name=f"fk_{table}_connector", ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "commerce_connectors",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_connectors"),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("marketplaces", postgresql.JSONB(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_hint", sa.String(120), nullable=True),
        sa.Column("health", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_sync_at", TS, nullable=True),
        sa.Column("last_success_at", TS, nullable=True),
        sa.Column("next_sync_at", TS, nullable=True),
        sa.Column("sync_counts", postgresql.JSONB(), nullable=False),
        sa.Column("cursors", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("commerce_connectors"),
        sa.CheckConstraint("channel in ('amazon', 'noon', 'own_store')", name="ck_commerce_connectors_channel"),
    )
    op.create_index("ix_commerce_connectors_scope", "commerce_connectors", ["tenant_id", "workspace_id", "created_at"])
    op.create_index("ix_commerce_connectors_due", "commerce_connectors", ["next_sync_at"], postgresql_where=sa.text("enabled"))

    op.create_table(
        "commerce_products",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_products"),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("title_ar", sa.String(500), nullable=True),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("tyre", postgresql.JSONB(), nullable=False),
        sa.Column("tyre_size", sa.String(40), nullable=True),
        sa.Column("cost_price", sa.Double(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("commerce_products"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "sku", name="uq_commerce_products_sku"),
    )

    op.create_table(
        "commerce_listings",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_listings"),
        sa.Column("connector_id", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("marketplace_id", sa.String(40), nullable=False),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("product_type", sa.String(120), nullable=True),
        sa.Column("price", sa.Double(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("fulfillment", sa.String(20), nullable=True),
        sa.Column("buy_box_price", sa.Double(), nullable=True),
        sa.Column("buy_box_winner", sa.Boolean(), nullable=True),
        sa.Column("lowest_competitor_price", sa.Double(), nullable=True),
        sa.Column("competition_checked_at", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("commerce_listings"),
        _connector_fk("commerce_listings"),
        sa.UniqueConstraint("connector_id", "marketplace_id", "sku", name="uq_commerce_listings_offer"),
    )
    op.create_index("ix_commerce_listings_sku", "commerce_listings", ["tenant_id", "workspace_id", "sku"])

    op.create_table(
        "commerce_orders",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_orders"),
        sa.Column("connector_id", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("marketplace_id", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("purchased_at", TS, nullable=False),
        sa.Column("ship_by", TS, nullable=True),
        sa.Column("total_amount", sa.Double(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fulfillment", sa.String(20), nullable=True),
        sa.Column("customer_id", sa.String(80), nullable=True),
        sa.Column("ship_city", sa.String(120), nullable=True),
        sa.Column("ship_country", sa.String(2), nullable=True),
        sa.Column("remote_updated_at", TS, nullable=True),
        sa.Column("synced_at", TS, nullable=False),
        _workspace_fk("commerce_orders"),
        _connector_fk("commerce_orders"),
        sa.UniqueConstraint("connector_id", "external_id", name="uq_commerce_orders_external"),
    )
    op.create_index("ix_commerce_orders_purchased", "commerce_orders", ["tenant_id", "workspace_id", "purchased_at"])
    op.create_index("ix_commerce_orders_status", "commerce_orders", ["tenant_id", "workspace_id", "status"])
    op.create_index("ix_commerce_orders_external", "commerce_orders", ["tenant_id", "workspace_id", "external_id"])

    op.create_table(
        "commerce_order_lines",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_order_lines"),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("external_line_id", sa.String(120), nullable=True),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Double(), nullable=True),
        sa.Column("total", sa.Double(), nullable=True),
        _workspace_fk("commerce_order_lines"),
        sa.ForeignKeyConstraint(["order_id"], ["commerce_orders.id"], name="fk_commerce_order_lines_order", ondelete="CASCADE"),
    )
    op.create_index("ix_commerce_order_lines_order", "commerce_order_lines", ["order_id", "position"])
    op.create_index("ix_commerce_order_lines_sku", "commerce_order_lines", ["tenant_id", "workspace_id", "sku"])

    op.create_table(
        "commerce_customers",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_customers"),
        sa.Column("connector_id", sa.String(80), nullable=False),
        sa.Column("ref_hash", sa.String(64), nullable=False),
        sa.Column("name_ciphertext", sa.Text(), nullable=True),
        sa.Column("email_ciphertext", sa.Text(), nullable=True),
        sa.Column("phone_ciphertext", sa.Text(), nullable=True),
        sa.Column("address_ciphertext", sa.Text(), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("orders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        _workspace_fk("commerce_customers"),
        _connector_fk("commerce_customers"),
        sa.UniqueConstraint("connector_id", "ref_hash", name="uq_commerce_customers_ref"),
    )

    for table, time_column, extra in (
        (
            "commerce_returns",
            "returned_at",
            [
                sa.Column("marketplace_id", sa.String(40), nullable=True),
                sa.Column("order_external_id", sa.String(120), nullable=False),
                sa.Column("sku", sa.String(120), nullable=True),
                sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
                sa.Column("reason", sa.String(200), nullable=True),
                sa.Column("status", sa.String(40), nullable=False),
            ],
        ),
        (
            "commerce_fees",
            "posted_at",
            [
                sa.Column("order_external_id", sa.String(120), nullable=True),
                sa.Column("kind", sa.String(20), nullable=False),
                sa.Column("description", sa.String(200), nullable=False, server_default=""),
            ],
        ),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.String(80), primary_key=True),
            *_scope(table),
            sa.Column("connector_id", sa.String(80), nullable=False),
            sa.Column("channel", sa.String(20), nullable=False),
            sa.Column("external_id", sa.String(200), nullable=False),
            *extra,
            sa.Column("amount", sa.Double(), nullable=True if table == "commerce_returns" else False),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column(time_column, TS, nullable=False),
            _workspace_fk(table),
            _connector_fk(table),
            sa.UniqueConstraint("connector_id", "external_id", name=f"uq_{table}_external"),
        )
        op.create_index(f"ix_{table}_time", table, ["tenant_id", "workspace_id", time_column])

    op.create_table(
        "commerce_alerts",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope("commerce_alerts"),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.String(120), nullable=True),
        sa.Column("channel", sa.String(20), nullable=True),
        sa.Column("connector_id", sa.String(80), nullable=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("acknowledged_by", sa.String(160), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("commerce_alerts"),
        _connector_fk("commerce_alerts"),
        sa.CheckConstraint("status in ('open', 'acknowledged', 'resolved')", name="ck_commerce_alerts_status"),
    )
    # One unresolved alert per subject.
    op.create_index(
        "uq_commerce_alerts_active",
        "commerce_alerts",
        ["tenant_id", "workspace_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.create_index("ix_commerce_alerts_list", "commerce_alerts", ["tenant_id", "workspace_id", "status", "updated_at"])

    op.create_table(
        "commerce_settings",
        *_scope("commerce_settings"),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False),
        sa.Column("handling_hours", sa.Integer(), nullable=False),
        sa.Column("return_spike_count", sa.Integer(), nullable=False),
        sa.Column("return_spike_ratio", sa.Double(), nullable=False),
        sa.Column("price_alert_percent", sa.Double(), nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", name="pk_commerce_settings"),
        _workspace_fk("commerce_settings"),
    )

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

    # Discovery for the background sync (anum_api/commerce/scheduler.py): due connectors only.
    op.execute(
        "grant select (id, tenant_id, workspace_id, enabled, next_sync_at) on commerce_connectors to anum_maintenance"
    )
    op.execute(
        """
        create policy maintenance_due_commerce_connectors on commerce_connectors
        for select to anum_maintenance
        using (enabled and next_sync_at <= now())
        """
    )

    op.add_column(
        "notification_preferences",
        sa.Column("commerce_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "commerce_alerts")
    op.execute("drop policy if exists maintenance_due_commerce_connectors on commerce_connectors")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on commerce_connectors from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
