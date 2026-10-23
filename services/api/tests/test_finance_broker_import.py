from datetime import timezone
from decimal import Decimal
import pytest

from anum_api.finance.broker_import import parse_broker_statement_csv
from anum_api.finance.broker_reconciliation import BrokerDataError

HEADER = ("row_type,broker_id,account_id,as_of,source_reference,currency,"
          "symbol,side,quantity,last_price,settled,available\n")
STAMP = "2026-10-23T12:00:00+00:00"
CASH = f"cash,licensed-broker,account1,{STAMP},report1,USD,,,,,100,95\n"
POSITION = f"position,licensed-broker,account1,{STAMP},report1,USD,ABC,long,2,5,,\n"


def load(rows: str):
    return parse_broker_statement_csv((HEADER + rows).encode())


def test_import_position_and_cash():
    result = load(CASH + POSITION)
    assert result.account_id == "account1"
    assert result.as_of.tzinfo == timezone.utc
    assert result.cash[0].available == Decimal("95")
    assert result.positions[0].last_price == Decimal("5")


def test_reordered_headers_work():
    text = (HEADER + CASH).strip().splitlines()
    columns, values = text[0].split(","), text[1].split(",")
    order = list(reversed(range(len(columns))))
    blob = ",".join(columns[i] for i in order) + "\n" + ",".join(values[i] for i in order) + "\n"
    assert parse_broker_statement_csv(blob.encode()).cash[0].settled == Decimal("100")


@pytest.mark.parametrize("rows", [
    "",
    CASH.replace("account1", "account2") + POSITION,
    CASH.replace("100,95", "NaN,95"),
    POSITION.replace("long", "invalid"),
    CASH.replace("cash,", "=HYPERLINK(\"https://example.com\"),"),
    CASH + CASH,
    POSITION.replace(",2,5,,", ",-2,5,,"),
])
def test_unsafe_or_invalid_rows_are_refused(rows):
    with pytest.raises(BrokerDataError):
        load(rows)


def test_unknown_header_rejected():
    with pytest.raises(BrokerDataError):
        parse_broker_statement_csv(("other," + HEADER + CASH).encode())


def test_large_statement_rejected():
    with pytest.raises(BrokerDataError, match="at most"):
        parse_broker_statement_csv(b"x" * 512001)


def test_malformed_utf8_rejected():
    with pytest.raises(BrokerDataError, match="UTF-8"):
        parse_broker_statement_csv(b"\xff")


def test_no_timestamp_timezone_rejected():
    with pytest.raises(BrokerDataError):
        load(CASH.replace("+00:00", ""))
