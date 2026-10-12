"""Deployment settings for social media marketing (``ANUM_MARKETING_*``, docs/marketing.md).

The platform *apps* (client ids and secrets) belong to the deployment and come from the
environment, like the developer-mode GitHub token: an operator creates one app per
platform and sets these names. Nothing is required; a platform without its app shows
what is missing on the Marketing page. The *accounts* connected through those apps
(their access and refresh tokens) are per workspace and stored encrypted with
``ANUM_SECRETS_KEY`` (``anum_api.secret_box``).
"""

from __future__ import annotations

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketingSettings(BaseSettings):
    # Where each platform sends the browser back after consent. Register exactly this
    # URL with every platform app (it is the web app's origin plus /marketing/callback).
    oauth_redirect_url: str = ""
    # The API's public HTTPS origin. Instagram (and TikTok photo pulls) fetch media from
    # a URL; the API serves each attached file there for a short time under a signed link.
    public_api_url: str = ""

    meta_app_id: str = ""
    meta_app_secret: SecretStr | None = None
    meta_graph_version: str = "v23.0"

    linkedin_client_id: str = ""
    linkedin_client_secret: SecretStr | None = None
    linkedin_version: str = "202509"

    x_client_id: str = ""
    x_client_secret: SecretStr | None = None

    tiktok_client_key: str = ""
    tiktok_client_secret: SecretStr | None = None
    # Off until TikTok's app audit approved Direct Post: until then videos go to the
    # creator's TikTok inbox as drafts ("upload as draft") and they finish posting in TikTok.
    tiktok_direct_post: bool = False

    google_client_id: str = ""
    google_client_secret: SecretStr | None = None

    # Publishing: how many times a failing post is tried before it is marked failed.
    max_attempts: int = Field(default=3, ge=1, le=10)
    # Largest media file sent to a platform in one publish.
    max_media_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    # Seconds a signed media link (Instagram fetch) stays valid.
    media_link_seconds: int = Field(default=3600, ge=60, le=86400)
    # Timeout of one platform HTTP call (uploads included).
    http_timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    model_config = SettingsConfigDict(
        env_prefix="ANUM_MARKETING_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )


marketing_settings = MarketingSettings()

# Tests replace this with an ``httpx.MockTransport``; production uses the network.
platform_transport: httpx.AsyncBaseTransport | None = None


def http_client(**kwargs: object) -> httpx.AsyncClient:
    """One client for platform calls: bounded timeout, no redirects to unknown hosts."""
    return httpx.AsyncClient(
        transport=platform_transport,
        timeout=marketing_settings.http_timeout_seconds,
        follow_redirects=False,
        **kwargs,  # type: ignore[arg-type]
    )


def secret(value: SecretStr | None) -> str:
    return value.get_secret_value().strip() if value is not None else ""
