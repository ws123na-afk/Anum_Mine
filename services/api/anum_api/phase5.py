from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings
from .skill_signing import (
    INTEGRATION_SIGNATURE_CONTEXT,
    PublisherKey,
    SkillSignature,
    SkillVerification,
    integration_digest,
    verify_digest,
)


class MarketplaceKind(StrEnum):
    SKILL = "skill"
    INTEGRATION = "integration"


class MarketplacePackage(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,79}$")
    name: str = Field(min_length=1, max_length=160)
    kind: MarketplaceKind
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    publisher: str = Field(min_length=1, max_length=160)
    verified: bool
    # What installing grants; shown to the owner before they install.
    permissions: list[str] = Field(max_length=50)
    regions: list[str] = Field(max_length=20)
    # Integration packages are signed like skill versions (docs/governance-and-scale.md#package-signing):
    # Ed25519 over the canonical integration digest, by a trusted publisher or platform key.
    signature: SkillSignature | None = None
    # Set by the API when a new signature is stored; ignored on input.
    signed_at: datetime | None = None
    signed_by: str | None = None
    # Computed on every read for integration packages; ignored on input, never stored.
    verification: SkillVerification | None = None


class PackageInstall(BaseModel):
    package_id: str
    tenant_id: str
    workspace_id: str
    version: str
    enabled: bool = True
    installed_by: str
    installed_at: datetime
    # Why the install is off, when the API turned it off (a revoked signing key).
    disabled_reason: str | None = None
    # The package's signature check (integration packages), computed on read.
    verification: SkillVerification | None = None


class InstallRequest(BaseModel):
    version: str | None = None


class PackageInstallUpdate(BaseModel):
    enabled: bool
    # Required to turn on an install whose package no longer verifies (a revoked key).
    acknowledge_flagged: bool = False


class RegionStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    # Configured but never probed: ANUM does not claim health it has not measured.
    UNVERIFIED = "unverified"


class RoutingTarget(BaseModel):
    id: str
    region: str
    provider: str
    model: str
    status: RegionStatus = RegionStatus.HEALTHY
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    sensitivity: list[str] = Field(default_factory=lambda: ["standard"])
    cost_per_1k_tokens: float = Field(ge=0)
    latency_ms: int = Field(gt=0)


class RoutingRequest(BaseModel):
    modality: str = "text"
    sensitivity: str = "standard"
    preferred_region: str | None = None
    max_cost_per_1k_tokens: float | None = Field(default=None, ge=0)
    max_latency_ms: int | None = Field(default=None, gt=0)


class RoutingDecision(BaseModel):
    target: RoutingTarget
    reason: str
    failover_target_ids: list[str]


class EnterpriseOperations(BaseModel):
    tenant_id: str
    active_regions: int
    healthy_targets: int
    degraded_targets: int
    installed_packages: int
    failover_ready: bool
    generated_at: datetime


# The marketplace starts empty. Packages appear only when an organization administrator
# publishes one to the tenant's catalog (PUT /marketplace/packages/{id}); ANUM never shows
# invented listings, and one tenant's packages are never visible to another.


def configured_targets() -> tuple[RoutingTarget, ...]:
    """The one model this deployment is actually configured to use.

    Cost and latency are not measured here, so they carry placeholder minimums
    (0 cost, 1 ms) and the target is reported as ``unverified`` (except the
    built-in mock, which always answers). Clients must show unverified targets
    as "not measured", never as real numbers. Organization administrators add
    real regional targets with PUT /routing/targets/{id}.
    """
    provider = settings.model_provider
    return (
        RoutingTarget(
            id="configured-model",
            region="local",
            provider=provider,
            model=settings.model_name if provider != "mock" else "anum-mock-planner",
            status=RegionStatus.HEALTHY if provider == "mock" else RegionStatus.UNVERIFIED,
            cost_per_1k_tokens=0.0,
            latency_ms=1,
        ),
    )


class PackageInstalledError(ValueError):
    """A package installed in any workspace of the tenant cannot leave the catalog."""


def requires_signature(package: MarketplacePackage) -> bool:
    """Integration packages must verify before install; skill packages are signed in the skill registry."""
    return package.kind == MarketplaceKind.INTEGRATION


def package_digest(package: MarketplacePackage) -> str:
    return integration_digest(
        package_id=package.id, name=package.name, kind=package.kind.value, version=package.version,
        publisher=package.publisher, permissions=list(package.permissions), regions=list(package.regions),
    )


