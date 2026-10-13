"""Large TyrePulse tables ANUM reads on demand, never copied by a sync.

``work_order_line_items`` (~184,000 rows) and ``parts_consumption`` (~215,000) are too big
to copy into the workspace on every sync. An assistant tool reads a bounded slice of one
of them when a question needs it (one work order, one vehicle, one period), through the
same read-only connection as the sync, and keeps nothing (docs/tyre-planning.md#fleet-operations-data).

Only the columns listed here are ever selected: operational fields, never a column about a
person (see ``model.PERSONAL_COLUMN``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveTable:
    name: str
    columns: tuple[str, ...]
    date_column: str
    vehicle_column: str
    work_order_column: str
    site_column: str
    # Numeric columns summed by the cost tool.
    amount_columns: tuple[str, ...] = ()


LIVE_TABLES: dict[str, LiveTable] = {
    "work_order_line_items": LiveTable(
        name="work_order_line_items",
        columns=("work_order_no", "asset_no", "site", "opened_date", "work_type", "task", "action", "qty"),
        date_column="opened_date",
        vehicle_column="asset_no",
        work_order_column="work_order_no",
        site_column="site",
    ),
    "parts_consumption": LiveTable(
        name="parts_consumption",
        columns=("work_order_no", "asset_code", "site", "event_date", "item_code", "item_description",
                 "qty_n", "cost_category", "line_cost", "tyre_cost", "spare_cost", "oil_cost", "currency"),
        date_column="event_date",
        vehicle_column="asset_code",
        work_order_column="work_order_no",
        site_column="site",
        amount_columns=("line_cost", "tyre_cost", "spare_cost", "oil_cost"),
    ),
}
