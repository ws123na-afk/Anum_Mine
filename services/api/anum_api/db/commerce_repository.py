"""PostgreSQL commerce store (migration ``0038_commerce``, docs/commerce.md).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every
query repeats the scope as well, so a row of another workspace is neither read nor
written even if a policy were missing. Connector credentials stay ciphertext here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Double, Integer, String, Text, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.commerce.models import (
    Alert,
    AlertStatus,
    CommerceSettings,
    Connector,
    Customer,
    FeeRecord,
    Listing,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    ReturnRecord,
)
from anum_api.commerce.tyre import TyreSpec, size_query
from anum_api.schemas import TenantContext, new_id

from .models import Base
from .scope import require_workspace

TS = DateTime(timezone=True)


class _Scoped:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)


class CommerceConnectorRecord(Base, _Scoped):
    __tablename__ = "commerce_connectors"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    marketplaces: Mapped[list[str]] = mapped_column(JSONB)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean)
    credentials_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    health: Mapped[str] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    sync_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    cursors: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class CommerceProductRecord(Base, _Scoped):
    __tablename__ = "commerce_products"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sku: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(500))
    title_ar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tyre: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tyre_size: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cost_price: Mapped[float | None] = mapped_column(Double, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class CommerceListingRecord(Base, _Scoped):
    __tablename__ = "commerce_listings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    marketplace_id: Mapped[str] = mapped_column(String(40))
    sku: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    product_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[float | None] = mapped_column(Double, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    fulfillment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    buy_box_price: Mapped[float | None] = mapped_column(Double, nullable=True)
    buy_box_winner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lowest_competitor_price: Mapped[float | None] = mapped_column(Double, nullable=True)
    competition_checked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS)


class CommerceOrderRecord(Base, _Scoped):
    __tablename__ = "commerce_orders"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    marketplace_id: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    purchased_at: Mapped[datetime] = mapped_column(TS)
    ship_by: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    total_amount: Mapped[float] = mapped_column(Double)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer)
    fulfillment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ship_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ship_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    remote_updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(TS)


class CommerceOrderLineRecord(Base, _Scoped):
    __tablename__ = "commerce_order_lines"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(120))
    external_line_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float | None] = mapped_column(Double, nullable=True)
    total: Mapped[float | None] = mapped_column(Double, nullable=True)


class CommerceCustomerRecord(Base, _Scoped):
    __tablename__ = "commerce_customers"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(80))
    ref_hash: Mapped[str] = mapped_column(String(64))
    name_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    orders_count: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(TS)
    last_seen_at: Mapped[datetime] = mapped_column(TS)


class CommerceReturnRecord(Base, _Scoped):
    __tablename__ = "commerce_returns"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    marketplace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_external_id: Mapped[str] = mapped_column(String(120))
    sku: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    amount: Mapped[float | None] = mapped_column(Double, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    returned_at: Mapped[datetime] = mapped_column(TS)


class CommerceFeeRecord(Base, _Scoped):
    __tablename__ = "commerce_fees"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(200))
    order_external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Double)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(TS)


class CommerceAlertRecord(Base, _Scoped):
    __tablename__ = "commerce_alerts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(120), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    connector_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    acknowledged_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class CommerceSettingsRecord(Base):
    __tablename__ = "commerce_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    low_stock_threshold: Mapped[int] = mapped_column(Integer)
    handling_hours: Mapped[int] = mapped_column(Integer)
    return_spike_count: Mapped[int] = mapped_column(Integer)
    return_spike_ratio: Mapped[float] = mapped_column(Double)
    price_alert_percent: Mapped[float] = mapped_column(Double)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


def _copy(record: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(record, key, value)


def _fields(model: Any, record: Any, names: Any) -> dict[str, Any]:
    return {name: getattr(record, name) for name in names}


class SqlAlchemyCommerceStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scoped(self, record_type: Any, context: TenantContext) -> Any:
        return (record_type.tenant_id == context.tenant_id) & (record_type.workspace_id == context.workspace_id)

    def _one(self, record_type: Any, context: TenantContext, *conditions: Any) -> Any:
        return self.session.scalar(select(record_type).where(self._scoped(record_type, context), *conditions).limit(1))

    # connectors -------------------------------------------------------------------------

    @staticmethod
    def _connector(record: CommerceConnectorRecord) -> Connector:
        return Connector(
            **_fields(Connector, record, (
                "id", "tenant_id", "workspace_id", "channel", "name", "marketplaces", "settings", "enabled", "credential_hint",
                "health", "last_error", "last_sync_at", "last_success_at", "next_sync_at", "sync_counts", "cursors",
                "created_by", "created_at", "updated_at",
            )),
            credentials_configured=record.credentials_ciphertext is not None,
        )

    def list_connectors(self, context: TenantContext) -> list[Connector]:
        records = self.session.scalars(
            select(CommerceConnectorRecord).where(self._scoped(CommerceConnectorRecord, context)).order_by(CommerceConnectorRecord.created_at)
        )
        return [self._connector(r) for r in records]

    def get_connector(self, context: TenantContext, connector_id: str) -> Connector | None:
        record = self._one(CommerceConnectorRecord, context, CommerceConnectorRecord.id == connector_id)
        return None if record is None else self._connector(record)

    def save_connector(self, connector: Connector, *, credentials_ciphertext: str | None = None, replace_credentials: bool = False) -> Connector:
        context = TenantContext(tenant_id=connector.tenant_id, workspace_id=connector.workspace_id, user_id="system")
        record = self._one(CommerceConnectorRecord, context, CommerceConnectorRecord.id == connector.id)
        if record is None:
            require_workspace(self.session, connector.tenant_id, connector.workspace_id)
            record = CommerceConnectorRecord(id=connector.id, tenant_id=connector.tenant_id, workspace_id=connector.workspace_id)
            self.session.add(record)
        _copy(record, connector.model_dump(include={
            "channel", "name", "marketplaces", "settings", "enabled", "credential_hint", "health", "last_error",
            "last_sync_at", "last_success_at", "next_sync_at", "sync_counts", "cursors", "created_by", "created_at", "updated_at",
        }, mode="python"))
        record.channel, record.health = connector.channel.value, connector.health.value
        if replace_credentials:
            record.credentials_ciphertext = credentials_ciphertext
        self.session.flush()
        return connector

    def credentials_ciphertext(self, context: TenantContext, connector_id: str) -> str | None:
        return self.session.scalar(
            select(CommerceConnectorRecord.credentials_ciphertext).where(
                self._scoped(CommerceConnectorRecord, context), CommerceConnectorRecord.id == connector_id
            )
        )

    def delete_connector(self, context: TenantContext, connector_id: str) -> bool:
        # Listings, orders (and lines), customers, returns, fees and alerts cascade.
        result = self.session.execute(
            delete(CommerceConnectorRecord).where(self._scoped(CommerceConnectorRecord, context), CommerceConnectorRecord.id == connector_id)
        )
        return bool(result.rowcount)

    def claim_due(self, context: TenantContext, connector_id: str, now: datetime, until: datetime) -> bool:
        result = self.session.execute(
            update(CommerceConnectorRecord)
            .where(
                self._scoped(CommerceConnectorRecord, context),
                CommerceConnectorRecord.id == connector_id,
                CommerceConnectorRecord.enabled.is_(True),
                or_(CommerceConnectorRecord.next_sync_at.is_(None), CommerceConnectorRecord.next_sync_at <= now),
            )
            .values(next_sync_at=until)
        )
        return bool(result.rowcount)

    # products ----------------------------------------------------------------------------

    @staticmethod
    def _product(record: CommerceProductRecord) -> Product:
        return Product(
            **_fields(Product, record, (
                "id", "tenant_id", "workspace_id", "sku", "title", "title_ar", "brand", "cost_price", "currency",
                "low_stock_threshold", "created_at", "updated_at",
            )),
            tyre=TyreSpec(**(record.tyre or {})),
        )

    def upsert_product(self, product: Product) -> Product:
        context = TenantContext(tenant_id=product.tenant_id, workspace_id=product.workspace_id, user_id="system")
        record = self._one(CommerceProductRecord, context, CommerceProductRecord.sku == product.sku)
        if record is None:
            require_workspace(self.session, product.tenant_id, product.workspace_id)
            record = CommerceProductRecord(id=product.id, tenant_id=product.tenant_id, workspace_id=product.workspace_id, created_at=product.created_at)
            self.session.add(record)
        else:
            product = product.model_copy(update={"id": record.id, "created_at": record.created_at})
        _copy(record, product.model_dump(include={"sku", "title", "title_ar", "brand", "cost_price", "currency", "low_stock_threshold", "updated_at"}))
        record.tyre = product.tyre.model_dump(exclude_none=True)
        record.tyre_size = (product.tyre.size or "").upper().replace("ZR", "R") or None
        self.session.flush()
        return product

    def get_product(self, context: TenantContext, product_id: str) -> Product | None:
        record = self._one(CommerceProductRecord, context, CommerceProductRecord.id == product_id)
        return None if record is None else self._product(record)

    def get_product_by_sku(self, context: TenantContext, sku: str) -> Product | None:
        record = self._one(CommerceProductRecord, context, CommerceProductRecord.sku == sku)
        return None if record is None else self._product(record)

    def list_products(self, context: TenantContext, *, query: str | None, limit: int, offset: int) -> tuple[list[Product], int]:
        conditions = [self._scoped(CommerceProductRecord, context)]
        if query:
            like = f"%{_escape(query.strip())}%"
            options = [
                CommerceProductRecord.sku.ilike(like, escape="\\"),
                CommerceProductRecord.title.ilike(like, escape="\\"),
                CommerceProductRecord.title_ar.ilike(like, escape="\\"),
                CommerceProductRecord.brand.ilike(like, escape="\\"),
            ]
            size = size_query(query)
            if size:
                options.append(CommerceProductRecord.tyre_size == size.upper().replace("ZR", "R"))
            conditions.append(or_(*options))
        total = self.session.scalar(select(func.count()).select_from(CommerceProductRecord).where(*conditions)) or 0
        records = self.session.scalars(
            select(CommerceProductRecord).where(*conditions).order_by(func.lower(CommerceProductRecord.title), CommerceProductRecord.sku).limit(limit).offset(offset)
        )
        return [self._product(r) for r in records], int(total)

    def delete_product(self, context: TenantContext, product_id: str) -> bool:
        result = self.session.execute(delete(CommerceProductRecord).where(self._scoped(CommerceProductRecord, context), CommerceProductRecord.id == product_id))
        return bool(result.rowcount)

    # listings ----------------------------------------------------------------------------

    _LISTING_FIELDS = (
        "id", "tenant_id", "workspace_id", "connector_id", "channel", "marketplace_id", "sku", "external_id", "title", "product_type",
        "price", "currency", "quantity", "status", "fulfillment", "buy_box_price", "buy_box_winner", "lowest_competitor_price",
        "competition_checked_at", "updated_at",
    )

    def _listing(self, record: CommerceListingRecord) -> Listing:
        return Listing(**_fields(Listing, record, self._LISTING_FIELDS))

    def upsert_listing(self, listing: Listing) -> Listing:
        context = TenantContext(tenant_id=listing.tenant_id, workspace_id=listing.workspace_id, user_id="system")
        record = self._one(
            CommerceListingRecord,
            context,
            CommerceListingRecord.connector_id == listing.connector_id,
            CommerceListingRecord.marketplace_id == listing.marketplace_id,
            CommerceListingRecord.sku == listing.sku,
        )
        if record is None:
            record = CommerceListingRecord(id=listing.id, tenant_id=listing.tenant_id, workspace_id=listing.workspace_id)
            self.session.add(record)
        else:
            listing = listing.model_copy(update={"id": record.id})
        values = listing.model_dump(include=set(self._LISTING_FIELDS) - {"id", "tenant_id", "workspace_id"})
        values["channel"] = listing.channel.value
        _copy(record, values)
        self.session.flush()
        return listing

    def get_listing(self, context: TenantContext, listing_id: str) -> Listing | None:
        record = self._one(CommerceListingRecord, context, CommerceListingRecord.id == listing_id)
        return None if record is None else self._listing(record)

    def list_listings(self, context, *, sku=None, connector_id=None, query=None, limit=50, offset=0):  # type: ignore[no-untyped-def]
        conditions = [self._scoped(CommerceListingRecord, context)]
        if sku is not None:
            conditions.append(CommerceListingRecord.sku == sku)
        if connector_id is not None:
            conditions.append(CommerceListingRecord.connector_id == connector_id)
        if query:
            like = f"%{_escape(query.strip())}%"
            options = [
                CommerceListingRecord.sku.ilike(like, escape="\\"),
                CommerceListingRecord.title.ilike(like, escape="\\"),
                CommerceListingRecord.external_id.ilike(like, escape="\\"),
            ]
            size = size_query(query)
            if size:
                options.append(func.replace(func.upper(CommerceListingRecord.title), " ", "").like(f"%{size.upper().replace('ZR', 'R')}%"))
            conditions.append(or_(*options))
        total = self.session.scalar(select(func.count()).select_from(CommerceListingRecord).where(*conditions)) or 0
        records = self.session.scalars(
            select(CommerceListingRecord)
            .where(*conditions)
            .order_by(CommerceListingRecord.sku, CommerceListingRecord.channel, CommerceListingRecord.marketplace_id)
            .limit(limit)
            .offset(offset)
        )
        return [self._listing(r) for r in records], int(total)

    def all_listings(self, context: TenantContext) -> list[Listing]:
        records = self.session.scalars(select(CommerceListingRecord).where(self._scoped(CommerceListingRecord, context)).limit(20_000))
        return [self._listing(r) for r in records]

    # orders ------------------------------------------------------------------------------

    _ORDER_FIELDS = (
        "id", "tenant_id", "workspace_id", "connector_id", "channel", "marketplace_id", "external_id", "status", "purchased_at",
        "ship_by", "total_amount", "currency", "item_count", "fulfillment", "customer_id", "ship_city", "ship_country",
        "remote_updated_at", "synced_at",
    )

    def _orders(self, records: list[CommerceOrderRecord]) -> list[Order]:
        if not records:
            return []
        lines: dict[str, list[OrderLine]] = {}
        for line in self.session.scalars(
            select(CommerceOrderLineRecord)
            .where(CommerceOrderLineRecord.order_id.in_([r.id for r in records]))
            .order_by(CommerceOrderLineRecord.order_id, CommerceOrderLineRecord.position)
        ):
            lines.setdefault(line.order_id, []).append(
                OrderLine(
                    sku=line.sku, external_line_id=line.external_line_id, title=line.title,
                    quantity=line.quantity, unit_price=line.unit_price, total=line.total,
                )
            )
        return [Order(**_fields(Order, r, self._ORDER_FIELDS), lines=lines.get(r.id, [])) for r in records]

    def upsert_order(self, order: Order) -> Order:
        context = TenantContext(tenant_id=order.tenant_id, workspace_id=order.workspace_id, user_id="system")
        record = self._one(
            CommerceOrderRecord, context, CommerceOrderRecord.connector_id == order.connector_id, CommerceOrderRecord.external_id == order.external_id
        )
        if record is None:
            record = CommerceOrderRecord(id=order.id, tenant_id=order.tenant_id, workspace_id=order.workspace_id)
            self.session.add(record)
        else:
            order = order.model_copy(update={"id": record.id})
        values = order.model_dump(include=set(self._ORDER_FIELDS) - {"id", "tenant_id", "workspace_id"})
        values["channel"], values["status"] = order.channel.value, order.status.value
        _copy(record, values)
        self.session.flush()
        self.session.execute(delete(CommerceOrderLineRecord).where(CommerceOrderLineRecord.order_id == record.id))
        for position, line in enumerate(order.lines):
            self.session.add(
                CommerceOrderLineRecord(
                    id=new_id("cline"),
                    tenant_id=order.tenant_id,
                    workspace_id=order.workspace_id,
                    order_id=record.id,
                    position=position,
                    sku=line.sku[:120],
                    external_line_id=line.external_line_id,
                    title=line.title[:500],
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    total=line.total,
                )
            )
        self.session.flush()
        return order

    def get_order(self, context: TenantContext, order_id: str) -> Order | None:
        record = self._one(CommerceOrderRecord, context, CommerceOrderRecord.id == order_id)
        return None if record is None else self._orders([record])[0]

    def find_order(self, context: TenantContext, external_id: str) -> Order | None:
        record = self._one(CommerceOrderRecord, context, CommerceOrderRecord.external_id == external_id)
        return None if record is None else self._orders([record])[0]

    def list_orders(self, context, *, channel=None, statuses=None, since=None, until=None, query=None, limit=50, offset=0):  # type: ignore[no-untyped-def]
        conditions = [self._scoped(CommerceOrderRecord, context)]
        if channel:
            conditions.append(CommerceOrderRecord.channel == str(channel))
        if statuses:
            conditions.append(CommerceOrderRecord.status.in_([OrderStatus(s).value for s in statuses]))
        if since is not None:
            conditions.append(CommerceOrderRecord.purchased_at >= since)
        if until is not None:
            conditions.append(CommerceOrderRecord.purchased_at < until)
        if query:
            like = f"%{_escape(query.strip())}%"
            lines = select(CommerceOrderLineRecord.order_id).where(
                self._scoped(CommerceOrderLineRecord, context),
                or_(CommerceOrderLineRecord.sku.ilike(like, escape="\\"), CommerceOrderLineRecord.title.ilike(like, escape="\\")),
            )
            conditions.append(or_(CommerceOrderRecord.external_id.ilike(like, escape="\\"), CommerceOrderRecord.id.in_(lines)))
        total = self.session.scalar(select(func.count()).select_from(CommerceOrderRecord).where(*conditions)) or 0
        records = list(
            self.session.scalars(
                select(CommerceOrderRecord)
                .where(*conditions)
                .order_by(CommerceOrderRecord.purchased_at.desc(), CommerceOrderRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return self._orders(records), int(total)

    def orders_between(self, context: TenantContext, start: datetime, end: datetime, *, limit: int) -> list[Order]:
        orders: list[Order] = []
        # Read in chunks so the line lookup stays a bounded IN list.
        offset = 0
        while len(orders) < limit:
            chunk, _ = self.list_orders(context, since=start, until=end, limit=min(1000, limit - len(orders)), offset=offset)
            orders += chunk
            offset += len(chunk)
            if len(chunk) < 1000:
                break
        return orders

    # customers ----------------------------------------------------------------------------

    _CUSTOMER_FIELDS = (
        "id", "tenant_id", "workspace_id", "connector_id", "ref_hash", "name_ciphertext", "email_ciphertext", "phone_ciphertext",
        "address_ciphertext", "city", "country", "orders_count", "first_seen_at", "last_seen_at",
    )

    def upsert_customer(self, customer: Customer) -> Customer:
        context = TenantContext(tenant_id=customer.tenant_id, workspace_id=customer.workspace_id, user_id="system")
        record = self._one(
            CommerceCustomerRecord, context, CommerceCustomerRecord.connector_id == customer.connector_id, CommerceCustomerRecord.ref_hash == customer.ref_hash
        )
        if record is None:
            record = CommerceCustomerRecord(id=customer.id, tenant_id=customer.tenant_id, workspace_id=customer.workspace_id)
            self.session.add(record)
            values = customer.model_dump(include=set(self._CUSTOMER_FIELDS) - {"id", "tenant_id", "workspace_id"})
        else:
            values = customer.model_dump(include=set(self._CUSTOMER_FIELDS) - {"id", "tenant_id", "workspace_id", "first_seen_at"})
            values["orders_count"] = record.orders_count
            customer = customer.model_copy(update={"id": record.id, "first_seen_at": record.first_seen_at, "orders_count": values["orders_count"]})
        _copy(record, values)
        self.session.flush()
        return customer

    def get_customer(self, context: TenantContext, customer_id: str) -> Customer | None:
        record = self._one(CommerceCustomerRecord, context, CommerceCustomerRecord.id == customer_id)
        return None if record is None else Customer(**_fields(Customer, record, self._CUSTOMER_FIELDS))

    # returns and money ------------------------------------------------------------------------

    _RETURN_FIELDS = (
        "id", "tenant_id", "workspace_id", "connector_id", "channel", "external_id", "marketplace_id", "order_external_id", "sku",
        "quantity", "reason", "status", "amount", "currency", "returned_at",
    )
    _FEE_FIELDS = (
        "id", "tenant_id", "workspace_id", "connector_id", "channel", "external_id", "order_external_id", "kind", "description",
        "amount", "currency", "posted_at",
    )

    def _upsert_by_external(self, record_type: Any, item: Any, fields: tuple[str, ...]) -> Any:
        context = TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id="system")
        record = self._one(record_type, context, record_type.connector_id == item.connector_id, record_type.external_id == item.external_id)
        if record is None:
            record = record_type(id=item.id, tenant_id=item.tenant_id, workspace_id=item.workspace_id)
            self.session.add(record)
        else:
            item = item.model_copy(update={"id": record.id})
        values = item.model_dump(include=set(fields) - {"id", "tenant_id", "workspace_id"}, mode="python")
        for key, value in list(values.items()):
            if hasattr(value, "value"):
                values[key] = value.value
        _copy(record, values)
        self.session.flush()
        return item

    def upsert_return(self, record: ReturnRecord) -> ReturnRecord:
        return self._upsert_by_external(CommerceReturnRecord, record, self._RETURN_FIELDS)

    def list_returns(self, context: TenantContext, *, since: datetime | None, limit: int, offset: int) -> tuple[list[ReturnRecord], int]:
        conditions = [self._scoped(CommerceReturnRecord, context)]
        if since is not None:
            conditions.append(CommerceReturnRecord.returned_at >= since)
        total = self.session.scalar(select(func.count()).select_from(CommerceReturnRecord).where(*conditions)) or 0
        records = self.session.scalars(
            select(CommerceReturnRecord).where(*conditions).order_by(CommerceReturnRecord.returned_at.desc(), CommerceReturnRecord.id.desc()).limit(limit).offset(offset)
        )
        return [ReturnRecord(**_fields(ReturnRecord, r, self._RETURN_FIELDS)) for r in records], int(total)

    def upsert_fee(self, record: FeeRecord) -> FeeRecord:
        return self._upsert_by_external(CommerceFeeRecord, record, self._FEE_FIELDS)

    def list_fees(self, context: TenantContext, *, since: datetime, until: datetime) -> list[FeeRecord]:
        records = self.session.scalars(
            select(CommerceFeeRecord)
            .where(self._scoped(CommerceFeeRecord, context), CommerceFeeRecord.posted_at >= since, CommerceFeeRecord.posted_at < until)
            .limit(100_000)
        )
        return [FeeRecord(**_fields(FeeRecord, r, self._FEE_FIELDS)) for r in records]

    # alerts --------------------------------------------------------------------------------

    _ALERT_FIELDS = (
        "id", "tenant_id", "workspace_id", "kind", "severity", "title", "detail", "subject", "channel", "connector_id",
        "dedupe_key", "status", "acknowledged_by", "created_at", "updated_at",
    )

    def _alert(self, record: CommerceAlertRecord) -> Alert:
        return Alert(**_fields(Alert, record, self._ALERT_FIELDS))

    def raise_alert(self, alert: Alert) -> tuple[Alert, bool]:
        context = TenantContext(tenant_id=alert.tenant_id, workspace_id=alert.workspace_id, user_id="system")
        record = self._one(
            CommerceAlertRecord, context, CommerceAlertRecord.dedupe_key == alert.dedupe_key, CommerceAlertRecord.status != AlertStatus.RESOLVED.value
        )
        if record is not None:
            record.title, record.detail, record.severity, record.updated_at = alert.title, alert.detail, alert.severity.value, alert.updated_at
            self.session.flush()
            return self._alert(record), False
        self.save_alert(alert)
        return alert, True

    def resolve_alerts(self, context: TenantContext, kind: str, keep_keys: set[str], *, connector_id: str | None = None) -> int:
        conditions = [
            self._scoped(CommerceAlertRecord, context),
            CommerceAlertRecord.kind == kind,
            CommerceAlertRecord.status != AlertStatus.RESOLVED.value,
        ]
        if keep_keys:
            conditions.append(CommerceAlertRecord.dedupe_key.not_in(sorted(keep_keys)))
        if connector_id is not None:
            conditions.append(CommerceAlertRecord.connector_id == connector_id)
        result = self.session.execute(update(CommerceAlertRecord).where(*conditions).values(status=AlertStatus.RESOLVED.value))
        return int(result.rowcount or 0)

    def list_alerts(self, context: TenantContext, *, status: AlertStatus | None, limit: int, offset: int) -> tuple[list[Alert], int]:
        conditions = [self._scoped(CommerceAlertRecord, context)]
        if status is not None:
            conditions.append(CommerceAlertRecord.status == status.value)
        total = self.session.scalar(select(func.count()).select_from(CommerceAlertRecord).where(*conditions)) or 0
        records = self.session.scalars(
            select(CommerceAlertRecord).where(*conditions).order_by(CommerceAlertRecord.updated_at.desc(), CommerceAlertRecord.id.desc()).limit(limit).offset(offset)
        )
        return [self._alert(r) for r in records], int(total)

    def get_alert(self, context: TenantContext, alert_id: str) -> Alert | None:
        record = self._one(CommerceAlertRecord, context, CommerceAlertRecord.id == alert_id)
        return None if record is None else self._alert(record)

    def save_alert(self, alert: Alert) -> Alert:
        context = TenantContext(tenant_id=alert.tenant_id, workspace_id=alert.workspace_id, user_id="system")
        record = self._one(CommerceAlertRecord, context, CommerceAlertRecord.id == alert.id)
        if record is None:
            record = CommerceAlertRecord(id=alert.id, tenant_id=alert.tenant_id, workspace_id=alert.workspace_id)
            self.session.add(record)
        values = alert.model_dump(include=set(self._ALERT_FIELDS) - {"id", "tenant_id", "workspace_id"})
        for key in ("kind", "severity", "status", "channel"):
            value = getattr(alert, key)
            values[key] = value.value if value is not None else None
        _copy(record, values)
        self.session.flush()
        return alert

    # settings ---------------------------------------------------------------------------------

    def get_settings(self, context: TenantContext) -> CommerceSettings | None:
        record = self.session.get(CommerceSettingsRecord, (context.tenant_id, context.workspace_id))
        if record is None:
            return None
        return CommerceSettings(**_fields(CommerceSettings, record, (
            "low_stock_threshold", "handling_hours", "return_spike_count", "return_spike_ratio", "price_alert_percent",
            "sync_interval_minutes", "updated_at",
        )))

    def save_settings(self, context: TenantContext, settings: CommerceSettings) -> CommerceSettings:
        record = self.session.get(CommerceSettingsRecord, (context.tenant_id, context.workspace_id))
        if record is None:
            require_workspace(self.session, context.tenant_id, context.workspace_id)
            record = CommerceSettingsRecord(tenant_id=context.tenant_id, workspace_id=context.workspace_id)
            self.session.add(record)
        _copy(record, settings.model_dump())
        self.session.flush()
        return settings


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
