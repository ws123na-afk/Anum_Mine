"""``ANUM_FINANCE_*`` operator settings (docs/finance.md#operator-settings).

Nothing here is a credential: a connector's credentials are entered by the person and
stored encrypted per connection. These only bound what the API does on its own.

``ANUM_FINANCE_LIVE_ENABLED`` (stage 8) defaults to false and is **refused at startup**
while no live executor exists (``live.LIVE_EXECUTORS`` is empty by design): the API will
not start with it on, and the Helm chart refuses the value too (``infra/helm/ci/lint.sh``).
Even when a later release allows it, the execution gateway still checks every other
precondition per request (``live.live_preconditions``). ``ANUM_FINANCE_WIDER_LIVE_ENABLED``
(stage 9) defaults to false and is refused unless the stage 8 flag is on.
"""

from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LIVE_FLAG_REFUSED = (
    "ANUM_FINANCE_LIVE_ENABLED must stay false: ANUM has no live executor and no write-scoped connector, "
    "so live finance execution cannot be enabled (docs/finance.md#limited-live)"
)

WIDER_FLAG_REFUSED = (
    "ANUM_FINANCE_WIDER_LIVE_ENABLED must stay false: it requires ANUM_FINANCE_LIVE_ENABLED, which is refused while "
    "no live executor exists (docs/finance.md#wider-live)"
)


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
    # Stage 7: how long the person's approval of an execution intent stays valid.
    approval_minutes: int = Field(default=30, ge=1, le=1440)
    # Stage 8: limited live execution. Off, and refused while no live executor exists.
    live_enabled: bool = False
    # Stage 9: wider live use after the limited stage proved itself. Off; needs the stage 8 flag.
    wider_live_enabled: bool = False
    # Graduation: the window the proof is measured over, and what it must show.
    wider_window_days: int = Field(default=30, ge=7, le=365)
    wider_min_days: int = Field(default=30, ge=7, le=365)
    wider_min_actions: int = Field(default=20, ge=5, le=10000)
    # Hard ceilings: at most this many tiers above the stage 8 limits, each at most this many
    # percent of those limits higher than the one before, and a cooling-off before one applies.
    wider_max_tier: int = Field(default=3, ge=1, le=5)
    wider_step_percent: int = Field(default=50, ge=10, le=100)
    wider_cooling_hours: int = Field(default=72, ge=24, le=720)

    model_config = SettingsConfigDict(env_prefix="ANUM_FINANCE_", env_file=".env", extra="ignore", hide_input_in_errors=True)

    @field_validator("live_enabled")
    @classmethod
    def _live_needs_an_executor(cls, value: bool) -> bool:
        from .live import live_executor_available

        if value and not live_executor_available():
            raise ValueError(LIVE_FLAG_REFUSED)
        return value

    @field_validator("wider_live_enabled")
    @classmethod
    def _wider_needs_live(cls, value: bool, info: ValidationInfo) -> bool:
        if value and not info.data.get("live_enabled"):
            raise ValueError(WIDER_FLAG_REFUSED)
        return value


finance_config = FinanceConfig()