def package_verification(
    package: MarketplacePackage, keys: list[PublisherKey], *, signature: SkillSignature | None = None,
    signed_at: datetime | None = None,
) -> SkillVerification:
    return verify_digest(
        digest=package_digest(package), signature=signature or package.signature,
        signed_at=signed_at or package.signed_at, keys=keys, context=INTEGRATION_SIGNATURE_CONTEXT, what="package",
    )


def with_package_verification(package: MarketplacePackage, keys: list[PublisherKey]) -> MarketplacePackage:
    if not requires_signature(package):
        return package.model_copy(update={"verification": None})
    return package.model_copy(update={"verification": package_verification(package, keys)})


def tenant_trusted_keys(context: TenantContext) -> list[PublisherKey]:
    """The tenant's publisher keys and the platform's, read in their own short unit of work."""
    from .skills_api import open_skill_store, trusted_keys

    with open_skill_store(context) as skills:
        return trusted_keys(skills, context)


def merge_targets(overrides: list[RoutingTarget]) -> list[RoutingTarget]:
    """The configured default target (unless overridden by id) followed by the tenant's own."""
    by_id = {target.id: target for target in overrides}
    defaults = configured_targets()
    default_ids = {default.id for default in defaults}
    return [by_id.get(target.id, target) for target in defaults] + [
        target for target in overrides if target.id not in default_ids
    ]


class ScaleStore(Protocol):
    """The tenant's marketplace catalog, workspace installs and tenant routing targets.

    The PostgreSQL store (``anum_api.db.marketplace_repository``) runs inside the tenant's
    RLS scope.
    """

    def catalog(self, context: TenantContext) -> list[MarketplacePackage]: ...
    def upsert_package(self, context: TenantContext, package: MarketplacePackage) -> MarketplacePackage: ...
    def delete_package(self, context: TenantContext, package_id: str) -> bool: ...
    def installs(self, context: TenantContext) -> list[PackageInstall]: ...
    def install(self, package: MarketplacePackage, context: TenantContext, version: str | None) -> PackageInstall: ...
    def set_install(self, context: TenantContext, install: PackageInstall) -> PackageInstall: ...
    def uninstall(self, package_id: str, context: TenantContext) -> bool: ...
    def targets(self, context: TenantContext) -> list[RoutingTarget]: ...
    def upsert_target(self, target: RoutingTarget, context: TenantContext) -> RoutingTarget: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


def _audit(
    store: ScaleStore, context: TenantContext, action: str, package_id: str, metadata: dict[str, object],
    outcome: str = "success",
) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=context.user_id, action=action, target=f"marketplace_package:{package_id}",
            outcome=outcome, correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata=metadata,
        )
    )


def _signing_metadata(check: SkillVerification | None) -> dict[str, object]:
    if check is None:
        return {}
    return {"digest": check.digest, "signature_status": check.status.value, "key_id": check.key_id, "signer": check.signer}


def new_package_install(package: MarketplacePackage, context: TenantContext, version: str | None) -> PackageInstall:
    if version is not None and version != package.version:
        raise ValueError("Requested package version is unavailable")
    return PackageInstall(
        package_id=package.id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        version=package.version,
        installed_by=context.user_id,
        installed_at=utc_now(),
    )


