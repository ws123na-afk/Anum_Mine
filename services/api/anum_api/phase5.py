from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, utc_now


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


CATALOG = (
    MarketplacePackage(
        id="skill.research-core", name="Research Core", kind=MarketplaceKind.SKILL,
        version="1.0.0", publisher="ANUM", verified=True,
        permissions=["memory:read", "network:read"], regions=["us-east", "eu-west"],
    ),
    MarketplacePackage(
        id="integration.crm-sync", name="CRM Sync", kind=MarketplaceKind.INTEGRATION,
        version="1.2.0", publisher="ANUM", verified=True,
        permissions=["contacts:read", "contacts:write"], regions=["us-east", "eu-west"],
    ),
)

DEFAULT_TARGETS = (
    RoutingTarget(
        id="us-primary", region="us-east", provider="openai", model="primary",
        cost_per_1k_tokens=0.01, latency_ms=180,
    ),
    RoutingTarget(
        id="eu-primary", region="eu-west", provider="openai", model="primary",
        cost_per_1k_tokens=0.012, latency_ms=210,
    ),
    RoutingTarget(
        id="us-economy", region="us-east", provider="openai", model="economy",
        cost_per_1k_tokens=0.003, latency_ms=280,
    ),
)


class Phase5Store:
    def __init__(self) -> None:
        self._installs: dict[tuple[str, str, str], PackageInstall] = {}
        self._targets: dict[tuple[str, str], RoutingTarget] = {}
        self._lock = RLock()

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
        if version is not None and version != package.version:
            raise ValueError("Requested package version is unavailable")
        item = PackageInstall(
            package_id=package.id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            version=package.version,
            installed_by=context.user_id,
            installed_at=utc_now(),
        )
        with self._lock:
            self._installs[(context.tenant_id, context.workspace_id, package.id)] = item
        return item

    def uninstall(self, package_id: str, context: TenantContext) -> bool:
        with self._lock:
            key = (context.tenant_id, context.workspace_id, package_id)
            return self._installs.pop(key, None) is not None

    def targets(self, context: TenantContext) -> list[RoutingTarget]:
        with self._lock:
            overrides = {
                target_id: target
                for (tenant_id, target_id), target in self._targets.items()
                if tenant_id == context.tenant_id
            }
            return [overrides.get(target.id, target) for target in DEFAULT_TARGETS] + [
                target
                for target_id, target in overrides.items()
                if target_id not in {default.id for default in DEFAULT_TARGETS}
            ]

    def upsert_target(self, target: RoutingTarget, context: TenantContext) -> RoutingTarget:
        with self._lock:
            self._targets[(context.tenant_id, target.id)] = target
        return target


store = Phase5Store()
router = APIRouter(prefix="/api/v1", tags=["scale-and-ecosystem"])


@router.get("/marketplace/packages", response_model=list[MarketplacePackage])
async def list_packages(
    kind: MarketplaceKind | None = None,
    context: TenantContext = Depends(tenant_context),
) -> list[MarketplacePackage]:
    require_permission(context, Permission.MARKETPLACE_READ)
    return [item for item in CATALOG if kind is None or item.kind == kind]


@router.get("/marketplace/installs", response_model=list[PackageInstall])
async def list_installs(context: TenantContext = Depends(tenant_context)) -> list[PackageInstall]:
    require_permission(context, Permission.MARKETPLACE_READ)
    return store.installs(context)


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
    package = next((item for item in CATALOG if item.id == package_id), None)
    if package is None:
        raise HTTPException(status_code=404, detail="Marketplace package not found")
    try:
        return store.install(package, context, payload.version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/marketplace/packages/{package_id}/install", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_package(
    package_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Response:
    require_permission(context, Permission.MARKETPLACE_MANAGE)
    if not store.uninstall(package_id, context):
        raise HTTPException(status_code=404, detail="Package installation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/routing/targets", response_model=list[RoutingTarget])
async def list_targets(context: TenantContext = Depends(tenant_context)) -> list[RoutingTarget]:
    require_permission(context, Permission.ROUTING_READ)
    return store.targets(context)


@router.put("/routing/targets/{target_id}", response_model=RoutingTarget)
async def configure_target(
    target_id: str,
    target: RoutingTarget,
    context: TenantContext = Depends(tenant_context),
) -> RoutingTarget:
    require_permission(context, Permission.ROUTING_MANAGE)
    if target.id != target_id:
        raise HTTPException(status_code=422, detail="Routing target id does not match path")
    return store.upsert_target(target, context)


@router.post("/routing/decisions", response_model=RoutingDecision)
async def decide_route(
    payload: RoutingRequest,
    context: TenantContext = Depends(tenant_context),
) -> RoutingDecision:
    require_permission(context, Permission.ROUTING_READ)
    eligible = [
        target for target in store.targets(context)
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
    targets = store.targets(context)
    healthy = [target for target in targets if target.status == RegionStatus.HEALTHY]
    return EnterpriseOperations(
        tenant_id=context.tenant_id,
        active_regions=len({
            target.region for target in targets if target.status != RegionStatus.OFFLINE
        }),
        healthy_targets=len(healthy),
        degraded_targets=sum(target.status == RegionStatus.DEGRADED for target in targets),
        installed_packages=len(store.installs(context)),
        failover_ready=len({target.region for target in healthy}) > 1,
        generated_at=utc_now(),
    )
