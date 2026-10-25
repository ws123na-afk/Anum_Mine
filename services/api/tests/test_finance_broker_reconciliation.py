from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from pydantic import ValidationError

from anum_api.finance.broker_reconciliation import (
    BrokerCash, BrokerPosition, BrokerSnapshot, BrokerDataError,
    ReconciliationTolerance, reconcile_snapshots,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def snapshot(*, cash="100", qty="2", currency="USD", account="acct-1",
             timestamp=NOW, price="5", include_position=True):
    return BrokerSnapshot(
        broker_id="test-broker",
        account_id=account,
        as_of=timestamp,
        source_reference="fixture-001",
        cash=[BrokerCash(currency=currency, settled=Decimal(cash), available=Decimal(cash))],
        positions=[BrokerPosition(symbol="ABC", currency=currency,
                                  quantity=Decimal(qty), last_price=Decimal(price) if price else None)]
        if include_position else [],
    )


def test_matching_snapshot_is_read_only():
    baseline = snapshot()
    actual = snapshot(timestamp=NOW + timedelta(minutes=1))
    result = reconcile_snapshots(baseline, actual, now=NOW + timedelta(minutes=2))
    assert result.matches
    assert result.currency_exposure == {"USD": Decimal("10")}
    assert result.trades_executed == 0
    assert result.read_only is True
    assert not result.stale


def test_mismatched_account_is_rejected():
    with pytest.raises(BrokerDataError, match="same broker"):
        reconcile_snapshots(snapshot(), snapshot(account="different"), now=NOW)


def test_cash_and_position_differences_detected():
    result = reconcile_snapshots(snapshot(), snapshot(cash="90", qty="3"), now=NOW)
    assert not result.matches
    assert {d.kind for d in result.discrepancies} == {
        "cash_settled", "cash_available", "position_quantity"
    }


def test_tolerances_allow_small_differences():
    result = reconcile_snapshots(
        snapshot(), snapshot(cash="99.99", qty="2.001"),
        tolerance=ReconciliationTolerance(cash=Decimal("0.02"), quantity=Decimal("0.002")), now=NOW,
    )
    assert result.matches


def test_stale_data_is_labelled():
    result = reconcile_snapshots(snapshot(), snapshot(), now=NOW + timedelta(hours=1))
    assert result.stale


def test_unpriced_positions_not_invented():
    result = reconcile_snapshots(snapshot(price=None), snapshot(price=None), now=NOW)
    assert result.currency_exposure == {}
    assert result.unpriced_positions == ["ABC:USD:long"]


def test_two_currencies_are_not_mixed():
    baseline = BrokerSnapshot(
        broker_id="b", account_id="a", as_of=NOW, source_reference="old",
        positions=[
            BrokerPosition(symbol="A", currency="USD", quantity=Decimal("2"), last_price=Decimal("3")),
            BrokerPosition(symbol="B", currency="SAR", quantity=Decimal("2"), last_price=Decimal("4")),
        ],
    )
    current = baseline.model_copy(update={"source_reference": "new"})
    result = reconcile_snapshots(baseline, current, now=NOW)
    assert result.currency_exposure == {"SAR": Decimal("8"), "USD": Decimal("6")}


def test_duplicate_positions_refused():
    with pytest.raises(ValidationError, match="Duplicate position"):
        BrokerSnapshot(broker_id="b", account_id="a", as_of=NOW, source_reference="f",
                       positions=[
                           BrokerPosition(symbol="A", currency="USD", quantity=Decimal(1)),
                           BrokerPosition(symbol="A", currency="USD", quantity=Decimal(2)),
                       ])


def test_future_snapshot_rejected():
    with pytest.raises(BrokerDataError, match="future"):
        reconcile_snapshots(snapshot(), snapshot(timestamp=NOW + timedelta(seconds=1)), now=NOW)


def test_no_implicit_broker_credentials_allowed():
    with pytest.raises(ValidationError):
        BrokerSnapshot(broker_id="b", account_id="a", as_of=NOW, source_reference="f", api_key="secret")


def test_negative_position_requires_explicit_short_side():
    with pytest.raises(ValidationError):
        BrokerPosition(symbol="A", currency="USD", quantity=Decimal(-1))


def test_naive_timestamps_rejected():
    with pytest.raises(ValidationError):
        snapshot(timestamp=datetime(2026, 10, 23))
