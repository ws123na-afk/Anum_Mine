"""``ANUM_COMMERCE_*`` operator settings (docs/commerce.md#operator-settings).

Nothing here is a credential: every channel credential is entered by a workspace owner
and stored encrypted per connector. These only bound what the API does on its own.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommerceSettingsConfig(BaseSettings):
    # The background sync loop (every replica may run it with PostgreSQL; due connectors
    # are claimed one by one). Off: connectors sync when someone presses "Sync now".
    sync_enabled: bool = False
    sync_poll_seconds: float = Field(default=60.0, ge=5, le=3600)
    sync_batch_size: int = Field(default=20, ge=1, le=500)
    # One sync of one connector stops after this long; the next one continues from the cursor.
    sync_time_budget_seconds: float = Field(default=50.0, ge=5, le=600)
    # Pages per collection per sync (each page is one rate-limited call).
    sync_max_pages: int = Field(default=10, ge=1, le=200)
    # Competitive pricing checks (Amazon getItemOffers) per sync: 0.5 requests a second.
    pricing_checks_per_sync: int = Field(default=10, ge=0, le=200)
    # The first sync reaches this far back.
    initial_lookback_days: int = Field(default=30, ge=1, le=365)
    # A failed request is retried this many times when the channel says "slow down" (429).
    max_retries: int = Field(default=3, ge=0, le=10)
    # The longest a request waits for the channel's Retry-After before giving up for this sync.
    max_retry_wait_seconds: float = Field(default=10.0, ge=0, le=120)
    request_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    amazon_lwa_url: str = "https://api.amazon.com/auth/o2/token"
    amazon_eu_endpoint: str = "https://sellingpartnerapi-eu.amazon.com"
    noon_base_url: str = "https://api.noon.partners"
    # Dashboard ranges are capped so one request never reads an unbounded number of orders.
    dashboard_max_days: int = Field(default=366, ge=7, le=1100)
    dashboard_max_orders: int = Field(default=50_000, ge=100, le=500_000)

    model_config = SettingsConfigDict(env_prefix="ANUM_COMMERCE_", env_file=".env", extra="ignore", hide_input_in_errors=True)


commerce_config = CommerceSettingsConfig()
