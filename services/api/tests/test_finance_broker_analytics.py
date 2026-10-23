from datetime import datetime, timezone
from decimal import Decimal

from anum_api.finance.broker_analytics import analyze_broker_statement
from anum_api.finance.broker_reconciliation import (
    BrokerSnapshot, BrokerPosition, BrokerCash, PositionSide,
)


def sample():
    return BrokerSnapshot(
        broker_id="fixture-broker", account_id="owner-account",
        source_reference="local-file", as_of=datetime(2026, 10, 23, tzinfo=timezone.utc),
        cash=[BrokerCash(currency="USD", settled=Decimal("100"), available=Decimal("90"))],
        positions=[
            BrokerPosition(symbol="ONE", currency="USD", quantity=Decimal("2"), last_price=Decimal("40")),
            BrokerPosition(symbol="TWO", currency="USD", quantity=Decimal("2"), last_price=Decimal("10")),
            BrokerPosition(symbol="THREE", currency="SAR", quantity=Decimal("1"), last_price=None),
        ],
    )


def test_report_keeps_currencies_separate_and_marks_missing_prices():
    result = analyze_broker_statement(sample())
    assert result.position_count == 3
    assert result.cash_count == 1
    assert result.live_prices_verified is False
    assert result.read_only is True
    assert {x.currency for x in result.exposure} == {"USD", "SAR"}
    usd = next(x for x in result.exposure if x.currency == "USD")
    sar = next(x for x in result.exposure if x.currency == "SAR")
    assert usd.gross_market_value == Decimal("100")
    assert usd.net_market_value == Decimal("100")
    assert usd.top_position_symbol == "ONE"
    assert usd.top_position_share_percent == Decimal("80")
    assert sar.unpriced_count == 1
    assert sar.gross_market_value == 0
    assert any("incomplete" in item for item in result.warnings)


def test_short_value_kept_separate_from_long_and_concentration():
    snapshot = sample().model_copy(update={"positions": [
        BrokerPosition(symbol="L", currency="USD", quantity=Decimal("1"), last_price=Decimal("50")),
        BrokerPosition(symbol="S", currency="USD", quantity=Decimal("2"), last_price=Decimal("50"), side=PositionSide.SHORT),
    ]})
    row = analyze_broker_statement(snapshot).exposure[0]
    assert row.long_market_value == 50
    assert row.short_market_value == 100
    assert row.net_market_value == -50
    assert row.gross_market_value == 150
    assert row.top_position_symbol == "S"


def test_report_flags_negative_cash_and_never_invents_a_broker_value():
    snapshot = sample().model_copy(update={
        "cash": [BrokerCash(currency="USD", settled=Decimal("50"), available=Decimal("-5"))],
        "positions": [],
    })
    result = analyze_broker_statement(snapshot)
    assert result.exposure == []
    assert any("negative" in item for item in result.warnings)
    assert result.live_prices_verified is False
