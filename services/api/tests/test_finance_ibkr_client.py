"""The IBKR client's safety properties (docs/finance-live-trading.md#connector): fixed hosts, pinned
TLS checked before a byte is sent, no redirects, bounded answers, secrets never shown. A local
TLS server with a throwaway self-signed certificate stands in for the gateway; real IBKR is never
called.
"""

from __future__ import annotations

import hashlib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from ibkr_fake import PIN, FakeIbkr

from anum_api.finance import ibkr_client
from anum_api.finance.ibkr_client import HttpRequest, IbkrClient, IbkrCredentials, IbkrError, normalize_account, normalize_pin
from anum_api.finance.ibkr_rules import (
    BrokerLimits,
    BrokerSettings,
    OrderFacts,
    check_quantity,
    failed,
    order_checks,
    us_session,
)


def _creds(**extra: str) -> IbkrCredentials:
    return IbkrCredentials(**{"gateway_host": "ibkr-gateway", "tls_pin": PIN, "account_id": "DU1234567", **extra})


def test_credentials_only_allow_the_fixed_gateway_hosts_and_hide_in_repr() -> None:
    for host in ("evil.example.com", "api.ibkr.com", "10.0.0.5"):
        with pytest.raises(ValueError):
            _creds(gateway_host=host)
    creds = _creds(flex_token="exampletoken", flex_query_id="42")
    assert "DU1234567" not in repr(creds) and "exampletoken" not in repr(creds)
    assert "DU1234567" not in repr(IbkrClient(creds, FakeIbkr()))
    assert normalize_pin("AB:" * 31 + "AB") == PIN
    with pytest.raises(ValueError):
        normalize_account("1234")


def test_redirects_401_429_and_oversized_answers_are_refused() -> None:
    fake = FakeIbkr(redirect=True)
    with pytest.raises(IbkrError) as error:
        IbkrClient(_creds(), fake, pace=False).auth_status()
    assert error.value.code == "redirect_refused"
    for status, code in ((401, "session_expired"), (429, "rate_limited")):
        with pytest.raises(IbkrError) as error:
            IbkrClient(_creds(), lambda request, s=status: (s, b""), pace=False).ledger()
        assert error.value.code == code
    with pytest.raises(IbkrError) as error:
        IbkrClient(_creds(), lambda request: (200, b"[" + b"1," * 300_000 + b"1]"), pace=False).trades()
    assert error.value.code == "bad_response"


def test_unsafe_path_values_are_never_sent() -> None:
    sent: list[HttpRequest] = []
    client = IbkrClient(_creds(), lambda request: (sent.append(request), (200, b"{}"))[1], pace=False)
    with pytest.raises(IbkrError):
        client.cancel_order("../../oauth")
    assert not sent


def test_flex_statements_only_go_to_ibkr_flex_hosts() -> None:
    client = IbkrClient(_creds(flex_token="exampletoken", flex_query_id="42"), FakeIbkr(), pace=False)
    statement = client.flex_statement()
    assert statement.ready and statement.dividends[0].amount == Decimal("0.42") and statement.trades == 2
    with pytest.raises(IbkrError) as error:
        client._flex("https://evil.example.com/AccountManagement/FlexWebService/GetStatement", {"t": "x"})
    assert error.value.code == "host_refused"
    with pytest.raises(IbkrError):
        client._flex("http://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement", {"t": "x"})
    with pytest.raises(IbkrError) as error:
        IbkrClient(_creds(), FakeIbkr(), pace=False).flex_statement()
    assert error.value.code == "not_configured"


def test_the_order_body_is_a_limit_day_order_without_cash_quantity() -> None:
    fake = FakeIbkr()
    placed = IbkrClient(_creds(), fake, pace=False).place_limit_order(client_order_id="fin_int_abc", conid=1, side="BUY", quantity=Decimal(1),
                                                                      limit_price=Decimal("9.5"))
    assert placed.order_id and fake.placed[0]["cOID"] == "fin_int_abc" and "cashQty" not in fake.placed[0]
    with pytest.raises(IbkrError):
        IbkrClient(_creds(), fake, pace=False).place_limit_order(client_order_id="x", conid=1, side="SHORT", quantity=Decimal(1),
                                                                 limit_price=Decimal(1))


