"""Vehicle, fuel and mileage (expand only). docs/vehicles.md#storage.

Seven new tables inside one tenant and workspace:

* ``vehicles``: the vehicle master (registration, VIN, make, model, year, type, country SA or
  AE, currency, ownership, fuel type, tank size, acquisition, purchase price and depreciation
  terms, insurance, registration and inspection expiry). ``ownership`` is ``personal`` (only its
  owner sees it) or ``business`` (every workspace member allowed to use vehicles sees it).
* ``vehicle_odometer_readings``: every odometer value with its time and source. The odometer
  never goes down: a trigger refuses a reading lower than the one before it, or higher than the
  one after it, unless the row is a **correction** (a reason is required). Readings are never
  updated; a reading leaves only with the record it came from.
* ``vehicle_fuel_entries``, ``vehicle_trips`` and ``vehicle_service_records``: quantities,
  odometer values and details. **No money is posted here.** A fuel entry or a service record
  points at a canonical Finance transaction through ``finance_links`` (dimension ``vehicle``);
  ``payment`` is a read copy of that link (status, amount, currency, who paid) that the link
  itself keeps current in the same transaction (docs/finance-links.md), so members who may not
  read the payer's ledger still see the vehicle's costs.
* ``vehicle_reminders``: each expiry or service reminder sent, once per due date and step.
* ``vehicle_preferences``: one person's switches (store trip places, default vehicle, reminder days).

Row level security (forced on every table):

* ``tenant_isolation_<table>``: the usual tenant and workspace predicate.
* Restrictive ``vehicle_visible_<table>``: a business vehicle and its records are visible to the
  workspace; a personal one only to its owner (``vehicle_owner_id`` on the child rows, copied from
  the vehicle and immutable, so no policy has to look another table up). The tenant purger and,
  for reading scope columns, the maintenance role are admitted.
* Restrictive ``vehicle_insert_<table>``: a row is written by the person it names
  (``owner_user_id`` is the caller).
* ``lifecycle_purge_<table>`` and its grants (0079's pattern).
* Discovery for the daily reminder job, migration 0011's pattern, never BYPASSRLS:
  ``anum_maintenance`` may read the scope and date columns of vehicles and service records.

Expand only: new tables, functions, triggers and policies. The previous release ignores them.
Numbered 0135 and chained after ``0134_finance_links``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0135_vehicles"
down_revision = "0134_finance_links"
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
CHILDREN = ("vehicle_odometer_readings", "vehicle_fuel_entries", "vehicle_trips", "vehicle_service_records", "vehicle_reminders")
TABLES = ("vehicles", *CHILDREN, "vehicle_preferences")
FUEL_TYPES = ("petrol_91", "petrol_95", "petrol_98", "diesel", "electric", "hybrid", "lpg", "other")
VEHICLE_TYPES = ("car", "suv", "pickup", "van", "truck", "motorcycle", "bus", "other")
SERVICE_KINDS = ("maintenance", "oil", "tyres", "battery", "repair", "inspection", "insurance", "registration", "fine", "toll", "parking",
                 "warranty", "wash", "other")
PAYMENT_METHODS = ("card", "apple_pay", "cash", "fleet_card", "bank_transfer", "other")
READING_SOURCES = ("manual", "fuel", "trip_start", "trip_end", "service", "device", "correction")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in (" + ", ".join(f"'{value}'" for value in values) + ")"


def _scope() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.String(160), nullable=False),
    ]


def _child() -> list[sa.Column]:
    """A record of one vehicle: who wrote it, and the vehicle's visibility copied from the vehicle."""
    return [
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("vehicle_id", sa.String(80), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visibility", sa.String(10), nullable=False),
        sa.Column("vehicle_owner_id", sa.String(160), nullable=False),
    ]


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], name=f"fk_{table}_workspace")


