"""Skill registry: tenant-published versions, workspace installations, details and history.

docs/skills.md. Publishing and rolling back an installation to an earlier version need the
organization administrator role; installing, enabling, disabling and uninstalling are
workspace-owner actions; every member can read. Every change is audited in the same
transaction (``audit_records``), and a skill's history is read back from those records.

Versions are signed with Ed25519 over a canonical package digest (``skill_signing``,
docs/skills.md#signing). Organization administrators manage the tenant's trusted publisher
keys; an unsigned or unverifiable version is never installed, upgraded to or rolled back to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .skill_signing import (
    KEY_ID_PATTERN,
    PublisherKey,
    PublisherKeyStatus,
    SigningError,
    SkillSignature,
    SkillVerification,
    key_fingerprint,
    normalize_public_key,
    platform_keys,
    verify,
)

if TYPE_CHECKING:
    from .agent_tools import ToolDefinition


class SkillVersionCreate(BaseModel):
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=50_000)
    required_tools: list[str] = Field(default_factory=list, max_length=100)
    risk_level: RiskLevel = RiskLevel.LOW
    # Ed25519 signature over the canonical package digest (docs/skills.md#signing). A version
    # can be published unsigned and signed later; it cannot be installed until it verifies.
    signature: SkillSignature | None = None


class SkillVersion(SkillVersionCreate):
    id: str
    publisher_tenant_id: str
    created_by: str
    created_at: datetime
    signed_at: datetime | None = None
    signed_by: str | None = None
    # Computed on every read against the trusted keys as they are now; never stored.
    verification: SkillVerification | None = None


class PublisherKeyCreate(BaseModel):
    id: str = Field(pattern=KEY_ID_PATTERN)
    publisher: str = Field(min_length=1, max_length=160)
    public_key: str = Field(min_length=1, max_length=100)


class PublisherKeyRotate(BaseModel):
    new_id: str = Field(pattern=KEY_ID_PATTERN)
    public_key: str = Field(min_length=1, max_length=100)


class PublisherKeyRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class PublisherKeyList(BaseModel):
    keys: list[PublisherKey]
    platform_keys: list[PublisherKey]
    can_manage: bool


class PublisherKeyRevoked(BaseModel):
    key: PublisherKey
    # Versions of this tenant signed with the key: no longer installable.
    affected_versions: int
    # Installations in this workspace now flagged.
    flagged_installations: int
    # Integration packages of this tenant signed with the key: no longer installable.
    affected_packages: int = 0
    # Whether the tenant's "disable flagged installs" setting was on.
    auto_disable: bool = True
    # Skill installations and integration installs turned off, in every workspace of the tenant.
    disabled_installations: int = 0


class SkillInstallCreate(BaseModel):
    skill_id: str
    version: str
    approved_tools: list[str] = Field(default_factory=list, max_length=100)


class SkillInstallation(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    skill_version_id: str
    skill_id: str
    version: str
    approved_tools: list[str]
    enabled: bool
    installed_by: str
    installed_at: datetime
    # Why the API turned the installation off (a revoked signing key), if it did.
    disabled_reason: str | None = None
    # The installed version's signature check, computed on read: anything other than
    # ``verified`` flags the installation (for example after its key was revoked).
    verification: SkillVerification | None = None


class SkillResolveRequest(BaseModel):
    skill_id: str
    available_tools: list[str] = Field(default_factory=list, max_length=200)
    maximum_risk: RiskLevel = RiskLevel.HIGH


class SkillInstallationUpdate(BaseModel):
    enabled: bool
    # Required to turn on an installation whose version no longer verifies (a revoked key).
    acknowledge_flagged: bool = False


class SigningSettings(BaseModel):
    """The tenant's signing settings (docs/skills.md#signing)."""

    # Turn off, in every workspace, each installation a revoked key signed. On by default.
    disable_flagged: bool = True
    updated_by: str | None = None
    updated_at: datetime | None = None


class SigningSettingsUpdate(BaseModel):
    disable_flagged: bool


class SigningSettingsView(SigningSettings):
    can_manage: bool = False


