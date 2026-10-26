"""No network or broker, no real orders, no transfers: SAR 500 research simulation."""
from decimal import Decimal
import pytest
from pydantic import ValidationError

from anum_api.finance.broker_paper import (
    Action, Market, PaperAsset, PaperHolding, PaperPortfolio,
    PaperSimulationRequest, PaperTrade, simulate_paper_trade,
)


def case(action=Action.BUY, quantity="1", price="50", market=Market.STOCK, **kwargs):
    quote = PaperAsset(ticker="EXMPL", market=market, reference_price_sar=Decimal(price),
                       source="manual reviewed test quote")
    trade = PaperTrade(ticker="EXMPL", market=market, action=action,
                       quantity=Decimal(quantity))
    return PaperSimulationRequest(
        portfolio=kwargs.pop("portfolio", PaperPortfolio()), quote=quote, trade=trade,
        **kwargs,
    )


def test_stocks_and_crypto_are_both_paper_only():
    for market in (Market.STOCK, Market.CRYPTO):
        result = simulate_paper_trade(case(market=market))
        assert result.accepted
        assert result.after.cash_sar == 450
        assert result.after.holdings[0].market == market
        assert result.real_orders_sent == 0
        assert result.live_trading_enabled is False
        assert result.money_transferred is False


def test_budget_cannot_exceed_500_sar():
    with pytest.raises(ValidationError):
        PaperPortfolio(starting_capital_sar=501)


def test_no_margin_no_leverage_or_uncovered_sell():
    assert not simulate_paper_trade(case(action=Action.SELL)).accepted
    assert not simulate_paper_trade(case(quantity="10")).accepted
    assert not simulate_paper_trade(case(
        portfolio=PaperPortfolio(cash_sar=Decimal("10")),
    )).accepted


def test_per_asset_exposure_capped():
    portfolio = PaperPortfolio(
        cash_sar=Decimal("450"),
        holdings=[PaperHolding(ticker="EXMPL", market=Market.STOCK,
                               quantity=Decimal("1"), average_cost_sar=Decimal("50"))],
    )
    result = simulate_paper_trade(case(portfolio=portfolio, quantity="2"))
    assert not result.accepted
    assert result.before == result.after


def test_daily_stop_and_kill_switch():
    assert not simulate_paper_trade(case(daily_realised_pnl_sar=Decimal("-10"))).accepted
    assert not simulate_paper_trade(case(
        portfolio=PaperPortfolio(trading_halted=True),
    )).accepted


def test_fee_is_included_in_simulated_cost_and_sell_pnl():
    bought = simulate_paper_trade(case(estimated_fee_sar=Decimal("1")))
    assert bought.accepted
    assert bought.after.cash_sar == 449
    assert bought.after.holdings[0].average_cost_sar == 51
    sold = simulate_paper_trade(case(
        action=Action.SELL, price="60", estimated_fee_sar=Decimal("1"),
        portfolio=bought.after,
    ))
    assert sold.accepted
    assert sold.after.cash_sar == 508
    assert sold.after.realised_pnl_sar == 8
    assert not sold.after.holdings


def test_refuse_unverified_fx_or_negative_price():
    with pytest.raises(ValidationError):
        PaperAsset(ticker="BTC", market=Market.CRYPTO, currency="USD",
                   reference_price_sar=Decimal("100"),
                   source="some source")
    with pytest.raises(ValidationError):
        PaperAsset(ticker="BTC", market=Market.CRYPTO, reference_price_sar=Decimal("-1"),
                   source="some source")


def test_broker_quote_mismatch_fails_closed():
    request = case()
    wrong = request.model_copy(update={
        "trade": PaperTrade(ticker="OTHER", market=Market.STOCK,
                            action=Action.BUY, quantity=Decimal("1")),
    })
    result = simulate_paper_trade(wrong)
    assert not result.accepted
    assert result.after == request.portfolio
