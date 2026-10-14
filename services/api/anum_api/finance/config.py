"""``ANUM_FINANCE_*`` operator settings (docs/finance.md#operator-settings).

Nothing here is a credential: a connector's credentials are entered by the person and
stored encrypted per connection. These only bound what the API does on its own.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FinanceConfig(BaseSettings):
    # The background connector sync (every replica may run it with PostgreSQL; due
    # connections are claimed one by one). Off: connections sync when the person presses
    # "Sync now".
    sync_enabled: bool = False
    sync_poll_seconds: float = Field(default=300.0, ge=10, le=3600)
    sync_batch_size: int = Field(default=20, ge=1, le=500)
    request_timeout_seconds: float = Field(default=15.0, ge=1, le=60)
    # After a failed sync the next try waits this long.
    retry_minutes: int = Field(default=60, ge=5, le=1440)

    model_config = SettingsConfigDict(env_prefix="ANUM_FINANCE_", env_file=".env", extra="ignore", hide_input_in_errors=True)


finance_config = FinanceConfig()
