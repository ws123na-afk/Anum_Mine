from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from anum_api.finance.broker_history import compare_broker_history
from anum_api.finance.broker_reconciliation import (
    BrokerSnapshot, BrokerPosition, BrokerCash, PositionSide, BrokerDataError,
)

T = datetime(2026, 10, 23, tzinfo=timezone.utc)


def book(*, later=False, quantity="2", price="10", cash="100", account="A", side=PositionSide.LONG):
    return BrokerSnapshot(
        broker_id="fixture", account_id=account, source_reference="statement",
        as_of=T + timedelta(days=1 if later else 0),
        cash=[BrokerCash(currency="USD", settled=Decimal(cash), available=Decimal(cash))],
        positions=[BrokerPosition(symbol="ABC", currency="USD", side=side,
                                  quantity=Decimal(quantity), last_price=Decimal(price))],
    )


def test_fixed_quantity_mark_to_market_is_not_called_profit():
    report = compare_broker_history(book(), book(later=True, price="12"))
    movement = report.position_changes[0]
    assert movement.valuation_change == Decimal("4")
    assert report.currency_changes[0].known_valuation_change == Decimal("4")
    assert report.profit_calculated is False
    assert report.trades_executed == 0


def test_quantity_change_not_counted_as_return():
    report = compare_broker_history(book(), book(later=True, quantity="4", price="12", cash="70"))
    assert report.position_changes[0].quantity_change == Decimal("2")
    assert report.position_changes[0].valuation_change is None
    assert report.currency_changes[0].partial is True
    assert report.currency_changes[0].known_valuation_change == 0
    assert report.cash_changes[0].settled_change == Decimal("-30")


def test_short_exposure_price_increase_is_negative():
    report = compare_broker_history(book(side=PositionSide.SHORT), book(later=True, price="12", side=PositionSide.SHORT))
    assert report.position_changes[0].valuation_change == Decimal("-4")
    assert report.currency_changes[0].current_priced_positions_value == Decimal("-24")


def test_new_position_does_not_invent_a_gain():
    prior = book().model_copy(update={"positions": []})
    report = compare_broker_history(prior, book(later=True))
    assert report.position_changes[0].valuation_change is None
    assert report.currency_changes[0].partial


def test_different_accounts_rejected():
    with pytest.raises(BrokerDataError):
        compare_broker_history(book(), book(later=True, account="B"))


def test_reversed_statement_dates_rejected():
    with pytest.raises(BrokerDataError):
        compare_broker_history(book(later=True), book())


def test_cash_only_statement_still_explained():
    before = book().model_copy(update={"positions": []})
    after = book(later=True, cash="110").model_copy(update={"positions": []})
    report = compare_broker_history(before, after)
    assert report.currency_changes == []
    assert report.cash_changes[0].available_change == 10
    assert report.profit_calculated is False
