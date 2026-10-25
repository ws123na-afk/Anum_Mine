"""Strict offline CSV statement ingestion for broker reconciliation.

Inputs are *data*, never trusted instructions. No URLs, account credentials,
execution scopes or deposits are accepted. Consumers must authorize ownership,
apply retention rules and encrypt persisted statements separately.

Expected format (a single document with row_type):
row_type,broker_id,account_id,as_of,source_reference,currency,symbol,side,quantity,last_price,settled,available
cash,...,USD,,,,,100.00,100.00
position,...,USD,ABC,long,2,30,,

Header columns may be reordered, but may not be missing or repeated. The
timestamp is ISO 8601 with an explicit offset. Empty numeric fields are
accepted only where the row type does not use them. No formula cells.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .broker_reconciliation import (
    BrokerCash, BrokerDataError, BrokerPosition, BrokerSnapshot,
)

HEADERS = (
    "row_type", "broker_id", "account_id", "as_of",
    "source_reference", "currency", "symbol", "side",
    "quantity", "last_price", "settled", "available",
)
MAX_BYTES = 512_000
MAX_RECORDS = 2000
CELL_LIMIT = 128


def _numeric(value: str, field: str, *, optional: bool = False) -> Decimal | None:
    if not value:
        if optional:
            return None
        raise BrokerDataError(f"Missing required numeric field: {field}")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BrokerDataError(f"Invalid number in {field}") from exc
    if not number.is_finite():
        raise BrokerDataError(f"Non-finite number in {field}")
    return number


def parse_broker_statement_csv(content: bytes) -> BrokerSnapshot:
    """Return a fully validated, bounded and account-consistent snapshot.

    Input is a raw, manually supplied document. It is not a live broker
    connection or proof that the statement originated at a given broker.
    """
    if not content or len(content) > MAX_BYTES:
        raise BrokerDataError("Statement must be nonempty and at most 512000 bytes")
    try:
        document = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BrokerDataError("Statement must be UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(document, newline=""), strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise BrokerDataError("Missing or duplicate CSV headers")
        if set(reader.fieldnames) != set(HEADERS):
            raise BrokerDataError("CSV headers do not match the documented statement format")

        cash: list[BrokerCash] = []
        positions: list[BrokerPosition] = []
        identity: tuple[str, str, str, str] | None = None
        count = 0

        for line in reader:
            count += 1
            if count > MAX_RECORDS:
                raise BrokerDataError("Too many statement rows")
            if None in line or any(value is None or len(value) > CELL_LIMIT for value in line.values()):
                raise BrokerDataError("Malformed or oversized CSV row")
            if any(value.lstrip().startswith(("=", "+", "-", "@")) for key, value in line.items()
                   if key not in ("quantity", "last_price", "settled", "available")):
                raise BrokerDataError("Spreadsheet formula or unsafe text in statement")
            entry = (line["broker_id"], line["account_id"], line["as_of"], line["source_reference"])
            if identity is None:
                identity = entry
            elif entry != identity:
                raise BrokerDataError("Mixed broker accounts or timestamps in statement")
            record_type = line["row_type"].strip().lower()
            try:
                if record_type == "cash":
                    if any(line[k].strip() for k in ("symbol", "side", "quantity", "last_price")):
                        raise BrokerDataError("Cash row contains position-only fields")
                    cash.append(BrokerCash(
                        currency=line["currency"],
                        settled=_numeric(line["settled"], "settled"),
                        available=_numeric(line["available"], "available"),
                    ))
                elif record_type == "position":
                    if line["settled"].strip() or line["available"].strip():
                        raise BrokerDataError("Position row contains cash-only fields")
                    positions.append(BrokerPosition(
                        currency=line["currency"], symbol=line["symbol"],
                        side=line["side"].strip().lower(),
                        quantity=_numeric(line["quantity"], "quantity"),
                        last_price=_numeric(line["last_price"], "last_price", optional=True),
                    ))
                else:
                    raise BrokerDataError("Unrecognized broker statement row type")
            except (ValueError, TypeError) as exc:
                if isinstance(exc, BrokerDataError):
                    raise
                raise BrokerDataError(f"Invalid broker statement row {count}") from exc
    except csv.Error as exc:
        raise BrokerDataError("Malformed CSV statement") from exc

    if identity is None:
        raise BrokerDataError("Statement has no rows")
    try:
        return BrokerSnapshot(
            broker_id=identity[0], account_id=identity[1],
            as_of=datetime.fromisoformat(identity[2]),
            source_reference=identity[3], cash=cash, positions=positions,
        )
    except (ValueError, TypeError) as exc:
        raise BrokerDataError("Invalid statement metadata or duplicate rows") from exc