class Phase5Store:
    """In-memory store for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self._installs: dict[tuple[str, str, str], PackageInstall] = {}
        self._targets: dict[tuple[str, str], RoutingTarget] = {}
        self._catalog: dict[tuple[str, str], MarketplacePackage] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._installs.clear()
            self._targets.clear()
            self._catalog.clear()

    def catalog(self, context: TenantContext) -> list[MarketplacePackage]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for (tenant_id, _), item in self._catalog.items()
                if tenant_id == context.tenant_id
            ]

    def upsert_package(self, context: TenantContext, package: MarketplacePackage) -> MarketplacePackage:
        with self._lock:
            self._catalog[(context.tenant_id, package.id)] = package.model_copy(deep=True)
        return package

    def delete_package(self, context: TenantContext, package_id: str) -> bool:
        with self._lock:
            if any(key[0] == context.tenant_id and key[2] == package_id for key in self._installs):
                raise PackageInstalledError("Installed marketplace package cannot be deleted")
            return self._catalog.pop((context.tenant_id, package_id), None) is not None

    def installs(self, context: TenantContext) -> list[PackageInstall]:
        with self._lock:
            return [
                item
                for (tenant, workspace, _), item in self._installs.items()
                if tenant == context.tenant_id and workspace == context.workspace_id
            ]

    def install(
        self, package: MarketplacePackage, context: TenantContext, version: str | None,
    ) -> PackageInstall:
        item = new_package_install(package, context, version)
        with self._lock:
            self._installs[(context.tenant_id, context.workspace_id, package.id)] = item
        return item

    def set_install(self, context: TenantContext, install: PackageInstall) -> PackageInstall:
        stored = install.model_copy(update={"verification": None})
        with self._lock:
            self._installs[(context.tenant_id, context.workspace_id, install.package_id)] = stored
        return install

    def uninstall(self, package_id: str, context: TenantContext) -> bool:
        with self._lock:
            key = (context.tenant_id, context.workspace_id, package_id)
            return self._installs.pop(key, None) is not None

    def targets(self, context: TenantContext) -> list[RoutingTarget]:
        with self._lock:
            overrides = [
                target
                for (tenant_id, _), target in self._targets.items()
                if tenant_id == context.tenant_id
            ]
        return merge_targets(overrides)

    def upsert_target(self, target: RoutingTarget, context: TenantContext) -> RoutingTarget:
        with self._lock:
            self._targets[(context.tenant_id, target.id)] = target
        return target

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        # The same in-memory recorder the audit export reads.
        from .governance import governance_store

        return governance_store.record_audit(record)


store = Phase5Store()


@contextmanager
def open_scale_store(context: TenantContext) -> Iterator[ScaleStore]:
    """The marketplace and routing store for one unit of work, by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.marketplace_repository import SqlAlchemyScaleStore

        return SqlAlchemyScaleStore(session)

    with open_scoped_store(context, store, sql_store) as scoped:
        yield scoped


router = APIRouter(prefix="/api/v1", tags=["scale-and-ecosystem"])


@router.get("/marketplace/packages", response_model=list[MarketplacePackage])
async def list_packages(
    kind: MarketplaceKind | None = None,
    q: str | None = Query(default=None, max_length=200),
    verified: bool | None = None,
    context: TenantContext = Depends(tenant_context),
) -> list[MarketplacePackage]:
    """The tenant's catalog; ``q`` matches id, name or publisher, ``verified`` filters."""
    require_permission(context, Permission.MARKETPLACE_READ)
    keys = tenant_trusted_keys(context)
    with open_scale_store(context) as scoped:
        catalog = [with_package_verification(item, keys) for item in scoped.catalog(context)]
    needle = (q or "").strip().casefold()
    return [
        item for item in catalog
        if (kind is None or item.kind == kind)
        and (verified is None or item.verified == verified)
        and (not needle or needle in item.id.casefold() or needle in item.name.casefold() or needle in item.publisher.casefold())
    ]


@router.put("/marketplace/packages/{package_id}", response_model=MarketplacePackage)
async def upsert_package(package_id: str, payload: MarketplacePackage, context: TenantContext = Depends(tenant_context)) -> MarketplacePackage:
    # The catalog is the tenant's: every workspace sees it, so publishing needs the
    # organization administrator role (docs/governance-and-scale.md#who-may-change-what).
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    if payload.id != package_id:
        raise HTTPException(status_code=422, detail="Marketplace package id does not match path")
    keys = tenant_trusted_keys(context)
    refused: SkillVerification | None = None
    with open_scale_store(context) as scoped:
        existing = next((item for item in scoped.catalog(context) if item.id == package_id), None)
        now = utc_now()
        if payload.signature is None:
            signed_at, signed_by = None, None
        elif existing is not None and existing.signature == payload.signature:
            signed_at, signed_by = existing.signed_at, existing.signed_by
        else:
            signed_at, signed_by = now, context.user_id
        package = payload.model_copy(update={"signed_at": signed_at, "signed_by": signed_by, "verification": None})
        check = package_verification(package, keys) if requires_signature(package) else None
        new_signature = payload.signature is not None and signed_at == now
        if check is not None and new_signature and not check.installable:
            refused = check
            _audit(scoped, context, "marketplace.signature_refused", package_id, {
                "version": payload.version, **_signing_metadata(check)}, outcome="denied")
        else:
            result = scoped.upsert_package(context, package)
            _audit(scoped, context, "marketplace.package_updated" if existing else "marketplace.package_published", package_id, {
                "version": payload.version, "kind": payload.kind.value, "verified": payload.verified,
                "permissions": list(payload.permissions), **_signing_metadata(check),
            })
            return result.model_copy(update={"verification": check})
    raise HTTPException(status_code=422, detail=f"Signature refused: {refused.detail if refused else 'it does not verify'}")


