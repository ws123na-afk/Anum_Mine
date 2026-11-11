"""Commerce amounts are exact decimals end to end (docs/commerce.md#data-model): no binary float error in sums,
half-up cents, three-decimal currencies kept, and JSON still carries numbers. In-memory store; orders are SYNTHETIC.
``test_postgres_commerce.py`` checks the same against ``numeric(18,4)`` columns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from anum_api import reports
from anum_api.commerce import insights
from anum_api.commerce.clients.base import number
from anum_api.commerce.models import (
    Channel,
    CommerceSettings,
    FeeKind,
    FeeRecord,
    Listing,
    Order,
    OrderLine,
    OrderStatus,
    ReturnRecord,
    cents,
    to_money,
)
from anum_api.commerce.store import commerce_store
from anum_api.commerce.tools import _approved_price, plan_call
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_shop_money", "workspace_shop_money"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_shop", roles=["owner"])
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 10)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    commerce_store.clear()
    yield
    commerce_store.clear()


def order(n: int, total: object, currency: str = "SAR", line_total: object = None) -> None:
    commerce_store.upsert_order(Order(
        id=f"ord_{n}", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=Channel.AMAZON, marketplace_id="m",
        external_id=f"EXT-{n}", status=OrderStatus.SHIPPED, purchased_at=NOW - timedelta(minutes=n), total_amount=total, currency=currency,
        item_count=1, lines=[OrderLine(sku="TYRE-205", quantity=1, total=line_total if line_total is not None else total)], synced_at=NOW))


def dashboard() -> insights.Dashboard:
    return insights.dashboard(commerce_store, CONTEXT, CommerceSettings(), first=DAY, last=DAY, tz_name="UTC", currency=None, now=NOW)


def test_amounts_are_parsed_exactly_and_rounded_half_up() -> None:
    assert to_money(0.1) + to_money(0.2) == Decimal("0.3")  # 0.1 + 0.2 == 0.30000000000000004 in float
    assert number("2.675") == Decimal("2.675")  # round(2.675, 2) gave 2.67
    assert number({"Amount": "19.99"}) == Decimal("19.99") and number(19.99) == Decimal("19.99")
    assert number("1.234") == Decimal("1.234")  # a three-decimal currency (KWD) keeps its fils
    assert number("not a number") is None and number(float("nan")) is None and number(True) is None
    assert cents(Decimal("1.005")) == Decimal("1.01") and cents(Decimal("2.675")) == Decimal("2.68")


def test_dashboard_totals_are_exact_sums_and_stay_json_numbers() -> None:
    for n, amount in enumerate(["0.1", "0.2", "0.1", "0.2", "0.1", "0.2", "0.1", "0.2", "0.1", "0.2"], start=1):
        order(n, amount)
    order(11, "1.005", currency="AED")
    commerce_store.upsert_fee(FeeRecord(id="fee_1", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=Channel.AMAZON,
                                        external_id="F1", kind=FeeKind.FEE, amount="1.005", currency="AED", posted_at=NOW))
    commerce_store.upsert_return(ReturnRecord(id="ret_1", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=Channel.AMAZON,
                                              external_id="R1", order_external_id="EXT-1", amount="0.125", currency="SAR", returned_at=NOW))
    data = dashboard()
    totals = {row.currency: row.revenue for row in data.currencies}
    assert totals == {"SAR": Decimal("1.50"), "AED": Decimal("1.01")}  # float: 1.4999999999999998, round(1.005, 2) = 1.0
    assert all(isinstance(row.revenue, Decimal) for row in data.currencies + data.by_channel + data.by_day + data.top_products)
    assert data.fees == {"AED": Decimal("1.01")} and data.returns.amount == {"SAR": Decimal("0.13")}
    wire = json.loads(data.model_dump_json())
    assert {row["currency"]: row["revenue"] for row in wire["currencies"]} == {"SAR": 1.5, "AED": 1.01}
    assert wire["fees"] == {"AED": 1.01} and wire["returns"]["amount"] == {"SAR": 0.13}


def test_the_spoken_summary_and_the_report_round_once() -> None:
    for n in range(1, 4):
        order(n, "0.335")  # 1.005 in total: half up is 1.01
    text, data = insights.sales_summary(commerce_store, CONTEXT, channel=None, period="today", tz_name="UTC", now=NOW)
    assert data["revenue"] == {"SAR": 1.01} and "SAR 1.01" in text
    json.dumps(data)  # still plain JSON for the tool result
    report = reports.commerce_summary(CONTEXT, 1, NOW + timedelta(hours=1))
    assert any(metric.value == "1.01" for metric in report.metrics if metric.label == "Revenue")
    assert {row[5] for row in report.data.rows} == {0.34}  # each 0.335 order shows half up
    json.dumps(report.model_dump(mode="json"))


def test_price_changes_are_exact_to_the_cent() -> None:
    assert _approved_price(2.675) == Decimal("2.68") and _approved_price("19.99") == Decimal("19.99")
    with pytest.raises(ValueError):
        _approved_price("abc")
    call = plan_call("set the price of TYRE-205 on amazon to 19.99")
    assert call is not None and call.arguments.get("price") == 19.99


def test_the_price_alert_compares_exact_amounts() -> None:
    listing = Listing(id="lst_1", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=Channel.AMAZON, marketplace_id="m",
                      sku="TYRE-205", price="100.00", lowest_competitor_price="98.00", currency="SAR", updated_at=NOW)
    # Exactly 2 percent below is not "more than 2 percent below"; a cent lower is.
    assert insights.price_alerts([listing], CommerceSettings(price_alert_percent=2.0)) == []
    cheaper = listing.model_copy(update={"lowest_competitor_price": Decimal("97.99")})
    assert [row.listing_id for row in insights.price_alerts([cheaper], CommerceSettings(price_alert_percent=2.0))] == ["lst_1"]
