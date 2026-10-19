"""HTTP routes for Business, part 1: setup, catalog, accounts, contacts, leads, evidence,
research, drafts and CSV import (``/api/v1/business``, docs/business.md).

Permissions (``anum_api/authorization.py``): owners and members read and work records
(``business:read``, ``business:write``); owners manage the catalog, the research caps,
deletions and do-not-contact removals (``business:manage``). Viewers are refused: the
desk holds contacts' personal data. Sending is off: no route here sends anything.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query as Q, Response, status
from pydantic import BaseModel, Field, model_validator

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .. import suppression
from . import assist, research
from .common import Page, Reason, clean_list, get_or_404, manage, page_bounds, read, write
from .models import (
    CONTACTABLE_BASES,
    LEAD_TRANSITIONS,
    OPEN_LEAD_STATES,
    Account,
    Activity,
    BusinessSettings,
    Confidence,
    ConsentBasis,
    Contact,
    CostAssumptions,
    Criterion,
    CriterionScore,
    Currency,
    Draft,
    DraftChannel,
    DraftStatus,
    Evidence,
    Inference,
    InferenceStatus,
    Lead,
    LeadState,
    Maturity,
    Money,
    Offer,
    OfferStatus,
    PriceType,
    Product,
    ProductKind,
    RecordSource,
    ResearchRun,
    money,
)
from .service import (
    apply_score,
    audit,
    can_move,
    clean_url,
    consent_missing,
    contact_block,
    contact_suppressed,
    domain_of,
    draft_hash,
    http_error,
    name_key,
    normal_email,
    normal_phone,
    qualification_gaps,
    record_activity,
    stamp_state,
    url_key,
)
from .store import MAX_SCAN, BusinessStore, Query, open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])


# -- setup and settings ----------------------------------------------------------------------


class Permissions(BaseModel):
    read: bool
    write: bool
    manage: bool
    decide: bool


class Usage(BaseModel):
    runs_today: int
    searches_today: int


class BusinessSetup(BaseModel):
    permissions: Permissions
    sending_enabled: bool = False
    web_search: bool
    model_connected: bool
    settings: BusinessSettings
    usage: Usage
    criteria: list[Criterion]
    conversation_criteria: list[Criterion]
    transitions: dict[str, list[str]]
    contactable_bases: list[ConsentBasis]


@router.get("/setup", response_model=BusinessSetup)
def get_setup(context: TenantContext = Depends(tenant_context)) -> BusinessSetup:
    read(context)
    from .pipeline import load_settings, usage

    with open_business_store(context) as store:
        settings = store.get_settings()
        today = usage(store, utc_now())
        runs, searches = today["runs"], today["searches"]
        sending = load_settings(store, context).sending_enabled
    from .models import CONVERSATION_CRITERIA

    return BusinessSetup(
        sending_enabled=sending,
        permissions=Permissions(
            read=True,
            write=has_permission(context, Permission.BUSINESS_WRITE),
            manage=has_permission(context, Permission.BUSINESS_MANAGE),
            decide=has_permission(context, Permission.APPROVAL_DECIDE),
        ),
        web_search=research.web_search_available(),
        model_connected=assist.model_connected(context),
        settings=settings,
        usage=Usage(runs_today=runs, searches_today=searches),
        criteria=list(Criterion),
        conversation_criteria=sorted(CONVERSATION_CRITERIA),
        transitions={state.value: sorted(item.value for item in targets) for state, targets in LEAD_TRANSITIONS.items()},
        contactable_bases=sorted(CONTACTABLE_BASES),
    )


class SettingsUpdate(BaseModel):
    research_runs_per_day: int = Field(ge=0, le=100)
    research_searches_per_day: int = Field(ge=0, le=500)


@router.put("/settings", response_model=BusinessSettings)
def put_settings(payload: SettingsUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BusinessSettings:
    manage(context)
    with open_business_store(context) as store:
        # The support policy and the billing profile are saved on their own routes and kept here.
        current = store.get_settings()
        value = BusinessSettings(**payload.model_dump(), support_policy=current.support_policy, billing=current.billing, updated_by=context.user_id, updated_at=utc_now())
        store.save_settings(value)
    audit(repository, context, "business.settings.updated", context.workspace_id, metadata=payload.model_dump())
    return value


# -- catalog ---------------------------------------------------------------------------------


class ProductInput(BaseModel):
    kind: ProductKind
    name: str = Field(min_length=2, max_length=160)
    name_ar: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=2000)
    maturity: Maturity = Maturity.IDEA
    integrations: list[str] = Field(default_factory=list, max_length=30)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    name_ar: str | None = Field(default=None, max_length=160)
    summary: str | None = Field(default=None, max_length=2000)
    maturity: Maturity | None = None
    integrations: list[str] | None = Field(default=None, max_length=30)


class OfferView(Offer):
    estimated_cost: Money
    margin_at_price: Money | None = None
    margin_negative: bool = False


class ProductView(Product):
    active_offer: OfferView | None = None
    versions: int = 0


class ProductDetail(ProductView):
    offers: list[OfferView] = Field(default_factory=list)


class OfferInput(BaseModel):
    currency: Currency = Currency.SAR
    price_type: PriceType = PriceType.FIXED
    price_min: Money | None = None
    price_max: Money | None = None
    deliverables: list[str] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(default_factory=list, max_length=40)
    cost: CostAssumptions = Field(default_factory=CostAssumptions)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _prices(self) -> "OfferInput":
        if self.price_type == PriceType.QUOTE:
            self.price_min = self.price_max = None
        elif self.price_min is None:
            raise ValueError("Enter the price (or choose 'quote after scoping').")
        if self.price_type == PriceType.RANGE:
            if self.price_max is None or self.price_max < (self.price_min or Decimal(0)):
                raise ValueError("A range needs a highest price at or above the lowest.")
        elif self.price_type != PriceType.QUOTE:
            self.price_max = None
        return self


def offer_view(offer: Offer) -> OfferView:
    cost = offer.cost.total()
    margin = money(offer.price_min - cost) if offer.price_min is not None and offer.price_type != PriceType.HOURLY else None
    return OfferView(**offer.model_dump(), estimated_cost=cost, margin_at_price=margin if margin is None or margin >= 0 else None,
                     margin_negative=margin is not None and margin < 0)


def _offers(store: BusinessStore, product_id: str) -> list[Offer]:
    items, _ = store.find("offers", Query(where={"product_id": product_id}, order_by="version", descending=True, limit=100))
    return items


def product_view(store: BusinessStore, product: Product, *, detail: bool = False) -> ProductView | ProductDetail:
    offers = _offers(store, product.id)
    active = next((item for item in offers if item.status == OfferStatus.ACTIVE), None)
    base = dict(**product.model_dump(), active_offer=offer_view(active) if active else None, versions=len(offers))
    if detail:
        return ProductDetail(**base, offers=[offer_view(item) for item in offers])
    return ProductView(**base)


@router.get("/products", response_model=Page[ProductView])
def list_products(
    q: str | None = Q(default=None, max_length=100), kind: ProductKind | None = None, maturity: Maturity | None = None,
    limit: int = 50, offset: int = 0, context: TenantContext = Depends(tenant_context),
) -> Page[ProductView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if kind:
        where["kind"] = kind
    if maturity:
        where["maturity"] = maturity
    with open_business_store(context) as store:
        items, total = store.find("products", Query(where=where, text=q, text_fields=("name", "name_ar", "summary"), order_by="name", descending=False, limit=limit, offset=offset))
        views = [product_view(store, item) for item in items]
    return Page[ProductView](items=views, total=total, limit=limit, offset=offset)  # type: ignore[arg-type]


@router.post("/products", response_model=ProductDetail, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    now = utc_now()
    product = Product(id=new_id("bprod"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_by=context.user_id,
                      created_at=now, updated_at=now, **{**payload.model_dump(), "integrations": clean_list(payload.integrations, 80)})
    with open_business_store(context) as store:
        if store.first("products", name=product.name):
            raise http_error(409, f"A product called {product.name} already exists.")
        store.add("products", product)
        view = product_view(store, product, detail=True)
    audit(repository, context, "business.product.created", product.id, metadata={"kind": product.kind.value})
    return view


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: str, context: TenantContext = Depends(tenant_context)) -> Any:
    read(context)
    with open_business_store(context) as store:
        return product_view(store, get_or_404(store, "products", product_id, "Product"), detail=True)


@router.patch("/products/{product_id}", response_model=ProductDetail)
def update_product(product_id: str, payload: ProductUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    with open_business_store(context) as store:
        product = get_or_404(store, "products", product_id, "Product", for_update=True)
        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        if "integrations" in changes:
            changes["integrations"] = clean_list(changes["integrations"], 80)
        product = product.model_copy(update={**changes, "updated_at": utc_now()})
        Product.model_validate(product.model_dump())
        store.save("products", product)
        view = product_view(store, product, detail=True)
    audit(repository, context, "business.product.updated", product_id, metadata={"fields": sorted(changes)})
    return view


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    manage(context)
    with open_business_store(context) as store:
        get_or_404(store, "products", product_id, "Product", for_update=True)
        if store.count("leads", Query(where={"product_id": product_id})) or store.count("opportunities", Query(where={"product_id": product_id})):
            raise http_error(409, "Leads or opportunities use this product. Mark it retired instead.")
        offer_ids = {offer.id for offer in _offers(store, product_id)}
        if offer_ids and store.count("tickets", Query(where={"support_offer_id": offer_ids})):
            raise http_error(409, "Support tickets name this product as their support plan. Mark it retired instead.")
        store.delete("products", product_id)
    audit(repository, context, "business.product.deleted", product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/products/{product_id}/versions", response_model=ProductDetail, status_code=status.HTTP_201_CREATED)
def create_offer(product_id: str, payload: OfferInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        product = get_or_404(store, "products", product_id, "Product", for_update=True)
        offers = _offers(store, product_id)
        if any(item.status == OfferStatus.DRAFT for item in offers):
            raise http_error(409, "This product already has a draft version. Edit or activate it first.")
        offer = Offer(
            id=new_id("boffer"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, product_id=product_id,
            version=(offers[0].version + 1) if offers else 1, created_by=context.user_id, created_at=now, updated_at=now,
            **{**payload.model_dump(), "deliverables": clean_list(payload.deliverables), "limitations": clean_list(payload.limitations)},
        )
        store.add("offers", offer)
        view = product_view(store, product, detail=True)
    audit(repository, context, "business.offer.created", offer.id, metadata={"product_id": product_id, "version": offer.version})
    return view


@router.patch("/offers/{offer_id}", response_model=ProductDetail)
def update_offer(offer_id: str, payload: OfferInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    with open_business_store(context) as store:
        offer = get_or_404(store, "offers", offer_id, "Version", for_update=True)
        if offer.status != OfferStatus.DRAFT:
            raise http_error(409, f"Version {offer.version} is {offer.status.value}; only a draft version changes. Add a new version instead.")
        offer = Offer.model_validate({**offer.model_dump(), **payload.model_dump(), "deliverables": clean_list(payload.deliverables),
                                      "limitations": clean_list(payload.limitations), "updated_at": utc_now()})
        store.save("offers", offer)
        view = product_view(store, get_or_404(store, "products", offer.product_id, "Product"), detail=True)
    audit(repository, context, "business.offer.updated", offer_id)
    return view


@router.post("/offers/{offer_id}/activate", response_model=ProductDetail)
def activate_offer(offer_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        offer = get_or_404(store, "offers", offer_id, "Version", for_update=True)
        if offer.status != OfferStatus.DRAFT:
            raise http_error(409, f"Version {offer.version} is already {offer.status.value}.")
        for item in _offers(store, offer.product_id):
            if item.status == OfferStatus.ACTIVE:
                store.save("offers", item.model_copy(update={"status": OfferStatus.RETIRED, "updated_at": now}))
        store.save("offers", offer.model_copy(update={"status": OfferStatus.ACTIVE, "activated_at": now, "updated_at": now}))
        view = product_view(store, get_or_404(store, "products", offer.product_id, "Product"), detail=True)
    audit(repository, context, "business.offer.activated", offer_id, metadata={"version": offer.version})
    return view


@router.post("/offers/{offer_id}/retire", response_model=ProductDetail)
def retire_offer(offer_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Any:
    manage(context)
    with open_business_store(context) as store:
        offer = get_or_404(store, "offers", offer_id, "Version", for_update=True)
        if offer.status == OfferStatus.RETIRED:
            raise http_error(409, f"Version {offer.version} is already retired.")
        if offer.status == OfferStatus.DRAFT:
            store.delete("offers", offer_id)
        else:
            store.save("offers", offer.model_copy(update={"status": OfferStatus.RETIRED, "updated_at": utc_now()}))
        view = product_view(store, get_or_404(store, "products", offer.product_id, "Product"), detail=True)
    audit(repository, context, "business.offer.retired", offer_id, metadata={"version": offer.version})
    return view


# -- accounts and contacts ---------------------------------------------------------------------


class AccountInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    industry: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=4000)
    source: RecordSource = RecordSource.MANUAL


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)


class ContactInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    role_title: str = Field(default="", max_length=160)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    profile_url: str | None = Field(default=None, max_length=500)
    consent_basis: ConsentBasis = ConsentBasis.UNKNOWN
    consent_note: str = Field(default="", max_length=500)


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    role_title: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    profile_url: str | None = Field(default=None, max_length=500)
    consent_basis: ConsentBasis | None = None
    consent_note: str | None = Field(default=None, max_length=500)


class ContactView(Contact):
    opted_out: list[str] = Field(default_factory=list)
    block_reason: str | None = None
    contactable: bool = False


class AccountView(Account):
    contacts: int = 0
    open_leads: int = 0


class AccountDetail(Account):
    contacts: list[ContactView] = Field(default_factory=list)
    leads: list["LeadView"] = Field(default_factory=list)


def contact_view(context: TenantContext, contact: Contact, account: Account | None) -> ContactView:
    opted = contact_suppressed(context, contact)
    block = contact_block(context, contact, bool(account and account.do_not_contact))
    return ContactView(**contact.model_dump(), opted_out=opted, block_reason=block,
                       contactable=block is None and contact.consent_basis in CONTACTABLE_BASES)


def _account(context: TenantContext, payload: AccountInput, now: datetime) -> Account:
    website = clean_url(payload.website) if payload.website else None
    if payload.website and website is None:
        raise http_error(422, "Enter the website as an http(s) address, for example https://example.com.")
    return Account(
        id=new_id("bacct"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=" ".join(payload.name.split()),
        name_key=name_key(payload.name), website=website, domain=domain_of(website), industry=payload.industry.strip(),
        country=payload.country.strip(), city=payload.city.strip(), notes=payload.notes, source=payload.source,
        owner_id=context.user_id, created_by=context.user_id, created_at=now, updated_at=now,
    )


def _contact(context: TenantContext, account_id: str, payload: ContactInput, now: datetime, source: RecordSource = RecordSource.MANUAL) -> Contact:
    email = normal_email(payload.email) if payload.email else None
    if payload.email and email is None:
        raise http_error(422, "Enter a valid e-mail address, or leave it empty.")
    phone = normal_phone(payload.phone) if payload.phone else None
    if payload.phone and phone is None:
        raise http_error(422, "Enter the phone number with its country code, or leave it empty.")
    profile = clean_url(payload.profile_url) if payload.profile_url else None
    if payload.profile_url and profile is None:
        raise http_error(422, "Enter the profile as an http(s) address, or leave it empty.")
    return Contact(
        id=new_id("bcont"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account_id,
        name=" ".join(payload.name.split()), role_title=payload.role_title.strip(), email=email, phone=phone, profile_url=profile,
        consent_basis=payload.consent_basis, consent_note=payload.consent_note.strip(),
        consent_recorded_at=now if payload.consent_basis != ConsentBasis.UNKNOWN else None,
        source=source, created_by=context.user_id, created_at=now, updated_at=now,
    )


class Duplicate(BaseModel):
    kind: Literal["account", "contact", "lead"]
    id: str
    label: str
    reason: str


def find_duplicates(store: BusinessStore, *, name: str | None = None, website: str | None = None, email: str | None = None,
                    phone: str | None = None, product_id: str | None = None) -> list[Duplicate]:
    found: list[Duplicate] = []
    accounts: dict[str, Account] = {}
    domain = domain_of(website) if website else None
    if domain:
        item = store.first("accounts", domain=domain)
        if item is not None:
            accounts[item.id] = item
            found.append(Duplicate(kind="account", id=item.id, label=item.name, reason=f"Same website ({domain})."))
    if name and name_key(name):
        for item in store.find("accounts", Query(where={"name_key": name_key(name)}, limit=5))[0]:
            if item.id not in accounts:
                accounts[item.id] = item
                found.append(Duplicate(kind="account", id=item.id, label=item.name, reason="Same company name."))
    for value, field in ((normal_email(email), "email"), (normal_phone(phone), "phone")):
        if value:
            item = store.first("contacts", **{field: value})
            if item is not None:
                found.append(Duplicate(kind="contact", id=item.id, label=item.name, reason=f"Same {'e-mail' if field == 'email' else 'phone number'}."))
    for account in accounts.values():
        leads, _ = store.find("leads", Query(where={"account_id": account.id, "state": OPEN_LEAD_STATES}, limit=20))
        for lead in leads:
            if (lead.product_id or None) == (product_id or None):
                found.append(Duplicate(kind="lead", id=lead.id, label=lead.title, reason=f"{account.name} already has an open lead for this offer."))
    return found


@router.get("/duplicates", response_model=list[Duplicate])
def check_duplicates(
    name: str | None = Q(default=None, max_length=200), website: str | None = Q(default=None, max_length=500),
    email: str | None = Q(default=None, max_length=254), phone: str | None = Q(default=None, max_length=40),
    product_id: str | None = Q(default=None, max_length=80), context: TenantContext = Depends(tenant_context),
) -> list[Duplicate]:
    read(context)
    with open_business_store(context) as store:
        return find_duplicates(store, name=name, website=website, email=email, phone=phone, product_id=product_id)


@router.get("/accounts", response_model=Page[AccountView])
def list_accounts(q: str | None = Q(default=None, max_length=100), limit: int = 50, offset: int = 0, context: TenantContext = Depends(tenant_context)) -> Page[AccountView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    with open_business_store(context) as store:
        items, total = store.find("accounts", Query(text=q, text_fields=("name", "domain", "industry", "city", "country"), order_by="name", descending=False, limit=limit, offset=offset))
        views = [AccountView(**item.model_dump(), contacts=store.count("contacts", Query(where={"account_id": item.id})),
                             open_leads=store.count("leads", Query(where={"account_id": item.id, "state": OPEN_LEAD_STATES}))) for item in items]
    return Page[AccountView](items=views, total=total, limit=limit, offset=offset)


def account_detail(context: TenantContext, store: BusinessStore, account: Account) -> AccountDetail:
    contacts, _ = store.find("contacts", Query(where={"account_id": account.id}, order_by="name", descending=False, limit=100))
    leads, _ = store.find("leads", Query(where={"account_id": account.id}, limit=100))
    return AccountDetail(**account.model_dump(), contacts=[contact_view(context, item, account) for item in contacts],
                         leads=lead_views(context, store, leads))


@router.post("/accounts", response_model=AccountDetail, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccountDetail:
    write(context)
    account = _account(context, payload, utc_now())
    with open_business_store(context) as store:
        if account.domain and store.first("accounts", domain=account.domain):
            raise http_error(409, f"An account with the website {account.domain} already exists.")
        store.add("accounts", account)
        record_activity(store, context, "account_created", f"Account {account.name} added.", account_id=account.id)
        view = account_detail(context, store, account)
    audit(repository, context, "business.account.created", account.id, metadata={"source": account.source.value})
    return view


@router.get("/accounts/{account_id}", response_model=AccountDetail)
def get_account(account_id: str, context: TenantContext = Depends(tenant_context)) -> AccountDetail:
    read(context)
    with open_business_store(context) as store:
        return account_detail(context, store, get_or_404(store, "accounts", account_id, "Account"))


@router.patch("/accounts/{account_id}", response_model=AccountDetail)
def update_account(account_id: str, payload: AccountUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccountDetail:
    write(context)
    changes = payload.model_dump(exclude_unset=True)
    with open_business_store(context) as store:
        account = get_or_404(store, "accounts", account_id, "Account", for_update=True)
        update: dict[str, Any] = {key: (value.strip() if isinstance(value, str) else value) for key, value in changes.items() if value is not None or key == "website"}
        if "name" in update:
            update["name"] = " ".join(update["name"].split())
            update["name_key"] = name_key(update["name"])
        if "website" in update:
            website = clean_url(update["website"]) if update["website"] else None
            if update["website"] and website is None:
                raise http_error(422, "Enter the website as an http(s) address, for example https://example.com.")
            update["website"], update["domain"] = website, domain_of(website)
            other = store.first("accounts", domain=update["domain"]) if update["domain"] else None
            if other is not None and other.id != account_id:
                raise http_error(409, f"Another account ({other.name}) has the website {update['domain']}.")
        account = account.model_copy(update={**update, "updated_at": utc_now()})
        store.save("accounts", account)
        view = account_detail(context, store, account)
    audit(repository, context, "business.account.updated", account_id, metadata={"fields": sorted(changes)})
    return view


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    manage(context)
    with open_business_store(context) as store:
        get_or_404(store, "accounts", account_id, "Account", for_update=True)
        if store.count("leads", Query(where={"account_id": account_id})) or store.count("opportunities", Query(where={"account_id": account_id})):
            raise http_error(409, "This account has leads or opportunities. Delete those first.")
        if store.count("tickets", Query(where={"account_id": account_id})):
            raise http_error(409, "This account has support tickets, which are kept as records. Close them instead of deleting the account.")
        if store.count("invoices", Query(where={"account_id": account_id})):
            raise http_error(409, "This account has invoices, which are kept as records. Delete the drafts and void the rest; an account with invoices is not deleted.")
        store.delete("accounts", account_id)
    audit(repository, context, "business.account.deleted", account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DoNotContact(BaseModel):
    on: bool
    reason: str = Field(min_length=3, max_length=300)


@router.post("/accounts/{account_id}/do-not-contact", response_model=AccountDetail)
def account_do_not_contact(account_id: str, payload: DoNotContact, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccountDetail:
    write(context)
    if not payload.on:
        manage(context)
    with open_business_store(context) as store:
        account = get_or_404(store, "accounts", account_id, "Account", for_update=True)
        account = account.model_copy(update={"do_not_contact": payload.on, "dnc_reason": payload.reason.strip() if payload.on else "", "updated_at": utc_now()})
        store.save("accounts", account)
        record_activity(store, context, "do_not_contact" if payload.on else "do_not_contact_cleared",
                        f"{account.name} {'marked' if payload.on else 'no longer marked'} do not contact: {payload.reason.strip()}", account_id=account.id)
        view = account_detail(context, store, account)
    audit(repository, context, "business.account.do_not_contact", account_id, metadata={"on": payload.on})
    return view


@router.post("/accounts/{account_id}/contacts", response_model=AccountDetail, status_code=status.HTTP_201_CREATED)
def add_contact(account_id: str, payload: ContactInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AccountDetail:
    write(context)
    with open_business_store(context) as store:
        account = get_or_404(store, "accounts", account_id, "Account", for_update=True)
        contact = _contact(context, account_id, payload, utc_now())
        if contact.email and store.first("contacts", email=contact.email):
            raise http_error(409, "A contact with this e-mail address already exists.")
        store.add("contacts", contact)
        record_activity(store, context, "contact_added", f"Contact {contact.name} added to {account.name}.", account_id=account_id,
                        metadata={"consent_basis": contact.consent_basis.value})
        view = account_detail(context, store, account)
    audit(repository, context, "business.contact.created", contact.id, metadata={"account_id": account_id, "consent_basis": contact.consent_basis.value})
    return view


@router.patch("/contacts/{contact_id}", response_model=ContactView)
def update_contact(contact_id: str, payload: ContactUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ContactView:
    write(context)
    changes = payload.model_dump(exclude_unset=True)
    now = utc_now()
    with open_business_store(context) as store:
        contact = get_or_404(store, "contacts", contact_id, "Contact", for_update=True)
        merged = ContactInput(**{**contact.model_dump(include=set(ContactInput.model_fields)), **{k: v for k, v in changes.items() if v is not None or k in {"email", "phone", "profile_url"}}})
        fresh = _contact(context, contact.account_id, merged, now)
        if fresh.email and fresh.email != contact.email:
            other = store.first("contacts", email=fresh.email)
            if other is not None and other.id != contact_id:
                raise http_error(409, "A contact with this e-mail address already exists.")
        consent_changed = fresh.consent_basis != contact.consent_basis
        contact = contact.model_copy(update={
            **fresh.model_dump(include={"name", "role_title", "email", "phone", "profile_url", "consent_basis", "consent_note"}),
            "consent_recorded_at": now if consent_changed else contact.consent_recorded_at, "updated_at": now,
        })
        store.save("contacts", contact)
        if consent_changed:
            record_activity(store, context, "consent_recorded", f"Consent basis for {contact.name}: {contact.consent_basis.value.replace('_', ' ')}.",
                            account_id=contact.account_id, metadata={"consent_basis": contact.consent_basis.value})
        account = store.get("accounts", contact.account_id)
    audit(repository, context, "business.contact.updated", contact_id, metadata={"fields": sorted(changes)})
    return contact_view(context, contact, account)


@router.post("/contacts/{contact_id}/do-not-contact", response_model=ContactView)
def contact_do_not_contact(contact_id: str, payload: DoNotContact, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ContactView:
    """Mark a person do not contact; their e-mail and phone also go on the tenant's opt-out
    list (docs/privacy.md#contact-suppression), so no ANUM channel messages them."""
    write(context)
    if not payload.on:
        manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        contact = get_or_404(store, "contacts", contact_id, "Contact", for_update=True)
        contact = contact.model_copy(update={"do_not_contact": payload.on, "dnc_reason": payload.reason.strip() if payload.on else "", "updated_at": now})
        store.save("contacts", contact)
        record_activity(store, context, "do_not_contact" if payload.on else "do_not_contact_cleared",
                        f"{contact.name} {'marked' if payload.on else 'no longer marked'} do not contact: {payload.reason.strip()}", account_id=contact.account_id)
        leads, _ = store.find("leads", Query(where={"contact_id": contact_id, "state": OPEN_LEAD_STATES}, limit=50))
        if payload.on:
            for lead in leads:
                stamp_state(lead, LeadState.SUPPRESSED, now)
                lead.suppressed_reason = payload.reason.strip()
                lead.updated_at = now
                store.save("leads", lead)
                record_activity(store, context, "state_changed", f"Lead suppressed: {contact.name} is do not contact.", lead_id=lead.id, account_id=lead.account_id,
                                metadata={"to": LeadState.SUPPRESSED.value})
        account = store.get("accounts", contact.account_id)
    if payload.on:
        with suppression.open_suppression_store(context) as entries:
            for value in (contact.email, contact.phone):
                normalized = suppression.normalize(value) if value else None
                if normalized is None:
                    continue
                kind, text = normalized
                entry = suppression.SuppressionEntry(
                    id=new_id("supp"), tenant_id=context.tenant_id, recipient_hash=suppression.recipient_hash(context.tenant_id, text), masked=suppression.mask(kind, text), kind=kind,
                    channel=suppression.SuppressionChannel.ALL, purpose=suppression.SuppressionPurpose.ALL, source=suppression.SuppressionSource.MANUAL,
                    reason=f"Business: {payload.reason.strip()}"[:300], created_by=context.user_id, created_at=now,
                )
                entries.add(context, entry)
    audit(repository, context, "business.contact.do_not_contact", contact_id, metadata={"on": payload.on, "leads_suppressed": len(leads) if payload.on else 0})
    return contact_view(context, contact, account)


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    manage(context)
    with open_business_store(context) as store:
        contact = get_or_404(store, "contacts", contact_id, "Contact", for_update=True)
        for lead in store.find("leads", Query(where={"contact_id": contact_id}, limit=500))[0]:
            store.save("leads", lead.model_copy(update={"contact_id": None, "updated_at": utc_now()}))
        for ticket in store.find("tickets", Query(where={"contact_id": contact_id}, limit=MAX_SCAN))[0]:
            store.save("tickets", ticket.model_copy(update={"contact_id": None, "updated_at": utc_now()}))
        store.delete("contacts", contact_id)
        record_activity(store, context, "contact_deleted", "A contact was deleted.", account_id=contact.account_id)
    audit(repository, context, "business.contact.deleted", contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- leads --------------------------------------------------------------------------------------


class LeadView(Lead):
    account_name: str = ""
    account_domain: str | None = None
    contact_name: str | None = None
    product_name: str | None = None
    block_reason: str | None = None


class LeadCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    account_id: str | None = None
    account: AccountInput | None = None
    contact_id: str | None = None
    contact: ContactInput | None = None
    product_id: str | None = None
    next_step: str = Field(default="", max_length=300)
    source: RecordSource = RecordSource.MANUAL

    @model_validator(mode="after")
    def _one_account(self) -> "LeadCreate":
        if (self.account_id is None) == (self.account is None):
            raise ValueError("Give either an existing account or a new one.")
        if self.contact_id is not None and self.contact is not None:
            raise ValueError("Give either an existing contact or a new one.")
        return self


class LeadUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    product_id: str | None = None
    contact_id: str | None = None
    next_step: str | None = Field(default=None, max_length=300)


class InferenceView(Inference):
    pass


class LeadDetail(BaseModel):
    lead: LeadView
    account: Account
    contact: ContactView | None
    product: Product | None
    evidence: list[Evidence]
    inferences: list[Inference]
    activities: list[Activity]
    drafts: list[Draft]
    research_runs: list[ResearchRun]
    opportunity_id: str | None
    qualification_gaps: list[str]
    allowed_transitions: list[LeadState]
    consent_gap: str | None


def lead_views(context: TenantContext, store: BusinessStore, leads: list[Lead]) -> list[LeadView]:
    accounts: dict[str, Account | None] = {}
    contacts: dict[str, Contact | None] = {}
    products: dict[str, Product | None] = {}
    blocks: dict[tuple[str, str | None], str | None] = {}
    views: list[LeadView] = []
    for lead in leads:
        if lead.account_id not in accounts:
            accounts[lead.account_id] = store.get("accounts", lead.account_id)
        if lead.contact_id and lead.contact_id not in contacts:
            contacts[lead.contact_id] = store.get("contacts", lead.contact_id)
        if lead.product_id and lead.product_id not in products:
            products[lead.product_id] = store.get("products", lead.product_id)
        account = accounts[lead.account_id]
        contact = contacts.get(lead.contact_id) if lead.contact_id else None
        key = (lead.account_id, lead.contact_id)
        if key not in blocks:
            blocks[key] = contact_block(context, contact, bool(account and account.do_not_contact))
        product = products.get(lead.product_id) if lead.product_id else None
        views.append(LeadView(**lead.model_dump(), account_name=account.name if account else "", account_domain=account.domain if account else None,
                              contact_name=contact.name if contact else None, product_name=product.name if product else None, block_reason=blocks[key]))
    return views


def lead_detail(context: TenantContext, store: BusinessStore, lead: Lead) -> LeadDetail:
    account = get_or_404(store, "accounts", lead.account_id, "Account")
    contact = store.get("contacts", lead.contact_id) if lead.contact_id else None
    product = store.get("products", lead.product_id) if lead.product_id else None
    evidence, _ = store.find("evidence", Query(where={"lead_id": lead.id}, order_by="created_at", limit=200))
    inferences, _ = store.find("inferences", Query(where={"lead_id": lead.id}, order_by="created_at", limit=200))
    activities, _ = store.find("activities", Query(where={"lead_id": lead.id}, order_by="created_at", limit=50))
    drafts, _ = store.find("drafts", Query(where={"lead_id": lead.id}, order_by="created_at", limit=50))
    runs, _ = store.find("research_runs", Query(where={"lead_id": lead.id}, order_by="created_at", limit=5))
    opportunity = store.first("opportunities", lead_id=lead.id)
    return LeadDetail(
        lead=lead_views(context, store, [lead])[0], account=account, contact=contact_view(context, contact, account) if contact else None,
        product=product, evidence=evidence, inferences=inferences, activities=activities, drafts=drafts, research_runs=runs,
        opportunity_id=opportunity.id if opportunity else None, qualification_gaps=qualification_gaps(lead, len(evidence)),
        allowed_transitions=sorted(LEAD_TRANSITIONS[lead.state], key=lambda item: list(LeadState).index(item)),
        consent_gap=consent_missing(contact),
    )


@router.get("/leads", response_model=Page[LeadView])
def list_leads(
    state: list[LeadState] = Q(default=[]), q: str | None = Q(default=None, max_length=100), product_id: str | None = None,
    account_id: str | None = None, sort: Literal["updated", "score", "created"] = "updated", limit: int = 50, offset: int = 0,
    context: TenantContext = Depends(tenant_context),
) -> Page[LeadView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if state:
        where["state"] = set(state)
    if product_id:
        where["product_id"] = product_id
    if account_id:
        where["account_id"] = account_id
    order = {"updated": "updated_at", "score": "score_total", "created": "created_at"}[sort]
    with open_business_store(context) as store:
        items, total = store.find("leads", Query(where=where, text=q, text_fields=("title", "next_step"), order_by=order, limit=limit, offset=offset))
        if q and not items and offset == 0:
            # A search also finds leads by their company's name.
            accounts, _ = store.find("accounts", Query(text=q, text_fields=("name", "domain"), limit=50))
            if accounts:
                items, total = store.find("leads", Query(where={**where, "account_id": {item.id for item in accounts}}, order_by=order, limit=limit, offset=offset))
        views = lead_views(context, store, items)
    return Page[LeadView](items=views, total=total, limit=limit, offset=offset)


def create_lead_in(store: BusinessStore, context: TenantContext, payload: LeadCreate, now: datetime) -> tuple[Lead, Account]:
    if payload.account_id:
        account = get_or_404(store, "accounts", payload.account_id, "Account", for_update=True)
    else:
        if payload.account is None:  # pragma: no cover - LeadCreate requires one of the two
            raise http_error(422, "Give either an existing account or a new one.")
        account = _account(context, payload.account, now)
        duplicates = find_duplicates(store, name=account.name, website=account.website, product_id=payload.product_id)
        same_site = next((item for item in duplicates if item.kind == "account" and item.reason.startswith("Same website")), None)
        if same_site is not None:
            raise http_error(409, f"{same_site.label} is already an account (same website). Add the lead to that account instead.")
        store.add("accounts", account)
        record_activity(store, context, "account_created", f"Account {account.name} added.", account_id=account.id)
    if payload.product_id:
        get_or_404(store, "products", payload.product_id, "Product")
    open_leads, _ = store.find("leads", Query(where={"account_id": account.id, "state": OPEN_LEAD_STATES}, limit=50))
    if any((lead.product_id or None) == (payload.product_id or None) for lead in open_leads):
        raise http_error(409, f"{account.name} already has an open lead for this offer.")
    contact_id = None
    if payload.contact_id:
        contact = get_or_404(store, "contacts", payload.contact_id, "Contact")
        if contact.account_id != account.id:
            raise http_error(422, "That contact belongs to another account.")
        contact_id = contact.id
    elif payload.contact is not None:
        contact = _contact(context, account.id, payload.contact, now, payload.source)
        if contact.email and store.first("contacts", email=contact.email):
            raise http_error(409, "A contact with this e-mail address already exists. Choose that contact instead.")
        store.add("contacts", contact)
        contact_id = contact.id
    lead = Lead(
        id=new_id("blead"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id, contact_id=contact_id,
        product_id=payload.product_id, title=" ".join(payload.title.split()), owner_id=context.user_id, source=payload.source,
        next_step=payload.next_step.strip(), created_by=context.user_id, created_at=now, updated_at=now,
    )
    store.add("leads", lead)
    record_activity(store, context, "lead_created", f"Lead \"{lead.title}\" added ({lead.source.value.replace('_', ' ')}).", lead_id=lead.id, account_id=account.id)
    return lead, account


@router.post("/leads", response_model=LeadDetail, status_code=status.HTTP_201_CREATED)
def create_lead(payload: LeadCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    with open_business_store(context) as store:
        lead, _ = create_lead_in(store, context, payload, utc_now())
        detail = lead_detail(context, store, lead)
    audit(repository, context, "business.lead.created", lead.id, metadata={"source": lead.source.value})
    return detail


@router.get("/leads/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: str, context: TenantContext = Depends(tenant_context)) -> LeadDetail:
    read(context)
    with open_business_store(context) as store:
        return lead_detail(context, store, get_or_404(store, "leads", lead_id, "Lead"))


@router.patch("/leads/{lead_id}", response_model=LeadDetail)
def update_lead(lead_id: str, payload: LeadUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    changes = payload.model_dump(exclude_unset=True)
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        if changes.get("product_id"):
            get_or_404(store, "products", changes["product_id"], "Product")
        if changes.get("contact_id"):
            contact = get_or_404(store, "contacts", changes["contact_id"], "Contact")
            if contact.account_id != lead.account_id:
                raise http_error(422, "That contact belongs to another account.")
        if "title" in changes and changes["title"] is None:
            changes.pop("title")
        if "next_step" in changes:
            changes["next_step"] = (changes["next_step"] or "").strip()
        lead = lead.model_copy(update={**changes, "updated_at": utc_now()})
        try:
            store.save("leads", lead)
        except Exception as exc:  # the open-lead unique index (same account and offer)
            if "uq_business_leads_open" in str(exc):
                raise http_error(409, "This account already has an open lead for that offer.") from exc
            raise
        detail = lead_detail(context, store, lead)
    audit(repository, context, "business.lead.updated", lead_id, metadata={"fields": sorted(changes)})
    return detail


@router.delete("/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(lead_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    manage(context)
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        if store.first("opportunities", lead_id=lead_id):
            raise http_error(409, "This lead has an opportunity. Mark the lead lost instead.")
        for kind in ("evidence", "inferences", "drafts", "research_runs"):
            for item in store.find(kind, Query(where={"lead_id": lead_id}, limit=1000))[0]:
                store.delete(kind, item.id)
        store.delete("leads", lead_id)
        record_activity(store, context, "lead_deleted", f"Lead \"{lead.title}\" deleted.", account_id=lead.account_id)
    audit(repository, context, "business.lead.deleted", lead_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class CriterionInput(BaseModel):
    score: int | None = Field(default=None, ge=0, le=5)
    reason: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class ScoreUpdate(BaseModel):
    criteria: dict[Criterion, CriterionInput]


@router.put("/leads/{lead_id}/score", response_model=LeadDetail)
def score_lead(lead_id: str, payload: ScoreUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        own = {item.id for item in store.find("evidence", Query(where={"lead_id": lead_id}, limit=500))[0]}
        updates: dict[Criterion, CriterionScore] = {}
        for criterion, value in payload.criteria.items():
            if value.score is not None and not value.reason.strip():
                raise http_error(422, f"Give the reason for the {criterion.value.replace('_', ' ')} score.")
            if set(value.evidence_ids) - own:
                raise http_error(422, "Link only this lead's own evidence.")
            updates[criterion] = CriterionScore(score=value.score, reason=value.reason.strip(), evidence_ids=list(dict.fromkeys(value.evidence_ids)))
        changed = apply_score(lead, updates, context.user_id, now)
        lead.updated_at = now
        store.save("leads", lead)
        if changed:
            record_activity(store, context, "score_changed", f"Score {lead.score_total}/30 ({lead.score_known} of 6 criteria known).", lead_id=lead.id,
                            account_id=lead.account_id, metadata={"criteria": changed, "total": lead.score_total, "known": lead.score_known})
        detail = lead_detail(context, store, lead)
    audit(repository, context, "business.lead.scored", lead_id, metadata={"criteria": changed, "total": lead.score_total})
    return detail


class Transition(BaseModel):
    to: LeadState
    reason: str = Field(default="", max_length=300)


@router.post("/leads/{lead_id}/transition", response_model=LeadDetail)
def transition_lead(lead_id: str, payload: Transition, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        before = lead.state
        if not can_move(lead.state, payload.to):
            allowed = ", ".join(sorted(item.value for item in LEAD_TRANSITIONS[lead.state])) or "none"
            raise http_error(409, f"A {lead.state.value} lead cannot move to {payload.to.value}. Next states: {allowed}.")
        reason = payload.reason.strip()
        account = get_or_404(store, "accounts", lead.account_id, "Account")
        contact = store.get("contacts", lead.contact_id) if lead.contact_id else None
        if payload.to == LeadState.QUALIFIED:
            evidence = store.count("evidence", Query(where={"lead_id": lead_id}))
            gaps = qualification_gaps(lead, evidence)
            if gaps:
                raise http_error(409, "Not ready to qualify: " + " ".join(gaps))
        if payload.to in {LeadState.CONTACTED, LeadState.REPLIED, LeadState.MEETING}:
            block = contact_block(context, contact, account.do_not_contact)
            if block:
                raise http_error(409, f"{block} Nothing may be recorded as a contact.")
        if payload.to == LeadState.CONTACTED:
            missing = consent_missing(contact)
            if missing:
                raise http_error(409, missing)
        if payload.to == LeadState.WON:
            raise http_error(409, "A lead is won when its proposal is accepted (Proposals, then Approvals).")
        if payload.to in {LeadState.LOST, LeadState.SUPPRESSED} and len(reason) < 3:
            raise http_error(422, "Give a reason of at least 3 characters.")
        if before in {LeadState.SUPPRESSED, LeadState.WON}:
            manage(context)
            block = contact_block(context, contact, account.do_not_contact)
            if before == LeadState.SUPPRESSED and block:
                raise http_error(409, f"{block} Clear that first.")
        stamp_state(lead, payload.to, now)
        if payload.to == LeadState.LOST:
            lead.lost_reason = reason
        if payload.to == LeadState.SUPPRESSED:
            lead.suppressed_reason = reason
        lead.updated_at = now
        store.save("leads", lead)
        record_activity(store, context, "state_changed", f"{before.value.capitalize()} to {payload.to.value}" + (f": {reason}" if reason else "."),
                        lead_id=lead.id, account_id=lead.account_id, metadata={"from": before.value, "to": payload.to.value})
        detail = lead_detail(context, store, lead)
    audit(repository, context, "business.lead.transitioned", lead_id, metadata={"from": before.value, "to": payload.to.value})
    return detail


ActivityKind = Literal["note", "contact_logged", "call_logged", "meeting_logged", "reply_logged"]


class ActivityInput(BaseModel):
    kind: ActivityKind = "note"
    summary: str = Field(min_length=2, max_length=500)


@router.post("/leads/{lead_id}/activities", response_model=Activity)
def log_activity(
    lead_id: str, payload: ActivityInput, response: Response, idempotency_key: str | None = Header(default=None, max_length=120),
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> Activity:
    """A note or a manually made contact (outside ANUM). The same Idempotency-Key records once."""
    write(context)
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        if payload.kind != "note":
            account = get_or_404(store, "accounts", lead.account_id, "Account")
            contact = store.get("contacts", lead.contact_id) if lead.contact_id else None
            block = contact_block(context, contact, account.do_not_contact)
            if block:
                raise http_error(409, f"{block} Nothing may be recorded as a contact.")
        key = f"lead:{lead_id}:{idempotency_key}" if idempotency_key else None
        activity, created = record_activity(store, context, payload.kind, payload.summary.strip(), lead_id=lead.id, account_id=lead.account_id, idempotency_key=key)
        if created and payload.kind != "note":
            store.save("leads", lead.model_copy(update={"updated_at": utc_now()}))
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    if created:
        audit(repository, context, "business.activity.logged", activity.id, metadata={"lead_id": lead_id, "kind": payload.kind})
    return activity


# -- evidence and inferences ----------------------------------------------------------------------


class EvidenceInput(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    title: str = Field(default="", max_length=300)
    published_on: date | None = None
    excerpt: str = Field(default="", max_length=1200)
    confidence: Confidence = Confidence.MEDIUM


@router.post("/leads/{lead_id}/evidence", response_model=LeadDetail, status_code=status.HTTP_201_CREATED)
def add_evidence(lead_id: str, payload: EvidenceInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    url = clean_url(payload.url)
    if url is None:
        raise http_error(422, "Enter the source as an http(s) address.")
    now = utc_now()
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        key = url_key(url)
        if store.first("evidence", lead_id=lead_id, url_key=key):
            raise http_error(409, "This source is already recorded for the lead.")
        item = Evidence(
            id=new_id("bevi"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead_id, account_id=lead.account_id, url=url,
            url_key=key, title=" ".join(payload.title.split()), published_on=payload.published_on, excerpt=payload.excerpt.strip(),
            confidence=payload.confidence, source=RecordSource.MANUAL, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("evidence", item)
        store.save("leads", lead.model_copy(update={"last_verified_at": now, "updated_at": now}))
        record_activity(store, context, "evidence_added", f"Source added: {item.title or domain_of(url) or url}"[:500], lead_id=lead_id, account_id=lead.account_id)
        detail = lead_detail(context, store, store.get("leads", lead_id))
    audit(repository, context, "business.evidence.added", item.id, metadata={"lead_id": lead_id, "confidence": item.confidence.value})
    return detail


@router.delete("/evidence/{evidence_id}", response_model=LeadDetail)
def delete_evidence(evidence_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    with open_business_store(context) as store:
        item = get_or_404(store, "evidence", evidence_id, "Source", for_update=True)
        lead = get_or_404(store, "leads", item.lead_id, "Lead", for_update=True)
        for criterion, value in list(lead.score.items()):
            if evidence_id in value.evidence_ids:
                lead.score[criterion] = value.model_copy(update={"evidence_ids": [x for x in value.evidence_ids if x != evidence_id]})
        lead.updated_at = utc_now()
        store.save("leads", lead)
        for inference in store.find("inferences", Query(where={"lead_id": lead.id}, limit=500))[0]:
            if evidence_id in inference.basis_evidence_ids:
                store.save("inferences", inference.model_copy(update={"basis_evidence_ids": [x for x in inference.basis_evidence_ids if x != evidence_id], "updated_at": utc_now()}))
        store.delete("evidence", evidence_id)
        record_activity(store, context, "evidence_removed", f"Source removed: {item.title or item.url}"[:500], lead_id=lead.id, account_id=lead.account_id)
        detail = lead_detail(context, store, lead)
    audit(repository, context, "business.evidence.removed", evidence_id, metadata={"lead_id": lead.id})
    return detail


class InferenceInput(BaseModel):
    statement: str = Field(min_length=3, max_length=1000)
    criterion: Criterion | None = None
    suggested_score: int | None = Field(default=None, ge=0, le=5)
    confidence: Confidence = Confidence.LOW
    basis_evidence_ids: list[str] = Field(min_length=1, max_length=20)


@router.post("/leads/{lead_id}/inferences", response_model=LeadDetail, status_code=status.HTTP_201_CREATED)
def add_inference(lead_id: str, payload: InferenceInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", lead_id, "Lead", for_update=True)
        own = {item.id for item in store.find("evidence", Query(where={"lead_id": lead_id}, limit=500))[0]}
        if set(payload.basis_evidence_ids) - own:
            raise http_error(422, "An inference rests on this lead's own evidence: link at least one of its sources.")
        item = Inference(id=new_id("binf"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead_id,
                         statement=" ".join(payload.statement.split()), criterion=payload.criterion, suggested_score=payload.suggested_score,
                         confidence=payload.confidence, basis_evidence_ids=list(dict.fromkeys(payload.basis_evidence_ids)), generator="human",
                         status=InferenceStatus.ACCEPTED, reviewed_by=context.user_id, created_by=context.user_id, created_at=now, updated_at=now)
        store.add("inferences", item)
        store.save("leads", lead.model_copy(update={"updated_at": now}))
        detail = lead_detail(context, store, store.get("leads", lead_id))
    audit(repository, context, "business.inference.added", item.id, metadata={"lead_id": lead_id})
    return detail


class InferenceReview(BaseModel):
    status: Literal["accepted", "rejected"]


@router.post("/inferences/{inference_id}/review", response_model=LeadDetail)
def review_inference(inference_id: str, payload: InferenceReview, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> LeadDetail:
    write(context)
    with open_business_store(context) as store:
        item = get_or_404(store, "inferences", inference_id, "Inference", for_update=True)
        store.save("inferences", item.model_copy(update={"status": InferenceStatus(payload.status), "reviewed_by": context.user_id, "updated_at": utc_now()}))
        record_activity(store, context, "inference_reviewed", f"Inference {payload.status}: {item.statement[:200]}", lead_id=item.lead_id)
        detail = lead_detail(context, store, get_or_404(store, "leads", item.lead_id, "Lead"))
    audit(repository, context, "business.inference.reviewed", inference_id, metadata={"status": payload.status})
    return detail


# -- research -------------------------------------------------------------------------------------


@router.post("/leads/{lead_id}/research", response_model=LeadDetail)
async def research_lead(lead_id: str, context: TenantContext = Depends(tenant_context)) -> LeadDetail:
    write(context)
    try:
        await research.run_research(context, lead_id)
    except research.ResearchRefused as exc:
        raise http_error(exc.status_code, exc.detail) from exc

    def detail() -> LeadDetail:
        with open_business_store(context) as store:
            return lead_detail(context, store, get_or_404(store, "leads", lead_id, "Lead"))

    return await asyncio.to_thread(detail)


# -- outbound drafts (sending is off) -------------------------------------------------------------


class DraftCreate(BaseModel):
    channel: DraftChannel = DraftChannel.EMAIL
    language: Literal["en", "ar"] = "en"


class DraftUpdate(BaseModel):
    subject: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class DraftResult(BaseModel):
    lead: LeadDetail
    draft: Draft
    note: str | None = None


def _draft_inputs(store: BusinessStore, lead: Lead) -> tuple[Account, Contact | None, Product | None, Offer | None, list[Evidence]]:
    account = get_or_404(store, "accounts", lead.account_id, "Account")
    contact = store.get("contacts", lead.contact_id) if lead.contact_id else None
    product = store.get("products", lead.product_id) if lead.product_id else None
    offer = next((item for item in _offers(store, product.id) if item.status == OfferStatus.ACTIVE), None) if product else None
    evidence, _ = store.find("evidence", Query(where={"lead_id": lead.id}, order_by="created_at", limit=20))
    return account, contact, product, offer, evidence


@router.post("/leads/{lead_id}/drafts", response_model=DraftResult, status_code=status.HTTP_201_CREATED)
async def create_draft(lead_id: str, payload: DraftCreate, context: TenantContext = Depends(tenant_context)) -> DraftResult:
    write(context)

    def load() -> tuple[Lead, Account, Contact | None, Product | None, Offer | None, list[Evidence], str]:
        with open_business_store(context) as store:
            lead = get_or_404(store, "leads", lead_id, "Lead")
            account, contact, product, offer, evidence = _draft_inputs(store, lead)
            if lead.state in {LeadState.SUPPRESSED, LeadState.LOST, LeadState.WON}:
                raise http_error(409, f"This lead is {lead.state.value}; drafts are for open leads.")
            block = contact_block(context, contact, account.do_not_contact)
            if block:
                raise http_error(409, f"{block} No message is drafted.")
            from .pipeline import load_settings, value_proposition

            paused = load_settings(store, context)
            if paused.paused:
                raise http_error(409, f"Business outreach is paused: {paused.paused_reason or 'an owner paused it'}. No message is drafted.")
            # Drafts use the lead's product's own value proposition (its target customer profile).
            return lead, account, contact, product, offer, evidence, value_proposition(store, lead.product_id, payload.language)

    lead, account, contact, product, offer, evidence, pitch = await asyncio.to_thread(load)
    text, generator, note = await assist.draft_message(context, payload.channel, payload.language, account, contact, product, offer, evidence,
                                                       value_proposition=pitch or None)
    facts = assist.facts_text(product, offer, evidence, account, pitch)
    now = utc_now()
    draft = Draft(
        id=new_id("bdraft"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, contact_id=contact.id if contact else None,
        channel=payload.channel, language=payload.language, subject=text.subject, body=text.body, generator=generator,
        untrusted_source=bool(evidence), issues=assist.check_draft(payload.channel, payload.language, text.subject, text.body, facts),
        created_by=context.user_id, created_at=now, updated_at=now,
    )

    def save() -> LeadDetail:
        with open_business_store(context) as store:
            store.add("drafts", draft)
            record_activity(store, context, "draft_created", f"{payload.channel.value.replace('_', ' ').capitalize()} draft written ({generator}); not sent.", lead_id=lead.id,
                            account_id=lead.account_id, metadata={"draft_id": draft.id, "generator": generator})
            return lead_detail(context, store, get_or_404(store, "leads", lead.id, "Lead"))

    detail = await asyncio.to_thread(save)
    await asyncio.to_thread(audit, None, context, "business.draft.created", draft.id, metadata={"lead_id": lead.id, "channel": payload.channel.value, "generator": generator})
    return DraftResult(lead=detail, draft=draft, note=note)


def _recheck(store: BusinessStore, draft: Draft) -> list[str]:
    lead = get_or_404(store, "leads", draft.lead_id, "Lead")
    account, _, product, offer, evidence = _draft_inputs(store, lead)
    from .pipeline import value_proposition

    facts = assist.facts_text(product, offer, evidence, account, value_proposition(store, lead.product_id))
    return assist.check_draft(draft.channel, draft.language, draft.subject, draft.body, facts)


@router.patch("/drafts/{draft_id}", response_model=Draft)
def update_draft(draft_id: str, payload: DraftUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Draft:
    write(context)
    with open_business_store(context) as store:
        draft = get_or_404(store, "drafts", draft_id, "Draft", for_update=True)
        if draft.status == DraftStatus.DISCARDED:
            raise http_error(409, "This draft was discarded. Write a new one.")
        draft = draft.model_copy(update={"subject": payload.subject.strip(), "body": payload.body.strip(), "generator": "human" if draft.generator == "template" else draft.generator,
                                         "status": DraftStatus.DRAFT, "approved_hash": None, "approved_by": None, "approved_at": None, "updated_at": utc_now()})
        draft.issues = _recheck(store, draft)
        store.save("drafts", draft)
    audit(repository, context, "business.draft.updated", draft_id)
    return draft


@router.post("/drafts/{draft_id}/approve", response_model=Draft)
def approve_draft(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Draft:
    """A person's review of the exact text. It does not send: sending is off in this release."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        draft = get_or_404(store, "drafts", draft_id, "Draft", for_update=True)
        if draft.status != DraftStatus.DRAFT:
            raise http_error(409, f"This draft is {draft.status.value}.")
        draft.issues = _recheck(store, draft)
        if draft.issues:
            store.save("drafts", draft)
            raise http_error(409, "Fix the draft first: " + " ".join(draft.issues))
        lead = get_or_404(store, "leads", draft.lead_id, "Lead")
        account = get_or_404(store, "accounts", lead.account_id, "Account")
        contact = store.get("contacts", draft.contact_id) if draft.contact_id else None
        block = contact_block(context, contact, account.do_not_contact)
        if block:
            raise http_error(409, f"{block} The draft cannot be approved.")
        draft = draft.model_copy(update={"status": DraftStatus.APPROVED, "approved_hash": draft_hash(draft.subject, draft.body, draft.channel.value, draft.contact_id),
                                         "approved_by": context.user_id, "approved_at": now, "updated_at": now})
        store.save("drafts", draft)
        record_activity(store, context, "draft_approved", "Draft approved for manual use; ANUM does not send it.", lead_id=lead.id, account_id=lead.account_id,
                        metadata={"draft_id": draft.id})
    audit(repository, context, "business.draft.approved", draft_id, metadata={"hash": draft.approved_hash})
    return draft


@router.post("/drafts/{draft_id}/discard", response_model=Draft)
def discard_draft(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Draft:
    write(context)
    with open_business_store(context) as store:
        draft = get_or_404(store, "drafts", draft_id, "Draft", for_update=True)
        draft = draft.model_copy(update={"status": DraftStatus.DISCARDED, "updated_at": utc_now()})
        store.save("drafts", draft)
    audit(repository, context, "business.draft.discarded", draft_id)
    return draft


# -- CSV import --------------------------------------------------------------------------------

IMPORT_COLUMNS = ("company", "website", "industry", "country", "city", "contact_name", "role", "email", "phone", "consent_basis", "lead_title", "notes")
MAX_IMPORT_BYTES = 512 * 1024
MAX_IMPORT_ROWS = 1000


class ImportRequest(BaseModel):
    csv: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)
    product_id: str | None = None
    dry_run: bool = True


class ImportRow(BaseModel):
    row: int
    company: str
    status: Literal["created", "duplicate", "suppressed", "invalid"]
    message: str
    lead_id: str | None = None


class ImportResult(BaseModel):
    dry_run: bool
    columns: list[str]
    rows: list[ImportRow]
    created: int
    duplicates: int
    suppressed: int
    invalid: int


@router.post("/import/leads", response_model=ImportResult)
def import_leads(payload: ImportRequest, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ImportResult:
    """Leads from a CSV the owner prepared (one row per company). A dry run reports what
    would happen; nothing is enriched or looked up outside ANUM."""
    write(context)
    reader = csv.DictReader(io.StringIO(payload.csv.lstrip("﻿")))
    columns = [name.strip().lower() for name in (reader.fieldnames or [])]
    if "company" not in columns:
        raise http_error(422, f"The first line must name the columns, including company. Known columns: {', '.join(IMPORT_COLUMNS)}.")
    reader.fieldnames = columns
    rows: list[ImportRow] = []
    now = utc_now()
    with open_business_store(context) as store:
        if payload.product_id:
            get_or_404(store, "products", payload.product_id, "Product")
        seen_domains: set[str] = set()
        seen_names: set[str] = set()
        for index, raw in enumerate(reader, start=2):
            if index - 1 > MAX_IMPORT_ROWS:
                raise http_error(413, f"Import at most {MAX_IMPORT_ROWS} rows at a time.")
            row = {key: (value or "").strip() for key, value in raw.items() if key}
            company = " ".join(row.get("company", "").split())
            if len(company) < 2:
                rows.append(ImportRow(row=index, company=company, status="invalid", message="The company name is missing."))
                continue
            try:
                basis = ConsentBasis(row.get("consent_basis") or "unknown")
            except ValueError:
                rows.append(ImportRow(row=index, company=company, status="invalid", message="Unknown consent basis; use one of " + ", ".join(item.value for item in ConsentBasis) + "."))
                continue
            website = row.get("website") or None
            if website and clean_url(website) is None:
                rows.append(ImportRow(row=index, company=company, status="invalid", message="The website is not an http(s) address."))
                continue
            if row.get("email") and normal_email(row.get("email")) is None:
                rows.append(ImportRow(row=index, company=company, status="invalid", message="The e-mail address is not valid."))
                continue
            if row.get("phone") and normal_phone(row.get("phone")) is None:
                rows.append(ImportRow(row=index, company=company, status="invalid", message="The phone number needs its country code."))
                continue
            domain = domain_of(website)
            key = name_key(company)
            duplicates = find_duplicates(store, name=company, website=website, email=row.get("email"), phone=row.get("phone"), product_id=payload.product_id)
            if (domain and domain in seen_domains) or key in seen_names:
                rows.append(ImportRow(row=index, company=company, status="duplicate", message="The same company appears earlier in this file."))
                continue
            seen_names.add(key)
            if domain:
                seen_domains.add(domain)
            blocking = [item for item in duplicates if item.kind in {"lead", "contact"} or item.reason.startswith("Same website")]
            if blocking:
                rows.append(ImportRow(row=index, company=company, status="duplicate", message=blocking[0].reason))
                continue
            contact_input = None
            if row.get("contact_name") or row.get("email") or row.get("phone"):
                contact_input = ContactInput(name=row.get("contact_name") or company, role_title=row.get("role", "")[:160], email=row.get("email") or None,
                                             phone=row.get("phone") or None, consent_basis=basis)
            probe = Contact(id="probe", tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id="probe", name=company,
                            email=normal_email(row.get("email")), phone=normal_phone(row.get("phone")), created_by="probe", created_at=now, updated_at=now)
            opted_out = contact_suppressed(context, probe) if contact_input else []
            same_name = next((item for item in duplicates if item.kind == "account"), None)
            if payload.dry_run:
                status_ = "suppressed" if opted_out else "created"
                message = (f"{', '.join(opted_out)} is on the opt-out list: the lead would be added as suppressed." if opted_out
                           else ("Would be added to the existing account " + same_name.label + "." if same_name else "Would be added."))
                rows.append(ImportRow(row=index, company=company, status=status_, message=message))
                continue
            create = LeadCreate(
                title=(row.get("lead_title") or f"{company} lead")[:200], account_id=same_name.id if same_name else None,
                account=None if same_name else AccountInput(name=company, website=website, industry=row.get("industry", "")[:120], country=row.get("country", "")[:80],
                                                           city=row.get("city", "")[:80], notes=row.get("notes", "")[:4000], source=RecordSource.CSV_IMPORT),
                contact=contact_input, product_id=payload.product_id, source=RecordSource.CSV_IMPORT,
            )
            try:
                lead, _ = create_lead_in(store, context, create, now)
            except Exception as exc:  # an HTTP refusal for this row only
                detail = getattr(exc, "detail", None)
                if detail is None:
                    raise
                rows.append(ImportRow(row=index, company=company, status="duplicate" if getattr(exc, "status_code", 0) == 409 else "invalid", message=str(detail)))
                continue
            if opted_out:
                stamp_state(lead, LeadState.SUPPRESSED, now)
                lead.suppressed_reason = "On the contact opt-out list at import."
                store.save("leads", lead)
                if lead.contact_id:
                    contact = store.get("contacts", lead.contact_id)
                    store.save("contacts", contact.model_copy(update={"do_not_contact": True, "dnc_reason": "On the contact opt-out list."}))
                rows.append(ImportRow(row=index, company=company, status="suppressed", message=f"{', '.join(opted_out)} is on the opt-out list: added as suppressed.", lead_id=lead.id))
            else:
                rows.append(ImportRow(row=index, company=company, status="created", message="Added.", lead_id=lead.id))
    counts = {name: sum(1 for item in rows if item.status == name) for name in ("created", "duplicate", "suppressed", "invalid")}
    audit(repository, context, "business.leads.import_checked" if payload.dry_run else "business.leads.imported", context.workspace_id,
          metadata={"rows": len(rows), **counts})
    return ImportResult(dry_run=payload.dry_run, columns=columns, rows=rows, created=counts["created"], duplicates=counts["duplicate"],
                        suppressed=counts["suppressed"], invalid=counts["invalid"])


AccountDetail.model_rebuild()