@router.put("/marketplace/packages/{package_id}/signature", response_model=MarketplacePackage)
async def sign_package(package_id: str, payload: SkillSignature, context: TenantContext = Depends(tenant_context)) -> MarketplacePackage:
    """Attach a signature to an integration package that is unsigned or no longer verifies."""
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    keys = tenant_trusted_keys(context)
    refused: SkillVerification | None = None
    with open_scale_store(context) as scoped:
        package = next((item for item in scoped.catalog(context) if item.id == package_id), None)
        if package is None:
            raise HTTPException(status_code=404, detail="Marketplace package not found")
        if not requires_signature(package):
            raise HTTPException(status_code=422, detail="Only integration packages are signed here; a skill is signed in the skill registry")
        before = package_verification(package, keys)
        if before.installable:
            raise HTTPException(status_code=409, detail="This package is already signed and verified")
        now = utc_now()
        check = package_verification(package, keys, signature=payload, signed_at=now)
        if not check.installable:
            refused = check
            _audit(scoped, context, "marketplace.signature_refused", package_id, {
                "version": package.version, **_signing_metadata(check)}, outcome="denied")
        else:
            signed = package.model_copy(update={"signature": payload, "signed_at": now, "signed_by": context.user_id})
            scoped.upsert_package(context, signed)
            _audit(scoped, context, "marketplace.package_signed", package_id, {
                "version": package.version, "previous_status": before.status.value, **_signing_metadata(check)})
            return signed.model_copy(update={"verification": check})
    raise HTTPException(status_code=422, detail=f"Signature refused: {refused.detail if refused else 'it does not verify'}")


