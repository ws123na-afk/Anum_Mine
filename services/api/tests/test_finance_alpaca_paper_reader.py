import json
import pytest

from anum_api.finance.alpaca_paper_reader import (
    AlpacaPaperCredentials, AlpacaPaperReader,
    BrokerPaperReadError, PAPER_ORIGIN,
)


def test_account_and_crypto_stock_positions_with_locked_host_and_get_only():
    seen = []

    def transport(request, limit):
        assert request.full_url.startswith(PAPER_ORIGIN)
        assert request.method == "GET"
        assert limit == 256000
        seen.append(request.full_url)
        assert request.get_header("Apca-api-key-id") == "test_id"
        if request.full_url.endswith("/v2/account"):
            return json.dumps({
                "id": "paper-1", "status": "ACTIVE",
                "buying_power": "500", "cash": "500", "equity": "500",
            }).encode()
        return json.dumps([
            {"symbol": "EXMPL", "asset_class": "us_equity", "qty": "1", "market_value": "30"},
            {"symbol": "BTCUSD", "asset_class": "crypto", "qty": "0.001", "market_value": "95"},
        ]).encode()

    reader = AlpacaPaperReader(AlpacaPaperCredentials("test_id", "not_real"), transport)
    account = reader.account()
    assert account.paper_only and not account.source_verified_live
    assert account.cash == 500
    assert account.funding_supported is False
    positions = reader.positions()
    assert len(positions) == 2
    assert {x.asset_class for x in positions} == {"crypto", "us_equity"}
    assert seen == [PAPER_ORIGIN + "/v2/account", PAPER_ORIGIN + "/v2/positions"]


def test_credentials_are_redacted_and_reject_empty():
    assert "secret-value" not in repr(AlpacaPaperCredentials("id", "secret-value"))
    with pytest.raises(BrokerPaperReadError):
        AlpacaPaperCredentials("", "something")


def test_broker_api_read_rejects_orders_and_external_hosts():
    reader = AlpacaPaperReader(AlpacaPaperCredentials("id", "secret"), lambda *_: b"{}")
    for endpoint in ("/v2/orders", "https://evil.example/v2/account", "//evil.example"):
        with pytest.raises(BrokerPaperReadError):
            reader._read(endpoint)


def test_large_and_malformed_payloads_fail_closed():
    reader = AlpacaPaperReader(AlpacaPaperCredentials("id", "secret"),
                               lambda *_: b"x" * 256001)
    with pytest.raises(BrokerPaperReadError):
        reader.account()
    broken = AlpacaPaperReader(AlpacaPaperCredentials("id", "secret"),
                               lambda *_: b"{broken")
    with pytest.raises(BrokerPaperReadError):
        broken.positions()


def test_broker_does_not_accept_unexpected_response_shapes():
    wrong = AlpacaPaperReader(AlpacaPaperCredentials("id", "secret"),
                               lambda *_: b'{"id":"abc"}')
    with pytest.raises(BrokerPaperReadError):
        wrong.account()
    with pytest.raises(BrokerPaperReadError):
        wrong.positions()
