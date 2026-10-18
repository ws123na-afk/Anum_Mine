"""``ANUM_CALLS_*`` operator settings for phone calls (docs/calls.md#settings).

Nothing here is a credential: a workspace's voice provider account is stored by an owner,
encrypted with ``ANUM_SECRETS_KEY``. These settings say where the provider reaches this API
and cap what any workspace may allow.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CallSettings(BaseSettings):
    # The public https origin the voice provider calls back (webhooks), for example
    # https://api.example.com. Empty: provider calls stay off (nothing could reach ANUM).
    public_base_url: str = Field(default="", max_length=300, pattern=r"^(|https://[A-Za-z0-9.-]+(:[0-9]{1,5})?)$")
    # The longest and most expensive AI call any workspace may allow; each workspace sets
    # its own caps at or under these.
    ai_max_seconds: int = Field(default=600, ge=30, le=900)
    ai_max_cost_cents: int = Field(default=500, ge=1, le=10_000)
    # Turns of one AI conversation (each turn is one model call).
    ai_max_turns: int = Field(default=12, ge=2, le=40)
    # The answer of one AI turn and of a call summary (tokens).
    ai_turn_max_tokens: int = Field(default=300, ge=64, le=2000)
    summary_max_tokens: int = Field(default=800, ge=128, le=4000)
    # Characters of notes or transcript placed in one summary prompt.
    summary_max_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    # Largest recording ANUM downloads from the provider.
    recording_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024 * 1024, le=512 * 1024 * 1024)
    provider_timeout_seconds: float = Field(default=15.0, ge=2, le=60)
    # A browser call must reach the provider this soon after ANUM prepared it.
    dial_window_seconds: int = Field(default=120, ge=30, le=900)

    model_config = SettingsConfigDict(env_prefix="ANUM_CALLS_", env_file=".env", extra="ignore", hide_input_in_errors=True)

    @property
    def webhooks_ready(self) -> bool:
        return bool(self.public_base_url.strip())


call_settings = CallSettings()