class SkillRollbackRequest(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class SkillToolView(BaseModel):
    """One tool a skill version requires, as this deployment's runtime would treat it."""

    name: str
    registered: bool
    description: str | None = None
    risk_level: RiskLevel | None = None
    # always | policy | rule | blocked | unavailable: when a call needs a human approval.
    approval: str
    approval_text: str
    # The integration host the tool contacts, if any (threat model G2).
    target: str | None = None
    # Granted to agents in this workspace by the installation.
    granted: bool = False


class SkillHistoryEntry(BaseModel):
    id: str
    action: str
    actor: str
    outcome: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillDetail(BaseModel):
    skill_id: str
    name: str
    description: str
    latest_version: str
    # Newest first.
    versions: list[SkillVersion]
    installation: SkillInstallation | None
    # The tools of the installed version, or of the latest one when not installed.
    tools_version: str
    tools: list[SkillToolView]
    history: list[SkillHistoryEntry]
    can_manage: bool
    can_rollback: bool


class SkillResolution(BaseModel):
    installation_id: str
    skill_version_id: str
    skill_id: str
    version: str
    instructions: str
    granted_tools: list[str]
    risk_level: RiskLevel


class SkillVersionExistsError(ValueError):
    """A tenant can publish a given skill version only once (versions are immutable)."""


class PublisherKeyExistsError(ValueError):
    """A key id is used once per tenant, including rotated and revoked keys."""


class SkillRegistryStore(Protocol):
    """Skill versions (tenant-owned) and installations (per workspace).

    Every call is scoped by an explicit ``TenantContext``. The PostgreSQL store
    (``anum_api.db.skills_repository``) additionally runs inside the tenant's RLS scope.
    """

    def add_version(self, version: SkillVersion) -> SkillVersion: ...
    def get_version(self, context: TenantContext, skill_id: str, version: str) -> SkillVersion | None: ...
    def list_versions(self, context: TenantContext) -> list[SkillVersion]: ...
    def save_installation(self, installation: SkillInstallation) -> SkillInstallation: ...
    def get_installation(self, context: TenantContext, skill_id: str) -> SkillInstallation | None: ...
    def list_installations(self, context: TenantContext) -> list[SkillInstallation]: ...
    def delete_installation(self, context: TenantContext, skill_id: str) -> bool: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...
    def skill_history(self, context: TenantContext, skill_id: str, limit: int) -> list[AuditRecord]: ...
    def list_publisher_keys(self, context: TenantContext) -> list[PublisherKey]: ...
    def get_signing_settings(self, context: TenantContext) -> SigningSettings | None: ...
    def save_signing_settings(self, context: TenantContext, value: SigningSettings) -> SigningSettings: ...
    def add_publisher_key(self, key: PublisherKey) -> PublisherKey: ...
    def save_publisher_key(self, key: PublisherKey) -> PublisherKey: ...
    def set_signature(
        self, context: TenantContext, skill_id: str, version: str, signature: SkillSignature,
        signed_at: datetime, signed_by: str,
    ) -> SkillVersion | None: ...


class SkillStore:
    """In-memory skill registry for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str, str], SkillVersion] = {}
        self.installations: dict[tuple[str, str, str], SkillInstallation] = {}
        self.publisher_keys: dict[tuple[str, str], PublisherKey] = {}
        self.signing_settings: dict[str, SigningSettings] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.versions.clear()
            self.installations.clear()
            self.publisher_keys.clear()
            self.signing_settings.clear()

    def get_signing_settings(self, context: TenantContext) -> SigningSettings | None:
        with self._lock:
            found = self.signing_settings.get(context.tenant_id)
            return found.model_copy() if found else None

    def save_signing_settings(self, context: TenantContext, value: SigningSettings) -> SigningSettings:
        with self._lock:
            self.signing_settings[context.tenant_id] = value.model_copy()
        return value

    def list_publisher_keys(self, context: TenantContext) -> list[PublisherKey]:
        with self._lock:
            keys = [k.model_copy() for (tenant, _), k in self.publisher_keys.items() if tenant == context.tenant_id]
        return sorted(keys, key=lambda k: (k.created_at, k.id))

    def add_publisher_key(self, key: PublisherKey) -> PublisherKey:
        with self._lock:
            if (key.tenant_id or "", key.id) in self.publisher_keys:
                raise PublisherKeyExistsError(key.id)
            self.publisher_keys[(key.tenant_id or "", key.id)] = key.model_copy()
        return key

    def save_publisher_key(self, key: PublisherKey) -> PublisherKey:
        with self._lock:
            self.publisher_keys[(key.tenant_id or "", key.id)] = key.model_copy()
        return key

    def set_signature(
        self, context: TenantContext, skill_id: str, version: str, signature: SkillSignature,
        signed_at: datetime, signed_by: str,
    ) -> SkillVersion | None:
        key = (context.tenant_id, skill_id, version)
        with self._lock:
            current = self.versions.get(key)
            if current is None:
                return None
            updated = current.model_copy(update={"signature": signature, "signed_at": signed_at, "signed_by": signed_by})
            self.versions[key] = updated
        return updated

    def add_version(self, version: SkillVersion) -> SkillVersion:
        key = (version.publisher_tenant_id, version.skill_id, version.version)
        with self._lock:
            if key in self.versions:
                raise SkillVersionExistsError(version.skill_id)
            self.versions[key] = version
        return version

    def get_version(self, context: TenantContext, skill_id: str, version: str) -> SkillVersion | None:
        return self.versions.get((context.tenant_id, skill_id, version))

    def list_versions(self, context: TenantContext) -> list[SkillVersion]:
        with self._lock:
            return [v for (tenant, _, _), v in self.versions.items() if tenant == context.tenant_id]

    def save_installation(self, installation: SkillInstallation) -> SkillInstallation:
        with self._lock:
            self.installations[(installation.tenant_id, installation.workspace_id, installation.skill_id)] = installation
        return installation

    def get_installation(self, context: TenantContext, skill_id: str) -> SkillInstallation | None:
        return self.installations.get((context.tenant_id, context.workspace_id, skill_id))

    def list_installations(self, context: TenantContext) -> list[SkillInstallation]:
        with self._lock:
            return [i for (tenant, workspace, _), i in self.installations.items()
                    if tenant == context.tenant_id and workspace == context.workspace_id]

    def delete_installation(self, context: TenantContext, skill_id: str) -> bool:
        with self._lock:
            return self.installations.pop((context.tenant_id, context.workspace_id, skill_id), None) is not None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        # The same in-memory recorder the audit export reads.
        from .governance import governance_store

        return governance_store.record_audit(record)

    def skill_history(self, context: TenantContext, skill_id: str, limit: int) -> list[AuditRecord]:
        from .governance import governance_store

        target = skill_target(skill_id)
        records = [record for record in governance_store.query_audit(context) if record.target == target]
        records.sort(key=lambda record: (record.created_at, record.id), reverse=True)
        return records[:limit]


skill_store = SkillStore()


def skill_target(skill_id: str) -> str:
    return f"skill:{skill_id}"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


# The runtime's tool registry, set by the API at startup (anum_api.main). Skill details use
# it to say which required tools exist here, their risk and when they need approval.
_tool_lookup: Callable[[str], ToolDefinition | None] | None = None


def configure_tool_catalog(lookup: Callable[[str], ToolDefinition | None] | None) -> None:
    global _tool_lookup
    _tool_lookup = lookup


def describe_tool(name: str, granted: bool) -> SkillToolView:
    definition = _tool_lookup(name) if _tool_lookup is not None else None
    if definition is None:
        return SkillToolView(
            name=name, registered=False, approval="unavailable", granted=granted,
            approval_text="Not available in this deployment: a skill that requires it cannot run here until the operator adds the tool.",
        )
    risk = definition.risk_level
    roles = f" Only {', '.join(sorted(definition.required_roles))} roles may run it." if definition.required_roles else ""
    if risk == RiskLevel.BLOCKED:
        approval, text = "blocked", "Blocked: the runtime never runs it."
    elif risk == RiskLevel.HIGH:
        approval, text = "always", "Every call waits for a human approval."
    elif risk == RiskLevel.MEDIUM:
        approval, text = "policy", "Needs approval when this workspace's approval policy covers medium risk."
    else:
        approval, text = "rule", "Runs without approval unless an organization approval rule or policy pack asks for one."
    return SkillToolView(
        name=name, registered=True, description=definition.description, risk_level=risk,
        approval=approval, approval_text=text + roles, target=definition.target, granted=granted,
    )


def _audit(
    store: SkillRegistryStore, context: TenantContext, action: str, skill_id: str, metadata: dict[str, Any],
    *, target: str | None = None, outcome: str = "success",
) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action=action, target=target or skill_target(skill_id), outcome=outcome,
            correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
        )
    )


def key_target(key_id: str) -> str:
    return f"skill_publisher_key:{key_id}"


def trusted_keys(store: SkillRegistryStore, context: TenantContext) -> list[PublisherKey]:
    """Platform keys first, then the tenant's own (ids never collide: adding one checks)."""
    return [*platform_keys(), *store.list_publisher_keys(context)]


def verification_of(
    version: SkillVersionCreate, keys: list[PublisherKey], *, signature: SkillSignature | None = None,
    signed_at: datetime | None = None,
) -> SkillVerification:
    stored_at = getattr(version, "signed_at", None)
    return verify(
        skill_id=version.skill_id, version=version.version, name=version.name, description=version.description,
        instructions=version.instructions, required_tools=list(version.required_tools),
        risk_level=version.risk_level.value, signature=signature or version.signature,
        signed_at=signed_at or stored_at, keys=keys,
    )


def with_verification(version: SkillVersion, keys: list[PublisherKey]) -> SkillVersion:
    return version.model_copy(update={"verification": verification_of(version, keys)})


def _signing_metadata(check: SkillVerification) -> dict[str, Any]:
    return {
        "digest": check.digest, "signature_status": check.status.value, "key_id": check.key_id,
        "signer": check.signer, "key_source": check.key_source.value if check.key_source else None,
    }


def _installation_view(
    installation: SkillInstallation, versions: dict[str, SkillVersion], keys: list[PublisherKey]
) -> SkillInstallation:
    version = versions.get(installation.version)
    if version is None:
        return installation
    return installation.model_copy(update={"verification": verification_of(version, keys)})


@contextmanager
def open_skill_store(context: TenantContext) -> Iterator[SkillRegistryStore]:
    """The skill registry for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.skills_repository import SqlAlchemySkillStore

        return SqlAlchemySkillStore(session)

    with open_scoped_store(context, skill_store, sql_store) as store:
        yield store


router = APIRouter(prefix="/api/v1", tags=["skills"])
_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.BLOCKED: 3}


@router.post("/skills/versions", response_model=SkillVersion, status_code=status.HTTP_201_CREATED)
def publish_skill(payload: SkillVersionCreate, context: TenantContext = Depends(tenant_context)) -> SkillVersion:
    # A published version is tenant-owned and installable in every workspace, so publishing
    # needs the organization administrator role; installing stays a workspace-owner action.
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    now = utc_now()
    signed = payload.signature is not None
    version = SkillVersion(id=new_id("skill_version"), publisher_tenant_id=context.tenant_id,
                           created_by=context.user_id, created_at=now,
                           signed_at=now if signed else None, signed_by=context.user_id if signed else None,
                           **payload.model_dump())
    refused: SkillVerification | None = None
    try:
        with open_skill_store(context) as store:
            keys = trusted_keys(store, context)
            check = verification_of(version, keys)
            if signed and not check.installable:
                # A bad signature is refused outright: nothing is published, the attempt is audited.
                refused = check
                _audit(store, context, "skill.signature_refused", version.skill_id, {
                    "version": version.version, **_signing_metadata(check)}, outcome="denied")
            else:
                result = store.add_version(version)
                _audit(store, context, "skill.published", version.skill_id, {
                    "version": version.version, "risk_level": version.risk_level.value,
                    "required_tools": list(version.required_tools), **_signing_metadata(check),
                })
                return result.model_copy(update={"verification": check})
    except SkillVersionExistsError as exc:
        raise HTTPException(409, "Skill version already exists") from exc
    raise HTTPException(422, f"Signature refused: {refused.detail if refused else 'it does not verify'}")


@router.put("/skills/versions/{skill_id}/{version}/signature", response_model=SkillVersion)
def sign_skill_version(
    skill_id: str, version: str, payload: SkillSignature, context: TenantContext = Depends(tenant_context)
) -> SkillVersion:
    """Attach a signature to a published version that is unsigned or no longer verifies.

    The content never changes (versions are immutable): the publisher signs the digest
    shown in ``verification.digest`` offline and an organization administrator attaches it.
    """
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    refused: SkillVerification | None = None
    with open_skill_store(context) as store:
        current = store.get_version(context, skill_id, version)
        if current is None:
            raise HTTPException(404, "Skill version not found")
        keys = trusted_keys(store, context)
        before = verification_of(current, keys)
        if before.installable:
            raise HTTPException(409, "This version is already signed and verified")
        now = utc_now()
        check = verification_of(current, keys, signature=payload, signed_at=now)
        if not check.installable:
            refused = check
            _audit(store, context, "skill.signature_refused", skill_id, {
                "version": version, **_signing_metadata(check)}, outcome="denied")
        else:
            updated = store.set_signature(context, skill_id, version, payload, now, context.user_id)
            if updated is None:
                raise HTTPException(404, "Skill version not found")
            _audit(store, context, "skill.signed", skill_id, {
                "version": version, "previous_status": before.status.value, **_signing_metadata(check)})
            return updated.model_copy(update={"verification": check})
    raise HTTPException(422, f"Signature refused: {refused.detail if refused else 'it does not verify'}")


@router.get("/skills/publisher-keys", response_model=PublisherKeyList)
def list_publisher_keys(context: TenantContext = Depends(tenant_context)) -> PublisherKeyList:
    """The tenant's trusted publisher keys (public keys only) and the platform's, any member."""
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        keys = store.list_publisher_keys(context)
    return PublisherKeyList(
        keys=keys, platform_keys=list(platform_keys()),
        can_manage=has_permission(context, Permission.ORG_POLICY_MANAGE),
    )


def _new_key(context: TenantContext, key_id: str, publisher: str, public_key: str, existing: list[PublisherKey]) -> PublisherKey:
    try:
        normalized = normalize_public_key(public_key)
    except SigningError as exc:
        raise HTTPException(422, str(exc)) from exc
    if any(key.id == key_id for key in platform_keys()) or any(key.id == key_id for key in existing):
        raise HTTPException(409, f"Key id {key_id} is already used; choose another id")
    fingerprint = key_fingerprint(normalized)
    clash = next((key for key in existing if key.fingerprint == fingerprint and key.status != PublisherKeyStatus.REVOKED), None)
    if clash is not None:
        raise HTTPException(409, f"This public key is already trusted as {clash.id}")
    if any(key.public_key == normalized for key in existing):
        raise HTTPException(409, "This public key was revoked here and cannot be trusted again")
    return PublisherKey(
        id=key_id, tenant_id=context.tenant_id, publisher=publisher.strip(), public_key=normalized,
        fingerprint=fingerprint, created_by=context.user_id, created_at=utc_now(),
    )


@router.post("/skills/publisher-keys", response_model=PublisherKey, status_code=status.HTTP_201_CREATED)
def add_publisher_key(payload: PublisherKeyCreate, context: TenantContext = Depends(tenant_context)) -> PublisherKey:
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    try:
        with open_skill_store(context) as store:
            key = _new_key(context, payload.id, payload.publisher, payload.public_key, store.list_publisher_keys(context))
            store.add_publisher_key(key)
            _audit(store, context, "skill.publisher_key_added", "", {
                "key_id": key.id, "publisher": key.publisher, "fingerprint": key.fingerprint,
            }, target=key_target(key.id))
            return key
    except PublisherKeyExistsError as exc:
        raise HTTPException(409, f"Key id {payload.id} is already used; choose another id") from exc


@router.post("/skills/publisher-keys/{key_id}/rotate", response_model=PublisherKey, status_code=status.HTTP_201_CREATED)
def rotate_publisher_key(key_id: str, payload: PublisherKeyRotate, context: TenantContext = Depends(tenant_context)) -> PublisherKey:
    """Replace an active key: the old one keeps verifying what it signed before now."""
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    try:
        with open_skill_store(context) as store:
            existing = store.list_publisher_keys(context)
            old = next((key for key in existing if key.id == key_id), None)
            if old is None:
                raise HTTPException(404, "Publisher key not found")
            if old.status != PublisherKeyStatus.ACTIVE:
                raise HTTPException(409, f"Only an active key can be rotated; {key_id} is {old.status.value}")
            new = _new_key(context, payload.new_id, old.publisher, payload.public_key, existing)
            store.add_publisher_key(new)
            store.save_publisher_key(old.model_copy(update={
                "status": PublisherKeyStatus.ROTATED, "rotated_at": new.created_at, "replaced_by": new.id,
            }))
            _audit(store, context, "skill.publisher_key_rotated", "", {
                "key_id": key_id, "new_key_id": new.id, "publisher": old.publisher,
                "fingerprint": old.fingerprint, "new_fingerprint": new.fingerprint,
            }, target=key_target(key_id))
            return new
    except PublisherKeyExistsError as exc:
        raise HTTPException(409, f"Key id {payload.new_id} is already used; choose another id") from exc


@router.post("/skills/publisher-keys/{key_id}/revoke", response_model=PublisherKeyRevoked)
def revoke_publisher_key(key_id: str, payload: PublisherKeyRevoke, context: TenantContext = Depends(tenant_context)) -> PublisherKeyRevoked:
    """Stop trusting a key: its versions cannot be installed and installed ones are flagged.

    With the tenant's ``disable_flagged`` setting on (the default), every installation the key
    signed is then turned off in every workspace, audited there, and the owners are notified.
    """
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    from .phase5 import open_scale_store, requires_signature

    with open_scale_store(context) as scale:
        signed_packages = {p.id for p in scale.catalog(context)
                           if requires_signature(p) and p.signature is not None and p.signature.key_id == key_id}
    with open_skill_store(context) as store:
        old = next((key for key in store.list_publisher_keys(context) if key.id == key_id), None)
        if old is None:
            raise HTTPException(404, "Publisher key not found")
        if old.status == PublisherKeyStatus.REVOKED:
            raise HTTPException(409, f"Key {key_id} is already revoked")
        revoked = old.model_copy(update={
            "status": PublisherKeyStatus.REVOKED, "revoked_at": utc_now(), "revoked_by": context.user_id,
            "revoke_reason": payload.reason.strip(),
        })
        store.save_publisher_key(revoked)
        signed = {(v.skill_id, v.version) for v in store.list_versions(context)
                  if v.signature is not None and v.signature.key_id == key_id}
        flagged = [i for i in store.list_installations(context) if (i.skill_id, i.version) in signed]
        for installation in flagged:
            _audit(store, context, "skill.installation_flagged", installation.skill_id, {
                "version": installation.version, "key_id": key_id, "reason": revoked.revoke_reason,
            }, outcome="flagged")
        auto_disable = (store.get_signing_settings(context) or SigningSettings()).disable_flagged
        _audit(store, context, "skill.publisher_key_revoked", "", {
            "key_id": key_id, "publisher": old.publisher, "fingerprint": old.fingerprint,
            "reason": revoked.revoke_reason, "affected_versions": len(signed),
            "affected_packages": len(signed_packages), "flagged_installations": len(flagged),
            "auto_disable": auto_disable,
        }, target=key_target(key_id))
    disabled = 0
    if auto_disable and (signed or signed_packages):
        # After the revocation commits: each workspace in its own short unit of work.
        from .skill_flagging import disable_signed_installs

        disabled = disable_signed_installs(context, key_id, revoked.revoke_reason or "", signed, signed_packages)
    return PublisherKeyRevoked(
        key=revoked, affected_versions=len(signed), flagged_installations=len(flagged),
        affected_packages=len(signed_packages), auto_disable=auto_disable, disabled_installations=disabled,
    )


@router.get("/skills/versions", response_model=list[SkillVersion])
def list_skill_versions(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> list[SkillVersion]:
    """Published versions, oldest first; ``q`` matches skill id, name or description."""
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        versions = store.list_versions(context)
        keys = trusted_keys(store, context)
    needle = (q or "").strip().casefold()
    if needle:
        versions = [
            v for v in versions
            if needle in v.skill_id.casefold() or needle in v.name.casefold() or needle in v.description.casefold()
        ]
    return [with_verification(v, keys) for v in versions[offset:offset + limit]]


@router.post("/skills/installations", response_model=SkillInstallation, status_code=201)
def install_skill(payload: SkillInstallCreate, context: TenantContext = Depends(tenant_context)) -> SkillInstallation:
    require_permission(context, Permission.POLICY_MANAGE)
    refused: SkillVerification | None = None
    with open_skill_store(context) as store:
        version = store.get_version(context, payload.skill_id, payload.version)
        if version is None:
            raise HTTPException(404, "Skill version not found")
        approved = sorted(set(payload.approved_tools))
        if not set(approved) <= set(version.required_tools):
            raise HTTPException(422, "Approved tools must be declared by the skill")
        previous = store.get_installation(context, payload.skill_id)
        check = verification_of(version, trusted_keys(store, context))
        if not check.installable:
            # Install and upgrade both land here: the refusal is audited and committed, then raised.
            refused = check
            _audit(store, context, "skill.install_refused", version.skill_id, {
                "version": version.version, "previous_version": previous.version if previous else None,
                **_signing_metadata(check)}, outcome="denied")
        else:
            result = store.save_installation(SkillInstallation(
                id=new_id("skill_install"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                skill_version_id=version.id, skill_id=version.skill_id, version=version.version,
                approved_tools=approved, enabled=True, installed_by=context.user_id, installed_at=utc_now(),
            ))
            _audit(store, context, "skill.installed", version.skill_id, {
                "version": version.version, "approved_tools": approved,
                "previous_version": previous.version if previous else None, **_signing_metadata(check),
            })
            return result.model_copy(update={"verification": check})
    reason = refused.detail if refused else "its signature does not verify."
    raise HTTPException(409, f"Skill {payload.skill_id} {payload.version} cannot be installed: {reason}")


@router.get("/skills/installations", response_model=list[SkillInstallation])
def list_installations(context: TenantContext = Depends(tenant_context)) -> list[SkillInstallation]:
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        installations = store.list_installations(context)
        keys = trusted_keys(store, context)
        versions = {(v.skill_id, v.version): v for v in store.list_versions(context)}
    return [
        _installation_view(i, {i.version: versions[(i.skill_id, i.version)]} if (i.skill_id, i.version) in versions else {}, keys)
        for i in installations
    ]


@router.patch("/skills/installations/{skill_id}", response_model=SkillInstallation)
def update_installation(skill_id: str, payload: SkillInstallationUpdate, context: TenantContext = Depends(tenant_context)) -> SkillInstallation:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None:
            raise HTTPException(404, "Skill installation not found")
        version = store.get_version(context, skill_id, installation.version)
        check = verification_of(version, trusted_keys(store, context)) if version is not None else None
        flagged = check is not None and not check.installable
        if payload.enabled and flagged and not payload.acknowledge_flagged:
            # An explicit owner decision: agents would load instructions no trusted key vouches for.
            raise HTTPException(409, f"{skill_id} {installation.version} no longer verifies: {check.detail if check else ''} "
                                     "Confirm that you want to turn it on anyway.")
        result = store.save_installation(installation.model_copy(update={
            "enabled": payload.enabled,
            "disabled_reason": None if payload.enabled else "Turned off by a workspace owner.",
        }))
        action = ("skill.flagged_reenabled" if flagged else "skill.enabled") if payload.enabled else "skill.disabled"
        _audit(store, context, action, skill_id, {
            "version": installation.version, **(_signing_metadata(check) if check else {})})
        return result.model_copy(update={"verification": check})


@router.get("/skills/signing-settings", response_model=SigningSettingsView)
def get_signing_settings(context: TenantContext = Depends(tenant_context)) -> SigningSettingsView:
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        current = store.get_signing_settings(context) or SigningSettings()
    return SigningSettingsView(**current.model_dump(), can_manage=has_permission(context, Permission.ORG_POLICY_MANAGE))


@router.put("/skills/signing-settings", response_model=SigningSettingsView)
def update_signing_settings(payload: SigningSettingsUpdate, context: TenantContext = Depends(tenant_context)) -> SigningSettingsView:
    """Organization administrators choose whether revocation turns flagged installations off."""
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    with open_skill_store(context) as store:
        before = store.get_signing_settings(context) or SigningSettings()
        saved = store.save_signing_settings(context, SigningSettings(
            disable_flagged=payload.disable_flagged, updated_by=context.user_id, updated_at=utc_now()))
        _audit(store, context, "skill.signing_settings_updated", "", {
            "disable_flagged": payload.disable_flagged, "previous": before.disable_flagged,
        }, target="skill_signing_settings")
    return SigningSettingsView(**saved.model_dump(), can_manage=True)


@router.post("/skills/installations/{skill_id}/rollback", response_model=SkillInstallation)
def rollback_installation(
    skill_id: str, payload: SkillRollbackRequest, context: TenantContext = Depends(tenant_context)
) -> SkillInstallation:
    """Move this workspace's installation back to an earlier published version.

    An organization administrator's action: it changes which reviewed instructions agents
    follow. Tool grants never widen: the installation keeps only the grants it already had
    that the earlier version also declares. The enabled switch is kept.
    """
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    refused: SkillVerification | None = None
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None:
            raise HTTPException(404, "Skill installation not found")
        target = store.get_version(context, skill_id, payload.version)
        if target is None:
            raise HTTPException(404, "Skill version not found")
        if version_key(target.version) >= version_key(installation.version):
            raise HTTPException(422, "Roll back only to an earlier version; install a newer version from Skills")
        check = verification_of(target, trusted_keys(store, context))
        if not check.installable:
            refused = check
            _audit(store, context, "skill.rollback_refused", skill_id, {
                "from_version": installation.version, "to_version": target.version, **_signing_metadata(check),
            }, outcome="denied")
        else:
            approved = sorted(set(installation.approved_tools) & set(target.required_tools))
            result = store.save_installation(installation.model_copy(update={
                "skill_version_id": target.id, "version": target.version, "approved_tools": approved,
                "installed_by": context.user_id, "installed_at": utc_now(),
            }))
            _audit(store, context, "skill.rolled_back", skill_id, {
                "from_version": installation.version, "to_version": target.version, "approved_tools": approved,
                **_signing_metadata(check),
            })
            return result.model_copy(update={"verification": check})
    reason = refused.detail if refused else "its signature does not verify."
    raise HTTPException(409, f"Cannot roll back to {payload.version}: only a signed version can be rolled back to. {reason}")


@router.delete("/skills/installations/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def uninstall_skill(skill_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.POLICY_MANAGE)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, skill_id)
        if installation is None or not store.delete_installation(context, skill_id):
            raise HTTPException(404, "Skill installation not found")
        _audit(store, context, "skill.uninstalled", skill_id, {"version": installation.version})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/skills/resolve", response_model=SkillResolution)
def resolve_skill(payload: SkillResolveRequest, context: TenantContext = Depends(tenant_context)) -> SkillResolution:
    require_permission(context, Permission.TASK_RUN)
    with open_skill_store(context) as store:
        installation = store.get_installation(context, payload.skill_id)
        if installation is None or not installation.enabled:
            raise HTTPException(404, "Enabled skill installation not found")
        version = store.get_version(context, payload.skill_id, installation.version)
    if version is None:
        raise HTTPException(404, "Enabled skill installation not found")
    if _RISK_ORDER[version.risk_level] > _RISK_ORDER[payload.maximum_risk]:
        raise HTTPException(403, "Skill exceeds the execution risk boundary")
    granted = set(installation.approved_tools) & set(payload.available_tools)
    if set(version.required_tools) - granted:
        raise HTTPException(409, "Required tools are not available and approved")
    return SkillResolution(
        installation_id=installation.id, skill_version_id=version.id, skill_id=version.skill_id,
        version=version.version, instructions=version.instructions,
        granted_tools=sorted(granted), risk_level=version.risk_level,
    )


# Registered last so /skills/versions and /skills/installations keep their own routes.
@router.get("/skills/{skill_id}", response_model=SkillDetail)
def skill_detail(
    skill_id: str,
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+\.\d+$"),
    context: TenantContext = Depends(tenant_context),
) -> SkillDetail:
    """Everything a reviewer needs: versions, the installation, tools with risk and approval, history.

    ``tools`` describe ``version`` when given (what installing it would grant), else the
    installed version, else the latest.
    """
    require_permission(context, Permission.POLICY_READ)
    with open_skill_store(context) as store:
        versions = [v for v in store.list_versions(context) if v.skill_id == skill_id]
        installation = store.get_installation(context, skill_id)
        history = store.skill_history(context, skill_id, 50)
        keys = trusted_keys(store, context)
    if not versions:
        raise HTTPException(404, "Skill not found")
    versions = [with_verification(v, keys) for v in versions]
    if installation is not None:
        installation = _installation_view(installation, {v.version: v for v in versions}, keys)
    versions.sort(key=lambda v: version_key(v.version), reverse=True)
    latest = versions[0]
    wanted = version or (installation.version if installation else None)
    shown = next((v for v in versions if v.version == wanted), None)
    if shown is None:
        if version is not None:
            raise HTTPException(404, "Skill version not found")
        shown = latest
    # Granted only for the installed version's own tools.
    granted = set(installation.approved_tools) if installation and installation.version == shown.version else set()
    return SkillDetail(
        skill_id=skill_id, name=shown.name, description=shown.description,
        latest_version=latest.version, versions=versions, installation=installation,
        tools_version=shown.version,
        tools=[describe_tool(name, name in granted) for name in sorted(set(shown.required_tools))],
        history=[
            SkillHistoryEntry(
                id=record.id, action=record.action, actor=record.actor, outcome=record.outcome,
                created_at=record.created_at, metadata=_plain(record.metadata),
            )
            for record in history
        ],
        can_manage=has_permission(context, Permission.POLICY_MANAGE),
        can_rollback=has_permission(context, Permission.ORG_POLICY_MANAGE),
    )


def _plain(value: Any) -> Any:
    """Audit metadata is a read-only mapping; give the response plain JSON values."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value
