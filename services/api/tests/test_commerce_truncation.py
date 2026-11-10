"""A commerce figure that did not read every order says so (docs/commerce.md#dashboard): "truncated" is judged on the
rows read, before cancelled orders and other channels are filtered out. In-memory store; orders are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest

from anum_api import reports
from anum_api.commerce import insights
from anum_api.commerce.config import commerce_config
from anum_api.commerce.models import (
    Channel,
    CommerceSettings,
    Order,
    OrderLine,
    OrderStatus,
    ReturnRecord,
)
from anum_api.commerce.store import commerce_store
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_shop_cap", "workspace_shop_cap"
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_shop", roles=["owner"])
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 10)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    commerce_store.clear()
    yield
    commerce_store.clear()


def order(n: int, status: OrderStatus, hours_ago: int, channel: Channel = Channel.AMAZON) -> None:
    commerce_store.upsert_order(Order(
        id=f"ord_{n}", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=channel, marketplace_id="m",
        external_id=f"EXT-{n}", status=status, purchased_at=NOW - timedelta(hours=hours_ago), total_amount=100.0, currency="SAR",
        item_count=1, lines=[OrderLine(sku="TYRE-205", quantity=1)], synced_at=NOW))


def four_orders() -> None:
    """Newest first: two cancelled, then two shipped, SAR 100.00 each, all today."""
    order(1, OrderStatus.CANCELLED, 1)
    order(2, OrderStatus.CANCELLED, 2)
    order(3, OrderStatus.SHIPPED, 3)
    order(4, OrderStatus.SHIPPED, 4)


def dashboard() -> insights.Dashboard:
    return insights.dashboard(commerce_store, CONTEXT, CommerceSettings(), first=DAY, last=DAY, tz_name="UTC", currency=None, now=NOW)


def test_the_dashboard_says_truncated_when_the_rows_read_pass_the_cap_whatever_the_filter_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    four_orders()
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 3)
    cut = dashboard()
    # Four orders exist and three are read (the newest): two cancelled and one shipped. One order of 100.00 is counted,
    # and the answer says it is partial. Judged after the filter, 2 counted orders never passed a cap of 3.
    assert cut.truncated is True
    assert [(total.currency, total.orders, total.revenue) for total in cut.currencies] == [("SAR", 1, 100.0)]
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 4)
    whole = dashboard()
    assert whole.truncated is False
    assert [(total.currency, total.orders, total.revenue) for total in whole.currencies] == [("SAR", 2, 200.0)]


def test_the_dashboard_says_truncated_when_not_every_return_was_read(monkeypatch: pytest.MonkeyPatch) -> None:
    four_orders()
    for n in (1, 2):
        commerce_store.upsert_return(ReturnRecord(
            id=f"ret_{n}", tenant_id=TENANT, workspace_id=WORKSPACE, connector_id="conn_1", channel=Channel.AMAZON, external_id=f"RET-{n}",
            order_external_id=f"EXT-{n + 2}", sku="TYRE-205", amount=100.0, currency="SAR", returned_at=NOW - timedelta(hours=n)))
    assert dashboard().truncated is False and dashboard().returns.count == 2
    monkeypatch.setattr(insights, "RETURNS_CAP", 1)
    cut = dashboard()
    assert cut.returns.count == 1 and cut.truncated is True


def test_the_sales_sentence_and_its_numbers_say_when_they_are_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    four_orders()
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 3)
    text, data = insights.sales_summary(commerce_store, CONTEXT, channel=None, period="today", tz_name="UTC", now=NOW)
    assert (data["orders"], data["revenue"], data["truncated"]) == (1, {"SAR": 100.0}, True)
    assert text.endswith("Only the newest 3 orders were read, so these figures are partial.")
    arabic, _ = insights.sales_summary(commerce_store, CONTEXT, channel=None, period="today", tz_name="UTC", arabic=True, now=NOW)
    assert "3" in arabic and arabic.endswith("جزئية.")
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 4)
    text, data = insights.sales_summary(commerce_store, CONTEXT, channel=None, period="today", tz_name="UTC", now=NOW)
    assert (data["orders"], data["revenue"], data["truncated"]) == (2, {"SAR": 200.0}, False) and "partial" not in text


def test_the_commerce_report_says_when_it_did_not_read_every_order(monkeypatch: pytest.MonkeyPatch) -> None:
    four_orders()
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 3)
    cut = reports.commerce_summary(CONTEXT, 7, NOW)
    assert cut.highlights[0] == "Only the newest 3 orders were read: these figures and the comparison are partial"
    assert {metric.label: metric.value for metric in cut.metrics}["Orders"] == "1"
    monkeypatch.setattr(commerce_config, "dashboard_max_orders", 4)
    whole = reports.commerce_summary(CONTEXT, 7, NOW)
    assert not any("partial" in line for line in whole.highlights)
    assert {metric.label: metric.value for metric in whole.metrics}["Orders"] == "2"
