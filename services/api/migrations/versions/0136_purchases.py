"""Purchases, deliveries, returns and refunds (expand only). docs/purchases.md#storage.

Seven new tables inside one tenant and workspace:

* ``purchases``: an order (order number, shop, date, currency, tax, shipping, payment method,
  expected delivery, an address **reference** only, warranty and return window). ``ownership`` is
  ``personal`` (only its owner sees it) or ``business`` (every workspace member allowed to use
  purchases sees it). ``total`` is the shop's figure, a detail: **no money is posted here.** The
  purchase points at its canonical Finance transaction through ``finance_links`` (dimension
  ``purchase``, role ``payment``); ``payment`` is a read copy of that link that the link itself
  keeps current in the same transaction (docs/finance-links.md).
* ``purchase_items``: the lines (description, quantity, unit price).
* ``purchase_events``: the append-only history of the purchase (status changes with the state
  before and after, shipments, returns, payments and refunds). A trigger refuses every update.
* ``purchase_shipments``: carriers and tracking numbers typed by the person or shared from the
  iPhone (no carrier integration).
* ``purchase_returns``: a return or refund request (date, reason, items, amount, deadline, pickup,
  courier, tracking, proof as a vault document id, confirmation and refund references, expected
  and actual refund dates, dispute). ``refunds`` is the read copy of its refund links (role
  ``refund`` on the refund's own canonical transaction): a refund is never income and never posts.
* ``purchase_reminders``: each reminder sent, once per due date and step.
* ``purchase_preferences``: one person's reminder switches.

Row level security (forced on every table):

* ``tenant_isolation_<table>``: the usual tenant and workspace predicate.
* Restrictive ``purchase_visible_<table>``: a business purchase and its rows are visible to the
  workspace; a personal one only to its owner (``purchase_owner_id`` on the child rows, copied from
  the purchase and immutable, so no policy looks another table up). The tenant purger and, for the
  scope and date columns, the maintenance role are admitted.
* Restrictive ``purchase_insert_<table>``: a row is written by the person it names.
* ``lifecycle_purge_<table>`` and its grants (0079's pattern).
* Discovery for the daily reminder job, migration 0011's pattern, never BYPASSRLS:
  ``anum_maintenance`` may read the scope and status columns of open purchases.

Expand only: new tables, functions, triggers and policies. The previous release ignores them.
Numbered 0136 and chained after ``0135_vehicles``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0136_purchases"
down_revision = "0135_vehicles"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
PURGER = "anum_tenant_purger"
MAINT = "anum_maintenance"
SCOPE = (
    "tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
    "and workspace_id = nullif(current_setting('anum.workspace_id', true), '')"
)
ME = "nullif(current_setting('anum.user_id', true), '')"
CHILDREN = ("purchase_items", "purchase_events", "purchase_shipments", "purchase_returns", "purchase_reminders")
TABLES = ("purchases", *CHILDREN, "purchase_preferences")
STATUSES = ("ordered", "confirmed", "shipped", "out_for_delivery", "delivered", "partially_delivered", "cancelled", "return_requested",
            "return_approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "disputed")
RETURN_STATUSES = ("requested", "approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "refunded", "rejected",
                   "disputed", "cancelled")
RETURN_REASONS = ("damaged", "defective", "wrong_item", "not_as_described", "missing_parts", "late", "changed_mind", "other")
SHIPMENT_STATUSES = ("pending", "in_transit", "out_for_delivery", "delivered", "failed", "returned_to_sender")
PAYMENT_METHODS = ("card", "apple_pay", "cash", "bank_transfer", "cash_on_delivery", "instalments", "gift_card", "other")
EVENT_KINDS = ("status", "note", "shipment", "return", "payment", "refund", "share")
REMINDER_KINDS = ("delivery_expected", "delivery_late", "return_window", "pickup_due", "refund_overdue", "warranty", "missing_confirmation")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _child() -> list[sa.Column]:
    """A row of one purchase: who wrote it, and the purchase's visibility copied from the purchase."""
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("purchase_id", sa.String(80), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False),
        sa.Column("purchase_owner_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _money(table: str, column: str, *, positive: bool = False) -> sa.CheckConstraint:
    sign = "> 0" if positive else ">= 0"
    return sa.CheckConstraint(f"{column} is null or {column} {sign}", name=f"ck_{table}_{column}")


def _purge_policy(table: str) -> None:
    op.execute(f"grant select (tenant_id, ctid, workspace_id), delete on {table} to {PURGER}")
    op.execute(
        f"create policy lifecycle_purge_{table} on {table} for all to {PURGER} "  # nosec B608: fixed table name
        "using (tenant_id = nullif(current_setting('anum.tenant_id', true), '') "
        "and exists (select 1 from tenant_lifecycle_jobs j where j.tenant_id = "
        f"{table}.tenant_id and j.status = 'purging' "
        "and (j.kind = 'organisation_deletion' or "
        f"(j.kind = 'workspace_deletion' and j.workspace_id = {table}.workspace_id)))) "
        "with check (false)"
    )


def _rls(table: str) -> None:
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(f"create policy tenant_isolation_{table} on {table} using ({SCOPE}) with check ({SCOPE})")
    roles = f"current_user = '{PURGER}'::name or current_user = '{MAINT}'::name"
    if table == "purchases":
        visible = f"ownership = 'business' or owner_user_id = {ME}"
    elif table == "purchase_preferences":
        visible = f"owner_user_id = {ME}"
    else:
        visible = f"visibility = 'business' or purchase_owner_id = {ME}"
    op.execute(f"create policy purchase_visible_{table} on {table} as restrictive for all "
               f"using ({visible} or {roles}) with check ({visible})")
    op.execute(f"create policy purchase_insert_{table} on {table} as restrictive for insert with check (owner_user_id = {ME})")
    _purge_policy(table)


def upgrade() -> None:
    op.create_table(
        "purchases",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("ownership", sa.String(10), nullable=False),
        sa.Column("order_ref", sa.String(80), nullable=True),
        sa.Column("merchant", sa.String(120), nullable=False),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("tax_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("shipping_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("total", sa.Numeric(14, 2), nullable=True),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment", postgresql.JSONB(), nullable=True),
        sa.Column("document_id", sa.String(80), nullable=True),
        sa.Column("expected_delivery_on", sa.Date(), nullable=True),
        sa.Column("delivered_on", sa.Date(), nullable=True),
        sa.Column("address_ref", sa.String(80), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("return_window_until", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ordered"),
        sa.Column("source", sa.String(8), nullable=False, server_default="web"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("archived_at", TS, nullable=True),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchases"),
        sa.CheckConstraint(_in("ownership", ("personal", "business")), name="ck_purchases_ownership"),
        sa.CheckConstraint("length(btrim(merchant)) > 0", name="ck_purchases_merchant"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_purchases_currency"),
        _money("purchases", "tax_amount"),
        _money("purchases", "shipping_amount"),
        _money("purchases", "total"),
        sa.CheckConstraint("payment_method is null or " + _in("payment_method", PAYMENT_METHODS), name="ck_purchases_method"),
        sa.CheckConstraint("payment is null or (jsonb_typeof(payment) = 'object' and payment->>'status' in ('active', 'needs_review', 'void'))",
                           name="ck_purchases_payment"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_purchases_status"),
        sa.CheckConstraint(_in("source", ("web", "iphone")), name="ck_purchases_source"),
        sa.CheckConstraint("version > 0", name="ck_purchases_version"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_purchases_idem"),
    )
    op.create_index("ix_purchases_owner", "purchases", ["tenant_id", "workspace_id", "owner_user_id"])
    op.create_index("ix_purchases_ordered", "purchases", ["tenant_id", "workspace_id", "ordered_on"])
    op.execute("create index ix_purchases_order_ref on purchases (tenant_id, workspace_id, upper(order_ref)) where order_ref is not null")

    op.create_table(
        "purchase_items",
        *_child(),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("sku", sa.String(60), nullable=True),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchase_items"),
        sa.CheckConstraint("length(btrim(description)) > 0", name="ck_purchase_items_description"),
        sa.CheckConstraint("quantity > 0 and quantity < 1000000", name="ck_purchase_items_quantity"),
        _money("purchase_items", "unit_price"),
        sa.CheckConstraint("position between 0 and 500", name="ck_purchase_items_position"),
    )
    op.create_index("ix_purchase_items_purchase", "purchase_items", ["purchase_id", "position"])

    op.create_table(
        "purchase_events",
        *_child(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("subject_id", sa.String(80), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("source", sa.String(8), nullable=False, server_default="web"),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchase_events"),
        sa.CheckConstraint(_in("kind", EVENT_KINDS), name="ck_purchase_events_kind"),
        sa.CheckConstraint("from_status is null or " + _in("from_status", STATUSES), name="ck_purchase_events_from"),
        sa.CheckConstraint("to_status is null or " + _in("to_status", STATUSES), name="ck_purchase_events_to"),
        sa.CheckConstraint(_in("source", ("web", "iphone", "system")), name="ck_purchase_events_source"),
    )
    op.create_index("ix_purchase_events_purchase", "purchase_events", ["purchase_id", "occurred_at", "created_at"])

    op.create_table(
        "purchase_shipments",
        *_child(),
        sa.Column("carrier", sa.String(60), nullable=True),
        sa.Column("tracking_number", sa.String(60), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_transit"),
        sa.Column("shipped_on", sa.Date(), nullable=True),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("delivered_on", sa.Date(), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchase_shipments"),
        sa.CheckConstraint(_in("status", SHIPMENT_STATUSES), name="ck_purchase_shipments_status"),
        sa.CheckConstraint("carrier is not null or tracking_number is not null", name="ck_purchase_shipments_named"),
    )
    op.create_index("ix_purchase_shipments_purchase", "purchase_shipments", ["purchase_id"])
    # One shipment per tracking number on a purchase.
    op.execute("create unique index uq_purchase_shipments_tracking on purchase_shipments (purchase_id, upper(tracking_number)) "
               "where tracking_number is not null")

    op.create_table(
        "purchase_returns",
        *_child(),
        sa.Column("status", sa.String(20), nullable=False, server_default="requested"),
        sa.Column("requested_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("reason_note", sa.String(300), nullable=True),
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("deadline_on", sa.Date(), nullable=True),
        sa.Column("pickup_on", sa.Date(), nullable=True),
        sa.Column("courier", sa.String(60), nullable=True),
        sa.Column("tracking_number", sa.String(60), nullable=True),
        sa.Column("proof_document_id", sa.String(80), nullable=True),
        sa.Column("confirmation_ref", sa.String(80), nullable=True),
        sa.Column("refund_ref", sa.String(80), nullable=True),
        sa.Column("returned_on", sa.Date(), nullable=True),
        sa.Column("expected_refund_on", sa.Date(), nullable=True),
        sa.Column("refunded_on", sa.Date(), nullable=True),
        sa.Column("dispute_note", sa.String(500), nullable=True),
        sa.Column("refunds", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchase_returns"),
        sa.CheckConstraint(_in("status", RETURN_STATUSES), name="ck_purchase_returns_status"),
        sa.CheckConstraint(_in("reason", RETURN_REASONS), name="ck_purchase_returns_reason"),
        sa.CheckConstraint("jsonb_typeof(items) = 'array'", name="ck_purchase_returns_items"),
        sa.CheckConstraint("jsonb_typeof(refunds) = 'array'", name="ck_purchase_returns_refunds"),
        _money("purchase_returns", "amount", positive=True),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_purchase_returns_currency"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_purchase_returns_idem"),
    )
    op.create_index("ix_purchase_returns_purchase", "purchase_returns", ["purchase_id"])

    op.create_table(
        "purchase_reminders",
        *_child(),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("step", sa.SmallInteger(), nullable=False),
        sa.Column("sent_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("purchase_reminders"),
        sa.CheckConstraint(_in("kind", REMINDER_KINDS), name="ck_purchase_reminders_kind"),
        sa.CheckConstraint("step between 0 and 366", name="ck_purchase_reminders_step"),
        sa.UniqueConstraint("purchase_id", "kind", "source_id", "due_on", "step", name="uq_purchase_reminders_once"),
    )

    op.create_table(
        "purchase_preferences",
        *_scope(),
        sa.Column("reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lead_days", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("warranty_days", sa.SmallInteger(), nullable=False, server_default="30"),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="SAR"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_purchase_preferences"),
        _workspace_fk("purchase_preferences"),
        sa.CheckConstraint("lead_days between 1 and 60", name="ck_purchase_preferences_lead"),
        sa.CheckConstraint("warranty_days between 1 and 120", name="ck_purchase_preferences_warranty"),
        sa.CheckConstraint("default_currency ~ '^[A-Z]{3}$'", name="ck_purchase_preferences_currency"),
    )

    # A purchase's ownership and owner, and a row's purchase and copied visibility, never change: the visibility
    # policies rely on them. A child row's copy must match its purchase when it is written.
    op.execute("""
        create function purchases_fixed_owner() returns trigger language plpgsql as $$
        begin
            if new.ownership <> old.ownership or new.owner_user_id <> old.owner_user_id or new.tenant_id <> old.tenant_id
               or new.workspace_id <> old.workspace_id then
                raise exception 'a purchase keeps its ownership and owner' using errcode = '23514';
            end if;
            return new;
        end $$
    """)
    op.execute("create trigger purchases_fixed_owner before update on purchases for each row execute function purchases_fixed_owner()")
    op.execute("""
        create function purchase_child_matches() returns trigger language plpgsql as $$
        declare
            p record;
        begin
            if tg_op = 'UPDATE' then
                if new.purchase_id <> old.purchase_id or new.visibility <> old.visibility or new.purchase_owner_id <> old.purchase_owner_id
                   or new.owner_user_id <> old.owner_user_id then
                    raise exception 'a purchase row keeps its purchase and owner' using errcode = '23514';
                end if;
                return new;
            end if;
            select ownership, owner_user_id, tenant_id, workspace_id into p from purchases where id = new.purchase_id;
            if not found or p.tenant_id <> new.tenant_id or p.workspace_id <> new.workspace_id
               or p.ownership <> new.visibility or p.owner_user_id <> new.purchase_owner_id then
                raise exception 'the row does not match its purchase' using errcode = '23514';
            end if;
            return new;
        end $$
    """)
    for table in CHILDREN:
        op.execute(f"create trigger {table}_matches before insert or update on {table} for each row execute function purchase_child_matches()")

    # The history is append-only: an event is never changed (it leaves only with its purchase or by deletion of the person's data).
    op.execute("""
        create function purchase_events_append_only() returns trigger language plpgsql as $$
        begin
            raise exception 'purchase history is append-only' using errcode = '23514';
        end $$
    """)
    op.execute("create trigger purchase_events_append_only before update on purchase_events for each row execute function purchase_events_append_only()")

    for table in TABLES:
        _rls(table)

    # Discovery for the daily reminder job: scope and status columns of open purchases only, never content, no write.
    op.execute(f"grant select (tenant_id, workspace_id, owner_user_id, archived_at, status) on purchases to {MAINT}")
    op.execute(f"""
        create policy maintenance_purchases_open on purchases for select to {MAINT}
        using (archived_at is null and status not in ('refunded', 'cancelled'))
    """)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on purchases from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists purchase_events_append_only()")
    op.execute("drop function if exists purchase_child_matches()")
    op.execute("drop function if exists purchases_fixed_owner()")
