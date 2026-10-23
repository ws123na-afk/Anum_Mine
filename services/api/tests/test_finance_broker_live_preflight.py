from anum_api.finance.broker_live_preflight import (
    AssetKind, BrokerKind, LivePreflightRequest, evaluate_live_preflight,
)


def test_sa_crypto_remains_disabled_even_if_every_checkbox_is_claimed():
    request = LivePreflightRequest(
        broker=BrokerKind.ALPACA,
        country_of_residence="SA",
        assets=[AssetKind.STOCK, AssetKind.CRYPTO],
        paper_testing_passed=True,
        real_account_approved=True,
        broker_country_eligibility_verified=True,
        asset_permissions_verified=True,
        api_permission_review_passed=True,
        secrets_in_tenant_vault=True,
        market_data_license_verified=True,
        independent_security_review_passed=True,
        guardian_independent_test_passed=True,
        order_idempotency_test_passed=True,
        emergency_stop_test_passed=True,
        explicit_written_user_live_approval=True,
    )
    result = evaluate_live_preflight(request)
    assert "independent_sa_crypto_legal_and_provider_verification" in result.missing
    assert result.live_trading_enabled is False
    assert result.credentials_connected is False
    assert result.funds_held_by_anum is False
    assert result.orders_sent == 0
    assert result.eligibility_confirmed is False


def test_empty_user_attestations_cannot_connect_a_stock_broker():
    result = evaluate_live_preflight(LivePreflightRequest(
        broker=BrokerKind.INTERACTIVE_BROKERS,
        country_of_residence="SA", assets=[AssetKind.STOCK],
    ))
    assert "broker_country_eligibility_verified" in result.missing
    assert "secrets_in_tenant_vault" in result.missing
    assert not result.live_trading_enabled
    assert result.phase == "paper_and_read_only_preparation"