# -- pinned TLS against a real local TLS server --------------------------------------------------------


@pytest.fixture
def tls_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    import socket

    listener = socket.create_server(("127.0.0.1", 0))
    received: list[bytes] = []

    def serve() -> None:
        for _ in range(2):
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as conn:
                    data = conn.recv(65536)
                    received.append(data)
                    if data:
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            except (OSError, ssl.SSLError):
                received.append(b"")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(), received
    listener.close()


def test_the_pin_is_checked_before_any_request_byte_is_written(tls_server) -> None:  # type: ignore[no-untyped-def]
    port, good_pin, received = tls_server
    wrong = "0" * 64
    with pytest.raises(IbkrError) as error:
        ibkr_client._https_transport(HttpRequest("GET", "127.0.0.1", port, "/v1/api/tickle", None, wrong))
    assert error.value.code == "pin_mismatch"
    status, body = ibkr_client._https_transport(HttpRequest("GET", "127.0.0.1", port, "/v1/api/tickle", None, good_pin))
    assert status == 200 and body == b"{}"
    assert received[0] == b"" and received[1].startswith(b"GET /v1/api/tickle")


# -- pure rules ----------------------------------------------------------------------------------------


def test_market_hours_use_new_york_time_holidays_and_early_closes() -> None:
    assert us_session(datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc), 15).open
    assert not us_session(datetime(2026, 11, 4, 14, 40, tzinfo=timezone.utc), 15).open  # 09:40, inside the first 15 minutes
    assert not us_session(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc), 0).open  # Thanksgiving
    assert not us_session(datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc), 0).open  # 13:30 on an early-close day
    assert not us_session(datetime(2028, 3, 1, 16, 0, tzinfo=timezone.utc), 0).open  # holidays not known for 2028


def test_quantities_and_limits_at_the_edges() -> None:
    assert check_quantity(Decimal(1), False) is None and check_quantity(Decimal("0.5"), False)
    assert check_quantity(Decimal("0.5"), True) is None and check_quantity(Decimal("0.00001"), True)
    settings = BrokerSettings.model_validate({"allowed": [{"symbol": "SPY", "conid": 7}]})
    facts = OrderFacts(session_ok=True, quote_last=Decimal("10"), quote_age_seconds=1, cash=Decimal("11"), daily_pnl=Decimal("-4.99"),
                       reconciliation_ok=True)
    now = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    assert not failed(order_checks(side="buy", quantity=Decimal(1), limit_price=Decimal("10"), symbol="SPY", conid=7, settings=settings,
                                   facts=facts, now=now))
    just_over = order_checks(side="buy", quantity=Decimal(1), limit_price=Decimal("10.01"), symbol="SPY", conid=7, settings=settings,
                             facts=facts.model_copy(update={"cash": Decimal(20)}), now=now)
    assert [c.code for c in failed(just_over)] == ["max_order"]
    loss = order_checks(side="buy", quantity=Decimal(1), limit_price=Decimal("10"), symbol="SPY", conid=7, settings=settings,
                        facts=facts.model_copy(update={"daily_pnl": Decimal("-5")}), now=now)
    assert [c.code for c in failed(loss)] == ["daily_loss"]
    wrong_contract = order_checks(side="buy", quantity=Decimal(1), limit_price=Decimal("10"), symbol="SPY", conid=8, settings=settings,
                                  facts=facts, now=now)
    assert [c.code for c in failed(wrong_contract)] == ["symbol"]
    with pytest.raises(ValueError):
        BrokerLimits(max_order=Decimal("1001"))
    assert BrokerLimits(max_order=Decimal(5)).loosened(BrokerLimits()) == []
    assert BrokerLimits(max_daily_loss=Decimal(6)).loosened(BrokerLimits()) == ["max_daily_loss"]
