"""Credential metadata: scoping and lifecycle, never the raw secret.

Per the doc: "Integration credentials should be scoped to tenant, workspace,
user, or agent... Agents should never see raw secrets." `CredentialMetadata`
enforces that structurally -- it has no field that could hold secret
material, only `secret_ref`, a pointer to wherever the real secret manager
(Vault, AWS Secrets Manager, ...) keeps it. Nothing in this module can leak a
credential value because nothing in this module ever holds one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import new_id, utc_now


CredentialScope = Literal["tenant", "workspace", "user", "agent"]


class CredentialMetadata(BaseModel):
    """What ANUM knows about one integration credential.

    `secret_ref` documents *where* the real secret lives (e.g.
    `"vault://anum/tenant_1/github-pat"` or an ARN) -- it is deliberately
    just a reference string, never the credential value itself.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    integration_id: str = Field(min_length=1, max_length=200)
    scope: CredentialScope
    tenant_id: str = Field(min_length=1)
    workspace_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    secret_ref: str = Field(min_length=1, max_length=500)
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @model_validator(mode="after")
    def _check_scope_identity(self) -> "CredentialMetadata":
        missing_for_scope = {
            "workspace": self.workspace_id is None,
            "user": self.user_id is None,
            "agent": self.agent_id is None,
        }
        if self.scope in missing_for_scope and missing_for_scope[self.scope]:
            raise ValueError(f"{self.scope}-scoped credentials require {self.scope}_id")
        return self


class CredentialStore(Protocol):
    def save(self, credential: CredentialMetadata) -> CredentialMetadata: ...

    def get(self, credential_id: str) -> CredentialMetadata | None: ...

    def list_for_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CredentialMetadata]: ...

    def revoke(
        self, credential_id: str, *, revoked_at: datetime | None = None
    ) -> CredentialMetadata | None: ...


class InMemoryCredentialStore:
    """Reference `CredentialStore` implementation for tests and local use."""

    def __init__(self) -> None:
        self._credentials: dict[str, CredentialMetadata] = {}

    def save(self, credential: CredentialMetadata) -> CredentialMetadata:
        self._credentials[credential.id] = credential
        return credential

    def get(self, credential_id: str) -> CredentialMetadata | None:
        return self._credentials.get(credential_id)

    def list_for_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CredentialMetadata]:
        results = [
            credential
            for credential in self._credentials.values()
            if credential.tenant_id == tenant_id
            and (workspace_id is None or credential.workspace_id == workspace_id)
            and (user_id is None or credential.user_id == user_id)
            and (agent_id is None or credential.agent_id == agent_id)
        ]
        return sorted(results, key=lambda credential: (credential.created_at, credential.id))

    def revoke(
        self, credential_id: str, *, revoked_at: datetime | None = None
    ) -> CredentialMetadata | None:
        credential = self._credentials.get(credential_id)
        if credential is None:
            return None
        updated = credential.model_copy(update={"revoked_at": revoked_at or utc_now()})
        self._credentials[credential_id] = updated
        return updated


def new_credential_id() -> str:
    return new_id("credential")
