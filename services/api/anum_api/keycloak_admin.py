"""Removing a deleted organisation's sign-in accounts from Keycloak (docs/organisation-lifecycle.md).

After an organisation's purge completes, the people who belonged only to that organisation
have no reason to keep a Keycloak account; people who also belong to other organisations
keep theirs and only lose this organisation from their ``tenant_id`` / ``tenant_ids``
attributes (the claims ANUM reads, docs/customer-organisations.md#switching).

The API reaches Keycloak's admin REST API with a confidential service-account client
(client credentials grant) that needs only the ``realm-management`` roles ``view-users``
and ``manage-users``. It is off unless ``ANUM_KEYCLOAK_ADMIN_URL``,
``ANUM_KEYCLOAK_ADMIN_CLIENT_ID`` and ``ANUM_KEYCLOAK_ADMIN_CLIENT_SECRET`` are set; then
the receipt says the accounts were not removed and the operator removes them.

The cleanup is idempotent: a deleted user is gone (``404`` is success), and a detached
user no longer carries the organisation, so a second run finds nobody.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .settings import settings

_SCOPE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


class KeycloakAdmin(Protocol):
    def users_with_attribute(self, name: str, value: str) -> list[dict[str, Any]]: ...
    def delete_user(self, user_id: str) -> None: ...
    def update_attributes(self, user: dict[str, Any], attributes: dict[str, list[str]]) -> None: ...


class KeycloakAdminError(RuntimeError):
    """Keycloak refused or could not be reached; the runner retries."""


class HttpKeycloakAdmin:
    """Keycloak's admin REST API with a client-credentials token (no user password)."""

    def __init__(self, base_url: str, realm: str, client_id: str, client_secret: str, *, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._token: str | None = None

    def _bearer(self) -> str:
        if self._token is None:
            response = self._http.post(
                f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token",
                data={"grant_type": "client_credentials", "client_id": self._client_id, "client_secret": self._client_secret},
            )
            if response.status_code != 200:
                raise KeycloakAdminError(f"Keycloak refused the service account ({response.status_code})")
            self._token = str(response.json()["access_token"])
        return self._token

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(
                method, f"{self.base_url}/admin/realms/{self.realm}{path}",
                headers={"Authorization": f"Bearer {self._bearer()}"}, **kwargs,
            )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"Keycloak could not be reached ({type(exc).__name__})") from exc
        if response.status_code >= 400 and response.status_code != 404:
            raise KeycloakAdminError(f"Keycloak answered {response.status_code} to {method} {path.split('?')[0]}")
        return response

    def users_with_attribute(self, name: str, value: str) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        first = 0
        while True:
            page = self._call("GET", "/users", params={"q": f"{name}:{value}", "first": first, "max": 100, "briefRepresentation": "false"})
            batch = page.json() if page.status_code == 200 else []
            users.extend(batch)
            if len(batch) < 100:
                return users
            first += 100

    def delete_user(self, user_id: str) -> None:
        self._call("DELETE", f"/users/{user_id}")

    def update_attributes(self, user: dict[str, Any], attributes: dict[str, list[str]]) -> None:
        self._call("PUT", f"/users/{user['id']}", json={**user, "attributes": attributes})


def build_admin() -> KeycloakAdmin | None:
    """The configured admin client, or ``None`` when Keycloak cleanup is not set up."""
    secret = settings.keycloak_admin_client_secret.get_secret_value() if settings.keycloak_admin_client_secret else ""
    if not (settings.keycloak_admin_url and settings.keycloak_admin_client_id and secret):
        return None
    return HttpKeycloakAdmin(settings.keycloak_admin_url, settings.keycloak_admin_realm, settings.keycloak_admin_client_id, secret)


@dataclass(frozen=True)
class CleanupResult:
    removed: int
    detached: int


def _values(attributes: dict[str, Any], name: str) -> list[str]:
    raw = attributes.get(name) or []
    values = [raw] if isinstance(raw, str) else list(raw)
    return [v for v in values if isinstance(v, str) and _SCOPE.fullmatch(v)]


def remove_organisation_users(admin: KeycloakAdmin, tenant_id: str) -> CleanupResult:
    """Delete the accounts that belong only to ``tenant_id``; take ``tenant_id`` off the rest."""
    found: dict[str, dict[str, Any]] = {}
    for attribute in ("tenant_id", "tenant_ids"):
        for user in admin.users_with_attribute(attribute, tenant_id):
            if user.get("id"):
                found[str(user["id"])] = user
    removed = detached = 0
    for user_id, user in sorted(found.items()):
        attributes = dict(user.get("attributes") or {})
        home = _values(attributes, "tenant_id")
        listed = _values(attributes, "tenant_ids")
        if tenant_id not in home and tenant_id not in listed:
            continue  # Keycloak's search is a match on any value; this user is not ours
        others = [t for t in dict.fromkeys([*home, *listed]) if t != tenant_id]
        if not others:
            admin.delete_user(user_id)
            removed += 1
            continue
        new_home = home[0] if home and home[0] != tenant_id else others[0]
        attributes["tenant_id"] = [new_home]
        attributes["tenant_ids"] = [t for t in others if t != new_home]
        admin.update_attributes(user, attributes)
        detached += 1
    return CleanupResult(removed=removed, detached=detached)