def _payment_check(table: str, column: str = "payment") -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} is null or (jsonb_typeof({column}) = 'object' and {column}->>'status' in ('active', 'needs_review', 'void'))",
        name=f"ck_{table}_{column}")


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
    if table == "vehicles":
        visible = f"ownership = 'business' or owner_user_id = {ME}"
    elif table == "vehicle_preferences":
        visible = f"owner_user_id = {ME}"
    else:
        visible = f"visibility = 'business' or vehicle_owner_id = {ME}"
    op.execute(f"create policy vehicle_visible_{table} on {table} as restrictive for all "
               f"using ({visible} or {roles}) with check ({visible})")
    op.execute(f"create policy vehicle_insert_{table} on {table} as restrictive for insert with check (owner_user_id = {ME})")
    _purge_policy(table)


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.String(80), primary_key=True),
        *_scope(),
        sa.Column("ownership", sa.String(10), nullable=False),
        sa.Column("registration", sa.String(20), nullable=False),
        sa.Column("vin", sa.String(17), nullable=True),
        sa.Column("nickname", sa.String(60), nullable=True),
        sa.Column("make", sa.String(60), nullable=True),
        sa.Column("model", sa.String(60), nullable=True),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.Column("vehicle_type", sa.String(12), nullable=False, server_default="car"),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("fuel_type", sa.String(12), nullable=False),
        sa.Column("tank_litres", sa.Numeric(7, 2), nullable=True),
        sa.Column("acquisition_date", sa.Date(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("residual_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("useful_life_months", sa.SmallInteger(), nullable=True),
        sa.Column("purchase_payment", postgresql.JSONB(), nullable=True),
        sa.Column("insurance_expires_on", sa.Date(), nullable=True),
        sa.Column("registration_expires_on", sa.Date(), nullable=True),
        sa.Column("inspection_expires_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("archived_at", TS, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicles"),
        sa.CheckConstraint(_in("ownership", ("personal", "business")), name="ck_vehicles_ownership"),
        sa.CheckConstraint("length(btrim(registration)) > 0", name="ck_vehicles_registration"),
        sa.CheckConstraint("vin is null or vin ~ '^[A-HJ-NPR-Z0-9]{11,17}$'", name="ck_vehicles_vin"),
        sa.CheckConstraint("year is null or year between 1950 and 2100", name="ck_vehicles_year"),
        sa.CheckConstraint(_in("vehicle_type", VEHICLE_TYPES), name="ck_vehicles_type"),
        sa.CheckConstraint(_in("country", ("SA", "AE")), name="ck_vehicles_country"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_vehicles_currency"),
        sa.CheckConstraint(_in("fuel_type", FUEL_TYPES), name="ck_vehicles_fuel"),
        sa.CheckConstraint("tank_litres is null or tank_litres between 1 and 2000", name="ck_vehicles_tank"),
        sa.CheckConstraint("purchase_price is null or purchase_price >= 0", name="ck_vehicles_price"),
        sa.CheckConstraint("residual_value is null or (residual_value >= 0 and (purchase_price is null or residual_value <= purchase_price))",
                           name="ck_vehicles_residual"),
        sa.CheckConstraint("useful_life_months is null or useful_life_months between 1 and 600", name="ck_vehicles_life"),
        _payment_check("vehicles", "purchase_payment"),
        sa.CheckConstraint("version > 0", name="ck_vehicles_version"),
    )
    # One live vehicle per registration and country in a workspace.
    op.execute("create unique index uq_vehicles_registration on vehicles (tenant_id, workspace_id, country, upper(registration)) "
               "where archived_at is null")
    op.create_index("ix_vehicles_owner", "vehicles", ["tenant_id", "workspace_id", "owner_user_id"])

    op.create_table(
        "vehicle_odometer_readings",
        *_child(),
        sa.Column("reading_km", sa.Numeric(10, 1), nullable=False),
        sa.Column("read_at", TS, nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=True),
        sa.Column("correction", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicle_odometer_readings"),
        sa.CheckConstraint("reading_km >= 0 and reading_km < 10000000", name="ck_vehicle_odometer_readings_km"),
        sa.CheckConstraint(_in("source", READING_SOURCES), name="ck_vehicle_odometer_readings_source"),
        # A correction is authorised by a written reason; only a correction may lower the odometer.
        sa.CheckConstraint("not correction or (reason is not null and length(btrim(reason)) >= 3)", name="ck_vehicle_odometer_readings_reason"),
        sa.CheckConstraint("correction = (source = 'correction')", name="ck_vehicle_odometer_readings_correction"),
    )
    op.create_index("ix_vehicle_odometer_readings_vehicle", "vehicle_odometer_readings", ["vehicle_id", "read_at", "created_at"])
    op.create_index("ix_vehicle_odometer_readings_source", "vehicle_odometer_readings", ["vehicle_id", "source_id"])

    op.create_table(
        "vehicle_fuel_entries",
        *_child(),
        sa.Column("filled_at", TS, nullable=False),
        sa.Column("station", sa.String(120), nullable=True),
        sa.Column("fuel_type", sa.String(12), nullable=False),
        sa.Column("litres", sa.Numeric(9, 3), nullable=False),
        sa.Column("price_per_litre", sa.Numeric(9, 3), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("odometer_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("full_tank", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("payment_method", sa.String(14), nullable=True),
        sa.Column("payment", postgresql.JSONB(), nullable=True),
        sa.Column("document_id", sa.String(80), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicle_fuel_entries"),
        sa.CheckConstraint(_in("fuel_type", FUEL_TYPES), name="ck_vehicle_fuel_entries_fuel"),
        sa.CheckConstraint("litres > 0 and litres < 10000", name="ck_vehicle_fuel_entries_litres"),
        sa.CheckConstraint("price_per_litre is null or price_per_litre > 0", name="ck_vehicle_fuel_entries_price"),
        sa.CheckConstraint("total is null or total > 0", name="ck_vehicle_fuel_entries_total"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_vehicle_fuel_entries_currency"),
        sa.CheckConstraint("payment_method is null or " + _in("payment_method", PAYMENT_METHODS), name="ck_vehicle_fuel_entries_method"),
        _payment_check("vehicle_fuel_entries"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_vehicle_fuel_entries_idem"),
    )
    op.create_index("ix_vehicle_fuel_entries_vehicle", "vehicle_fuel_entries", ["vehicle_id", "filled_at"])

    op.create_table(
        "vehicle_trips",
        *_child(),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("start_odometer_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("end_odometer_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("distance_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("distance_basis", sa.String(16), nullable=True),
        sa.Column("purpose", sa.String(10), nullable=False, server_default="business"),
        sa.Column("purpose_note", sa.String(200), nullable=True),
        sa.Column("project_id", sa.String(80), nullable=True),
        sa.Column("origin", sa.String(120), nullable=True),
        sa.Column("destination", sa.String(120), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("source", sa.String(8), nullable=False, server_default="web"),
        sa.Column("device_id", sa.String(80), nullable=True),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicle_trips"),
        sa.CheckConstraint(_in("status", ("open", "closed")), name="ck_vehicle_trips_status"),
        sa.CheckConstraint("(status = 'closed') = (ended_at is not null)", name="ck_vehicle_trips_closed"),
        sa.CheckConstraint("ended_at is null or ended_at >= started_at", name="ck_vehicle_trips_time"),
        sa.CheckConstraint("start_odometer_km is null or end_odometer_km is null or end_odometer_km >= start_odometer_km",
                           name="ck_vehicle_trips_odometer"),
        sa.CheckConstraint("distance_km is null or distance_km >= 0", name="ck_vehicle_trips_distance"),
        sa.CheckConstraint("distance_basis is null or " + _in("distance_basis", ("typed", "previous_reading")), name="ck_vehicle_trips_basis"),
        sa.CheckConstraint(_in("purpose", ("business", "personal", "commute")), name="ck_vehicle_trips_purpose"),
        sa.CheckConstraint(_in("source", ("web", "device")), name="ck_vehicle_trips_source"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_vehicle_trips_idem"),
    )
    op.create_index("ix_vehicle_trips_vehicle", "vehicle_trips", ["vehicle_id", "started_at"])
    # At most one open driving session per person and vehicle.
    op.execute("create unique index uq_vehicle_trips_open on vehicle_trips (tenant_id, workspace_id, owner_user_id, vehicle_id) "
               "where status = 'open'")

    op.create_table(
        "vehicle_service_records",
        *_child(),
        sa.Column("kind", sa.String(14), nullable=False),
        sa.Column("service_on", sa.Date(), nullable=False),
        sa.Column("odometer_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("workshop", sa.String(120), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("reference", sa.String(60), nullable=True),
        sa.Column("next_due_on", sa.Date(), nullable=True),
        sa.Column("next_due_km", sa.Numeric(10, 1), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("trip_id", sa.String(80), nullable=True),
        sa.Column("payment_method", sa.String(14), nullable=True),
        sa.Column("payment", postgresql.JSONB(), nullable=True),
        sa.Column("document_id", sa.String(80), nullable=True),
        sa.Column("idem_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicle_service_records"),
        sa.CheckConstraint(_in("kind", SERVICE_KINDS), name="ck_vehicle_service_records_kind"),
        sa.CheckConstraint("payment_method is null or " + _in("payment_method", PAYMENT_METHODS), name="ck_vehicle_service_records_method"),
        sa.CheckConstraint("next_due_km is null or next_due_km > 0", name="ck_vehicle_service_records_due_km"),
        _payment_check("vehicle_service_records"),
        sa.UniqueConstraint("tenant_id", "workspace_id", "owner_user_id", "idem_key", name="uq_vehicle_service_records_idem"),
    )
    op.create_index("ix_vehicle_service_records_vehicle", "vehicle_service_records", ["vehicle_id", "service_on"])

    op.create_table(
        "vehicle_reminders",
        *_child(),
        sa.Column("kind", sa.String(14), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("step", sa.SmallInteger(), nullable=False),
        sa.Column("sent_at", TS, nullable=False, server_default=sa.func.now()),
        _workspace_fk("vehicle_reminders"),
        sa.CheckConstraint(_in("kind", ("insurance", "registration", "inspection", "service")), name="ck_vehicle_reminders_kind"),
        sa.CheckConstraint("step between 0 and 366", name="ck_vehicle_reminders_step"),
        sa.UniqueConstraint("vehicle_id", "kind", "source_id", "due_on", "step", name="uq_vehicle_reminders_once"),
    )

    op.create_table(
        "vehicle_preferences",
        *_scope(),
        sa.Column("trip_places", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_vehicle_id", sa.String(80), nullable=True),
        sa.Column("reminder_days", sa.SmallInteger(), nullable=False, server_default="30"),
        sa.Column("reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "workspace_id", "owner_user_id", name="pk_vehicle_preferences"),
        _workspace_fk("vehicle_preferences"),
        sa.CheckConstraint("reminder_days between 1 and 120", name="ck_vehicle_preferences_days"),
    )

    # A vehicle's ownership and owner, and a record's vehicle and copied visibility, never change: the
    # visibility policies rely on them. A child row's copy must match its vehicle when it is written.
    op.execute("""
        create function vehicles_fixed_owner() returns trigger language plpgsql as $$
        begin
            if new.ownership <> old.ownership or new.owner_user_id <> old.owner_user_id or new.tenant_id <> old.tenant_id
               or new.workspace_id <> old.workspace_id then
                raise exception 'a vehicle keeps its ownership and owner' using errcode = '23514';
            end if;
            return new;
        end $$
    """)
    op.execute("create trigger vehicles_fixed_owner before update on vehicles for each row execute function vehicles_fixed_owner()")
    op.execute("""
        create function vehicle_child_matches() returns trigger language plpgsql as $$
        declare
            v record;
        begin
            if tg_op = 'UPDATE' then
                if new.vehicle_id <> old.vehicle_id or new.visibility <> old.visibility or new.vehicle_owner_id <> old.vehicle_owner_id
                   or new.owner_user_id <> old.owner_user_id then
                    raise exception 'a vehicle record keeps its vehicle and owner' using errcode = '23514';
                end if;
                return new;
            end if;
            select ownership, owner_user_id, tenant_id, workspace_id into v from vehicles where id = new.vehicle_id;
            if not found or v.tenant_id <> new.tenant_id or v.workspace_id <> new.workspace_id
               or v.ownership <> new.visibility or v.owner_user_id <> new.vehicle_owner_id then
                raise exception 'the record does not match its vehicle' using errcode = '23514';
            end if;
            return new;
        end $$
    """)
    for table in CHILDREN:
        op.execute(f"create trigger {table}_matches before insert or update on {table} for each row execute function vehicle_child_matches()")

    # The odometer never goes down. Readings are ordered by (read_at, created_at, id); a reading that is
    # not a correction must be at least the reading before it and at most the reading after it, unless
    # that later reading is itself a correction (which starts a new run). The vehicle row is locked so two
    # readings of one vehicle cannot pass the check at the same time. Readings are never changed.
    op.execute("""
        create function vehicle_odometer_monotonic() returns trigger language plpgsql as $$
        declare
            before_km numeric;
            after_km numeric;
            after_correction boolean;
        begin
            if tg_op = 'UPDATE' then
                if new.reading_km <> old.reading_km or new.read_at <> old.read_at or new.correction <> old.correction
                   or new.vehicle_id <> old.vehicle_id then
                    raise exception 'an odometer reading is never changed: add a correction' using errcode = '23514';
                end if;
                return new;
            end if;
            perform 1 from vehicles where id = new.vehicle_id for update;
            if new.correction then
                return new;
            end if;
            select reading_km into before_km from vehicle_odometer_readings
             where vehicle_id = new.vehicle_id and (read_at, created_at, id) < (new.read_at, new.created_at, new.id)
             order by read_at desc, created_at desc, id desc limit 1;
            select reading_km, correction into after_km, after_correction from vehicle_odometer_readings
             where vehicle_id = new.vehicle_id and (read_at, created_at, id) > (new.read_at, new.created_at, new.id)
             order by read_at, created_at, id limit 1;
            if before_km is not null and new.reading_km < before_km then
                raise exception 'odometer_decrease: % km is below the earlier reading of % km', new.reading_km, before_km using errcode = '23514';
            end if;
            if after_km is not null and not after_correction and new.reading_km > after_km then
                raise exception 'odometer_decrease: % km is above the later reading of % km', new.reading_km, after_km using errcode = '23514';
            end if;
            return new;
        end $$
    """)
    op.execute("create trigger vehicle_odometer_monotonic before insert or update on vehicle_odometer_readings "
               "for each row execute function vehicle_odometer_monotonic()")

    for table in TABLES:
        _rls(table)

    # Discovery for the daily reminder job: scope and date columns only, never content, no write.
    op.execute(f"grant select (tenant_id, workspace_id, owner_user_id, archived_at, insurance_expires_on, registration_expires_on, "
               f"inspection_expires_on) on vehicles to {MAINT}")
    op.execute(f"""
        create policy maintenance_vehicles_due on vehicles for select to {MAINT}
        using (archived_at is null and (insurance_expires_on <= current_date + 120 or registration_expires_on <= current_date + 120
               or inspection_expires_on <= current_date + 120))
    """)
    op.execute(f"grant select (tenant_id, workspace_id, vehicle_owner_id, next_due_on, next_due_km) on vehicle_service_records to {MAINT}")
    op.execute(f"""
        create policy maintenance_vehicle_service_due on vehicle_service_records for select to {MAINT}
        using (next_due_km is not null or next_due_on <= current_date + 120)
    """)


def downgrade() -> None:
    # Only for an empty test database: production rolls back by restoring a backup.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'anum_maintenance') then
            revoke all on vehicles, vehicle_service_records from anum_maintenance;
          end if;
        end
        $$
        """
    )
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("drop function if exists vehicle_odometer_monotonic()")
    op.execute("drop function if exists vehicle_child_matches()")
    op.execute("drop function if exists vehicles_fixed_owner()")
