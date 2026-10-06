from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, utc_now
from .scoped_store import open_scoped_store
from .settings import settings


class MarketplaceKind(StrEnum):
    SKILL = "skill"
    INTEGRATION = "integration"


class MarketplacePackage(BaseModel):
    id: str
    name: str
    kind: MarketplaceKind
    version: str
    publisher: str
    verified: bool
    permissions: list[str]
    regions: list[str]


class PackageInstall(BaseModel):
    package_id: str
    tenant_id: str
    workspace_id: str
    version: str
    enabled: bool = True
    installed_by: str
    installed_at: datetime


class InstallRequest(BaseModel):
    version: str | None = None


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


# The marketplace starts empty. Packages appear only when an owner publishes one to the
# tenant's catalog (PUT /marketplace/packages/{id}); ANUM never shows invented listings,
# and one tenant's packages are never visible to another.


def configured_targets() -> tuple[RoutingTarget, ...]:
    """The one model this deployment is actually configured to use.

    Cost and latency are not measured here, so they carry placeholder minimums
    (0 cost, 1 ms) and the target is reported as ``unverified`` (except the
    built-in mock, which always answers). Clients must show unverified targets
    as "not measured", never as real numbers. Owners add real regional targets
    with PUT /routing/targets/{id}.
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
    def uninstall(self, package_id: str, context: TenantContext) -> bool: ...
    def targets(self, context: TenantContext) -> list[RoutingTarget]: ...
    def upsert_target(self, target: RoutingTarget, context: TenantContext) -> RoutingTarget: ...


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
    context: TenantContext = Depends(tenant_context),
) -> list[MarketplacePackage]:
    require_permission(context, Permission.MARKETPLACE_READ)
    with open_scale_store(context) as scoped:
        catalog = scoped.catalog(context)
    return [item for item in catalog if kind is None or item.kind == kind]


@router.put("/marketplace/packages/{package_id}", response_model=MarketplacePackage)
async def upsert_package(package_id: str, payload: MarketplacePackage, context: TenantContext = Depends(tenant_context)) -> MarketplacePackage:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    if payload.id != package_id:
        raise HTTPException(status_code=422, detail="Marketplace package id does not match path")
    with open_scale_store(context) as scoped:
        return scoped.upsert_package(context, payload)


@router.delete("/marketplace/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(package_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    try:
        with open_scale_store(context) as scoped:
            deleted = scoped.delete_package(context, package_id)
    except PackageInstalledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Marketplace package not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/marketplace/installs", response_model=list[PackageInstall])
async def list_installs(context: TenantContext = Depends(tenant_context)) -> list[PackageInstall]:
    require_permission(context, Permission.MARKETPLACE_READ)
    with open_scale_store(context) as scoped:
        return scoped.installs(context)


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
    try:
        with open_scale_store(context) as scoped:
            package = next((item for item in scoped.catalog(context) if item.id == package_id), None)
            if package is None:
                raise HTTPException(status_code=404, detail="Marketplace package not found")
            return scoped.install(package, context, payload.version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/marketplace/packages/{package_id}/install", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_package(
    package_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Response:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    with open_scale_store(context) as scoped:
        removed = scoped.uninstall(package_id, context)
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
    require_permission(context, Permission.ROUTING_MANAGE)
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