@router.delete("/marketplace/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(package_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    try:
        with open_scale_store(context) as scoped:
            deleted = scoped.delete_package(context, package_id)
            if deleted:
                _audit(scoped, context, "marketplace.package_deleted", package_id, {})
    except PackageInstalledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Marketplace package not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/marketplace/installs", response_model=list[PackageInstall])
async def list_installs(context: TenantContext = Depends(tenant_context)) -> list[PackageInstall]:
    require_permission(context, Permission.MARKETPLACE_READ)
    keys = tenant_trusted_keys(context)
    with open_scale_store(context) as scoped:
        packages = {item.id: item for item in scoped.catalog(context)}
        installs = scoped.installs(context)
    return [_install_view(install, packages.get(install.package_id), keys) for install in installs]


def _install_view(install: PackageInstall, package: MarketplacePackage | None, keys: list[PublisherKey]) -> PackageInstall:
    if package is None or not requires_signature(package):
        return install
    return install.model_copy(update={"verification": package_verification(package, keys)})


@router.post(
    "/marketplace/packages/{package_id}/install",
    response_model=PackageInstall,
    status_code=status.HTTP_201_CREATED,
)
async def install_package(
    package_id: str,
    payload: InstallRequest,
    context: TenantContext = Depends(tenant_context),
) -> PackageInstall:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    keys = tenant_trusted_keys(context)
    refused: tuple[MarketplacePackage, SkillVerification] | None = None
    try:
        with open_scale_store(context) as scoped:
            package = next((item for item in scoped.catalog(context) if item.id == package_id), None)
            if package is None:
                raise HTTPException(status_code=404, detail="Marketplace package not found")
            check = package_verification(package, keys) if requires_signature(package) else None
            if check is not None and not check.installable:
                # The refusal is audited and committed, then raised.
                refused = (package, check)
                _audit(scoped, context, "marketplace.install_refused", package_id, {
                    "version": package.version, **_signing_metadata(check)}, outcome="denied")
            else:
                result = scoped.install(package, context, payload.version)
                _audit(scoped, context, "marketplace.package_installed", package_id, {
                    "version": result.version, "permissions": list(package.permissions), **_signing_metadata(check),
                })
                return result.model_copy(update={"verification": check})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if refused is None:  # pragma: no cover - the block above always returns or refuses
        raise HTTPException(status_code=409, detail="Package cannot be installed")
    package, check = refused
    raise HTTPException(status_code=409, detail=f"Package {package.id} {package.version} cannot be installed: {check.detail}")


@router.patch("/marketplace/packages/{package_id}/install", response_model=PackageInstall)
async def update_package_install(
    package_id: str, payload: PackageInstallUpdate, context: TenantContext = Depends(tenant_context),
) -> PackageInstall:
    """Turn this workspace's install off or on; on again for a flagged package needs ``acknowledge_flagged``."""
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    keys = tenant_trusted_keys(context)
    with open_scale_store(context) as scoped:
        install = next((item for item in scoped.installs(context) if item.package_id == package_id), None)
        if install is None:
            raise HTTPException(status_code=404, detail="Package installation not found")
        package = next((item for item in scoped.catalog(context) if item.id == package_id), None)
        check = package_verification(package, keys) if package is not None and requires_signature(package) else None
        flagged = check is not None and not check.installable
        if payload.enabled and flagged and not payload.acknowledge_flagged:
            raise HTTPException(status_code=409, detail=(
                f"{package_id} no longer verifies: {check.detail if check else ''} "
                "Confirm that you want to turn it on anyway."))
        result = scoped.set_install(context, install.model_copy(update={
            "enabled": payload.enabled, "disabled_reason": None if payload.enabled else "Turned off by a workspace owner.",
        }))
        action = ("marketplace.flagged_reenabled" if flagged else "marketplace.package_enabled") if payload.enabled else "marketplace.package_disabled"
        _audit(scoped, context, action, package_id, {"version": install.version, **_signing_metadata(check)})
        return result.model_copy(update={"verification": check})


@router.delete("/marketplace/packages/{package_id}/install", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_package(
    package_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Response:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    with open_scale_store(context) as scoped:
        removed = scoped.uninstall(package_id, context)
        if removed:
            _audit(scoped, context, "marketplace.package_uninstalled", package_id, {})
    if not removed:
        raise HTTPException(status_code=404, detail="Package installation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/routing/targets", response_model=list[RoutingTarget])
async def list_targets(context: TenantContext = Depends(tenant_context)) -> list[RoutingTarget]:
    require_permission(context, Permission.ROUTING_READ)
    with open_scale_store(context) as scoped:
        return scoped.targets(context)


@router.put("/routing/targets/{target_id}", response_model=RoutingTarget)
async def configure_target(
    target_id: str,
    target: RoutingTarget,
    context: TenantContext = Depends(tenant_context),
) -> RoutingTarget:
    # Routing targets are tenant-wide: every workspace's model calls route through them.
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    if target.id != target_id:
        raise HTTPException(status_code=422, detail="Routing target id does not match path")
    with open_scale_store(context) as scoped:
        return scoped.upsert_target(target, context)


@router.post("/routing/decisions", response_model=RoutingDecision)
async def decide_route(
    payload: RoutingRequest,
    context: TenantContext = Depends(tenant_context),
) -> RoutingDecision:
    require_permission(context, Permission.ROUTING_READ)
    with open_scale_store(context) as scoped:
        targets = scoped.targets(context)
    eligible = [
        target for target in targets
        if target.status != RegionStatus.OFFLINE
        and payload.modality in target.modalities
        and payload.sensitivity in target.sensitivity
    ]
    if payload.max_cost_per_1k_tokens is not None:
        eligible = [
            target for target in eligible
            if target.cost_per_1k_tokens <= payload.max_cost_per_1k_tokens
        ]
    if payload.max_latency_ms is not None:
        eligible = [target for target in eligible if target.latency_ms <= payload.max_latency_ms]
    if not eligible:
        raise HTTPException(status_code=503, detail="No routing target satisfies policy constraints")
    eligible.sort(key=lambda target: (
        target.status != RegionStatus.HEALTHY,
        target.region != payload.preferred_region if payload.preferred_region else False,
        target.cost_per_1k_tokens,
        target.latency_ms,
        target.id,
    ))
    selected = eligible[0]
    return RoutingDecision(
        target=selected,
        reason="Selected by health, residency preference, cost, and latency policy",
        failover_target_ids=[target.id for target in eligible[1:]],
    )


@router.get("/enterprise/operations", response_model=EnterpriseOperations)
async def enterprise_operations(context: TenantContext = Depends(tenant_context)) -> EnterpriseOperations:
    require_permission(context, Permission.OPERATIONS_READ)
    with open_scale_store(context) as scoped:
        targets = scoped.targets(context)
        installed = len(scoped.installs(context))
    healthy = [target for target in targets if target.status == RegionStatus.HEALTHY]
    return EnterpriseOperations(
        tenant_id=context.tenant_id,
        active_regions=len({
            target.region for target in targets if target.status != RegionStatus.OFFLINE
        }),
        healthy_targets=len(healthy),
        degraded_targets=sum(target.status == RegionStatus.DEGRADED for target in targets),
        installed_packages=installed,
        failover_ready=len({target.region for target in healthy}) > 1,
        generated_at=utc_now(),
    )
