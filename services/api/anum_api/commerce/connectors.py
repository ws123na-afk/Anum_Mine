"""Connector setup: what each channel needs, credential encryption and client construction.

Credentials are a small JSON object per connector, Fernet-encrypted with
``ANUM_SECRETS_KEY`` (``anum_api.secret_box``) before they reach the store, decrypted
only inside a sync or an approved tool call, and never returned: the API shows a hint
(the last characters, or the staff e-mail for the ANUM 360 store) and whether they are set.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..secret_box import secret_cipher
from .clients.amazon import AmazonClient
from .clients.base import ChannelClient
from .clients.noon import DEFAULT_PATHS as NOON_PATHS
from .clients.noon import NoonClient
from .clients.own_store import ANUM360_PATHS, OwnStoreClient
from .http import UnsafeEndpointError, check_owner_url
from .models import AMAZON_MARKETPLACES, NOON_MARKETPLACES, OWN_STORE_MARKETPLACE, Channel, Connector, OwnStoreMode

# Which credential fields each channel (and own-store mode) needs.
REQUIRED_CREDENTIALS: dict[str, tuple[str, ...]] = {
    Channel.AMAZON.value: ("lwa_client_id", "lwa_client_secret", "refresh_token"),
    Channel.NOON.value: ("api_key",),
    f"{Channel.OWN_STORE.value}:{OwnStoreMode.ANUM360.value}": ("email", "password"),
    f"{Channel.OWN_STORE.value}:{OwnStoreMode.REST.value}": ("api_key",),
    f"{Channel.OWN_STORE.value}:{OwnStoreMode.SUPABASE.value}": ("api_key",),
    f"{Channel.OWN_STORE.value}:{OwnStoreMode.WEBHOOK.value}": (),
}
_SELLER_ID = re.compile(r"^[A-Z0-9]{8,20}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class ConnectorInput(BaseModel):
    """Create or change a connector. Credentials are write-only: omit them to keep the saved ones."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    marketplaces: list[str] | None = Field(default=None, max_length=10)
    settings: dict[str, Any] | None = None
    credentials: dict[str, str] | None = None
    enabled: bool | None = None


class ConnectorCreate(ConnectorInput):
    channel: Channel


class ConnectorSetupError(ValueError):
    """The connector's settings or credentials are incomplete or invalid (message is safe to show)."""


def credential_key(channel: Channel, settings: dict[str, Any]) -> str:
    if channel == Channel.OWN_STORE:
        return f"{channel.value}:{OwnStoreMode(settings.get('mode') or OwnStoreMode.ANUM360).value}"
    return channel.value


def validate_marketplaces(channel: Channel, marketplaces: list[str] | None) -> list[str]:
    if channel == Channel.OWN_STORE:
        return [OWN_STORE_MARKETPLACE]
    known = AMAZON_MARKETPLACES if channel == Channel.AMAZON else NOON_MARKETPLACES
    chosen = list(dict.fromkeys(marketplaces or known))
    unknown = [m for m in chosen if m not in known]
    if unknown:
        raise ConnectorSetupError(f"Unknown marketplace: {', '.join(unknown)}. Choose from {', '.join(known)}.")
    if not chosen:
        raise ConnectorSetupError("Choose at least one marketplace.")
    return chosen


