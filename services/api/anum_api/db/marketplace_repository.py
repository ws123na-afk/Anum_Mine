from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.phase5 import (
    MarketplaceKind,
    MarketplacePackage,
    PackageInstall,
    PackageInstalledError,
    RegionStatus,
    RoutingTarget,
    new_package_install,
    merge_targets,
)
from anum_api.schemas import TenantContext, utc_now

from .models import MarketplaceInstallRecord, MarketplacePackageRecord, RoutingTargetRecord
from .scope import require_tenant, require_workspace


class SqlAlchemyScaleStore:
    """PostgreSQL marketplace catalog (per tenant), installs (per workspace) and routing targets.

    The session must already carry the tenant and workspace RLS context; queries also
    filter on the scope explicitly.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def catalog(self, context: TenantContext) -> list[MarketplacePackage]:
        records = self.session.scalars(
            select(MarketplacePackageRecord)
            .where(MarketplacePackageRecord.tenant_id == context.tenant_id)
            .order_by(MarketplacePackageRecord.id)
        ).all()
        return [_package(record) for record in records]

    def upsert_package(self, context: TenantContext, package: MarketplacePackage) -> MarketplacePackage:
        require_tenant(self.session, context.tenant_id)
        record = self.session.get(MarketplacePackageRecord, (context.tenant_id, package.id))
        if record is None:
            record = MarketplacePackageRecord(tenant_id=context.tenant_id, id=package.id)
            self.session.add(record)
        record.name = package.name
        record.kind = package.kind.value
        record.version = package.version
        record.publisher = package.publisher
        record.verified = package.verified
        record.permissions = list(package.permissions)
        record.regions = list(package.regions)
        record.updated_at = utc_now()
        self.session.flush()
        return package

    def delete_package(self, context: TenantContext, package_id: str) -> bool:
        try:
            # The RESTRICT foreign key from marketplace_installs is checked without RLS,
            # so an install in any of the tenant's workspaces blocks the delete.
            with self.session.begin_nested():
                result = self.session.execute(
                    delete(MarketplacePackageRecord).where(
                        MarketplacePackageRecord.tenant_id == context.tenant_id,
                        MarketplacePackageRecord.id == package_id,
                    )
                )
        except IntegrityError as exc:
            raise PackageInstalledError("Installed marketplace package cannot be deleted") from exc
        return bool(result.rowcount)

    def installs(self, context: TenantContext) -> list[PackageInstall]:
        records = self.session.scalars(
            select(MarketplaceInstallRecord)
            .where(
                MarketplaceInstallRecord.tenant_id == context.tenant_id,
                MarketplaceInstallRecord.workspace_id == context.workspace_id,
            )
            .order_by(MarketplaceInstallRecord.installed_at, MarketplaceInstallRecord.package_id)
        ).all()
        return [
            PackageInstall(
                package_id=record.package_id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                version=record.version,
                enabled=record.enabled,
                installed_by=record.installed_by,
                installed_at=record.installed_at,
            )
            for record in records
        ]

    def install(self, package: MarketplacePackage, context: TenantContext, version: str | None) -> PackageInstall:
        item = new_package_install(package, context, version)
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        record = self.session.get(
            MarketplaceInstallRecord, (context.tenant_id, context.workspace_id, package.id)
        )
        if record is None:
            record = MarketplaceInstallRecord(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id, package_id=package.id
            )
            self.session.add(record)
        record.version = item.version
        record.enabled = item.enabled
        record.installed_by = item.installed_by
        record.installed_at = item.installed_at
        self.session.flush()
        return item

    def uninstall(self, package_id: str, context: TenantContext) -> bool:
        result = self.session.execute(
            delete(MarketplaceInstallRecord).where(
                MarketplaceInstallRecord.tenant_id == context.tenant_id,
                MarketplaceInstallRecord.workspace_id == context.workspace_id,
                MarketplaceInstallRecord.package_id == package_id,
            )
        )
        return bool(result.rowcount)

    def targets(self, context: TenantContext) -> list[RoutingTarget]:
        records = self.session.scalars(
            select(RoutingTargetRecord)
            .where(RoutingTargetRecord.tenant_id == context.tenant_id)
            .order_by(RoutingTargetRecord.id)
        ).all()
        return merge_targets(
            [
                RoutingTarget(
                    id=record.id,
                    region=record.region,
                    provider=record.provider,
                    model=record.model,
                    status=RegionStatus(record.status),
                    modalities=list(record.modalities),
                    sensitivity=list(record.sensitivity),
                    cost_per_1k_tokens=record.cost_per_1k_tokens,
                    latency_ms=record.latency_ms,
                )
                for record in records
            ]
        )

    def upsert_target(self, target: RoutingTarget, context: TenantContext) -> RoutingTarget:
        require_tenant(self.session, context.tenant_id)
        record = self.session.get(RoutingTargetRecord, (context.tenant_id, target.id))
        if record is None:
            record = RoutingTargetRecord(tenant_id=context.tenant_id, id=target.id)
            self.session.add(record)
        record.updated_at = utc_now()
        record.region = target.region
        record.provider = target.provider
        record.model = target.model
        record.status = target.status.value
        record.modalities = list(target.modalities)
        record.sensitivity = list(target.sensitivity)
        record.cost_per_1k_tokens = target.cost_per_1k_tokens
        record.latency_ms = target.latency_ms
        self.session.flush()
        return target


def _package(record: MarketplacePackageRecord) -> MarketplacePackage:
    return MarketplacePackage(
        id=record.id,
        name=record.name,
        kind=MarketplaceKind(record.kind),
        version=record.version,
        publisher=record.publisher,
        verified=record.verified,
        permissions=list(record.permissions),
        regions=list(record.regions),
    )