def validate_settings(channel: Channel, settings: dict[str, Any]) -> dict[str, Any]:
    """Keep only the settings this channel understands, checked."""
    clean: dict[str, Any] = {}
    if channel == Channel.AMAZON:
        seller = str(settings.get("seller_id") or "").strip().upper()
        if not _SELLER_ID.match(seller):
            raise ConnectorSetupError("Enter your Amazon Merchant Token (Seller ID), 8 to 20 letters and digits, from Seller Central > Settings > Account Info.")
        clean["seller_id"] = seller
        clean["fetch_buyer_details"] = bool(settings.get("fetch_buyer_details", False))
    elif channel == Channel.NOON:
        if settings.get("partner_id"):
            clean["partner_id"] = str(settings["partner_id"]).strip()[:80]
        if settings.get("base_url"):
            clean["base_url"] = _owner_url(settings["base_url"])
        paths = settings.get("paths") or {}
        if not isinstance(paths, dict):
            raise ConnectorSetupError("paths must be an object of noon API paths.")
        custom = {k: str(v) for k, v in paths.items() if k in NOON_PATHS and isinstance(v, str) and v.startswith("/") and len(v) <= 200}
        if custom:
            clean["paths"] = custom
    else:
        try:
            mode = OwnStoreMode(settings.get("mode") or OwnStoreMode.ANUM360)
        except ValueError:
            raise ConnectorSetupError("mode must be anum360, rest, supabase or webhook.") from None
        clean["mode"] = mode.value
        currency = str(settings.get("currency") or ("AED" if mode == OwnStoreMode.ANUM360 else "SAR")).upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ConnectorSetupError("currency must be a three-letter code such as AED or SAR.")
        clean["currency"] = currency
        if mode != OwnStoreMode.WEBHOOK:
            if not settings.get("base_url"):
                raise ConnectorSetupError("Enter your store's API address (https://...).")
            clean["base_url"] = _owner_url(settings["base_url"])
        window = settings.get("status_window_days")
        if window is not None:
            try:
                clean["status_window_days"] = max(1, min(90, int(window)))
            except (TypeError, ValueError):
                raise ConnectorSetupError("status_window_days must be a number of days.") from None
        if mode == OwnStoreMode.ANUM360 and isinstance(settings.get("paths"), dict):
            custom = {k: str(v) for k, v in settings["paths"].items() if k in ANUM360_PATHS and isinstance(v, str) and v.startswith("/")}
            if custom:
                clean["paths"] = custom
        if mode == OwnStoreMode.SUPABASE:
            for group in ("tables", "columns"):
                value = settings.get(group)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    raise ConnectorSetupError(f"{group} must be an object.")
                clean[group] = _identifiers(value, group)
    return clean


def _identifiers(value: dict[str, Any], group: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[str(key)] = _identifiers(item, group)
        elif isinstance(item, str) and _IDENT.match(item):
            result[str(key)] = item
        else:
            raise ConnectorSetupError(f"{group}.{key} must be a table or column name (letters, digits, underscore).")
    return result


def _owner_url(value: Any) -> str:
    try:
        return check_owner_url(str(value))
    except UnsafeEndpointError as exc:
        raise ConnectorSetupError(exc.message) from None


def validate_credentials(channel: Channel, settings: dict[str, Any], credentials: dict[str, str]) -> dict[str, str]:
    required = REQUIRED_CREDENTIALS[credential_key(channel, settings)]
    clean = {k: str(v).strip() for k, v in credentials.items() if k in required and str(v).strip()}
    missing = [k for k in required if k not in clean]
    if missing:
        raise ConnectorSetupError("Missing credentials: " + ", ".join(missing) + ".")
    if any(len(v) > 4000 for v in clean.values()):
        raise ConnectorSetupError("A credential is too long.")
    return clean


def new_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def encrypt_credentials(values: dict[str, str]) -> str:
    return secret_cipher().encrypt(json.dumps(values, sort_keys=True))


def decrypt_credentials(ciphertext: str | None) -> dict[str, str]:
    if not ciphertext:
        return {}
    value = json.loads(secret_cipher().decrypt(ciphertext))
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def credential_hint(channel: Channel, settings: dict[str, Any], credentials: dict[str, str]) -> str:
    if channel == Channel.AMAZON:
        return f"app …{credentials.get('lwa_client_id', '')[-4:]}"
    if channel == Channel.OWN_STORE and settings.get("mode") == OwnStoreMode.ANUM360.value:
        email = credentials.get("email", "")
        name, _, domain = email.partition("@")
        return f"{name[:2]}…@{domain}" if domain else "staff account"
    if credentials.get("webhook_secret"):
        return "signing secret set"
    key = credentials.get("api_key", "")
    return f"key …{key[-4:]}" if len(key) >= 8 else "key set"


def build_client(connector: Connector, credentials: dict[str, str]) -> ChannelClient:
    key = f"{connector.tenant_id}/{connector.workspace_id}/{connector.id}"
    if connector.channel == Channel.AMAZON:
        return AmazonClient(key, credentials, connector.settings, connector.marketplaces)
    if connector.channel == Channel.NOON:
        return NoonClient(key, credentials, connector.settings, connector.marketplaces)
    return OwnStoreClient(key, credentials, connector.settings)  # type: ignore[return-value]


def channel_target(connector: Connector) -> str:
    """The host a change goes to, shown on approvals."""
    from urllib.parse import urlsplit

    from .config import commerce_config

    if connector.channel == Channel.AMAZON:
        return urlsplit(commerce_config.amazon_eu_endpoint).hostname or "sellingpartnerapi-eu.amazon.com"
    if connector.channel == Channel.NOON:
        return urlsplit(str(connector.settings.get("base_url") or commerce_config.noon_base_url)).hostname or "noon"
    return urlsplit(str(connector.settings.get("base_url") or "")).hostname or "own-store"
