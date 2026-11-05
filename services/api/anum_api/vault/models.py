"""Records and fixed vocabularies of the document vault (docs/documents.md).

Pure: no I/O, so the unit tests and both stores share one definition.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AccessClass = Literal["private", "workspace"]
DocumentStatus = Literal["active", "deleted"]
ExtractionKind = Literal["receipt", "dates"]
ExtractionStatus = Literal["queued", "pending", "confirmed", "rejected", "failed"]
LinkTarget = Literal["vehicle", "person", "business", "purchase", "finance_transaction"]
Source = Literal["web", "device", "api"]

# The built-in categories (owner spec, 4 Nov 2026). A workspace adds its own (``vcat_...`` ids).
BUILT_IN_CATEGORIES: tuple[str, ...] = (
    "passport", "national_id", "residency", "visa", "contract", "salary_certificate", "vehicle_registration",
    "insurance", "bank_statement", "receipt", "invoice", "purchase_order", "agreement", "warranty",
    "maintenance_invoice", "certificate", "other",
)
# Identity documents: their number is an identity number and their dates are read only when the
# workspace owner turned identity date reading on (``ocr_identity_dates``).
IDENTITY_CATEGORIES = frozenset({"passport", "national_id", "residency", "visa"})
# Categories whose scans the receipt reader looks at.
RECEIPT_CATEGORIES = frozenset({"receipt", "invoice", "maintenance_invoice", "purchase_order"})
# Categories whose scans the date reader looks at (issue and expiry dates only, never numbers).
DATE_CATEGORIES = frozenset(IDENTITY_CATEGORIES | {"insurance", "vehicle_registration", "warranty", "certificate"})

CUSTOM_CATEGORY = re.compile(r"vcat_[A-Za-z0-9_-]{4,60}")
COUNTRY = re.compile(r"[A-Z]{2}")
TAG = re.compile(r"[^\x00-\x1f\x7f]{1,40}")
MAX_TAGS = 12
LINK_TYPES: tuple[str, ...] = ("vehicle", "person", "business", "purchase", "finance_transaction")


def is_category(value: str, custom_ids: set[str] | frozenset[str]) -> bool:
    return value in BUILT_IN_CATEGORIES or (bool(CUSTOM_CATEGORY.fullmatch(value)) and value in custom_ids)


def extraction_kind(category: str) -> ExtractionKind | None:
    """Which reader a category's scans go to; None when neither applies."""
    if category in RECEIPT_CATEGORIES:
        return "receipt"
    if category in DATE_CATEGORIES:
        return "dates"
    return None


_NUMBER_CHARS = re.compile(r"[^A-Za-z0-9]")


def normalize_number(value: str) -> str:
    """An identity or account number without spaces, dashes or dots; letters upper case."""
    return _NUMBER_CHARS.sub("", value).upper()


def last_four(number: str) -> str:
    return normalize_number(number)[-4:]


def masked_number(last4: str | None) -> str | None:
    """How lists show a number: the last four only, never the rest."""
    return None if not last4 else f"•••• {last4}"


class VaultSettings(BaseModel):
    reminder_days: int = Field(default=30, ge=1, le=365)
    trash_days: int = Field(default=30, ge=1, le=365)
    ocr_receipts: bool = False
    ocr_identity_dates: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None


class CategoryRecord(BaseModel):
    id: str
    label: str
    created_by: str
    created_at: datetime


class DocumentRecord(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    file_id: str
    owner_user_id: str
    title: str
    category: str
    access_class: AccessClass = "private"
    status: DocumentStatus = "active"
    content_sha256: str
    issue_date: date | None = None
    expiry_date: date | None = None
    issuer: str | None = None
    country: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    number_ciphertext: str | None = Field(default=None, repr=False)
    number_last4: str | None = None
    receipt: dict[str, Any] | None = None
    assistant_may_read: bool = False
    reminder_days: int | None = None
    reminders_sent: list[str] = Field(default_factory=list)
    retention_until: date | None = None
    source: Source = "web"
    row_version: int = 1
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    purge_after: datetime | None = None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class AccessGrant(BaseModel):
    document_id: str
    user_id: str
    granted_by: str
    granted_at: datetime


class LinkRecord(BaseModel):
    id: str
    document_id: str
    target_type: LinkTarget
    target_id: str
    label: str = ""
    status: Literal["proposed", "confirmed"] = "confirmed"
    created_by: str
    created_at: datetime


class ExtractionRecord(BaseModel):
    id: str
    document_id: str
    file_sha256: str
    kind: ExtractionKind
    status: ExtractionStatus
    engine: str = ""
    languages: str = ""
    # {name: {"value": ..., "confidence": 0..1, "source": "the line it came from" | None}}
    fields: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    requested_by: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AccessEvent(BaseModel):
    id: str
    document_id: str
    actor: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DeviceRecord(BaseModel):
    id: str
    owner_user_id: str
    name: str
    token_hash: str = Field(repr=False)
    hint: str
    scopes: list[str] = Field(default_factory=lambda: ["documents"])
    status: Literal["active", "revoked"] = "active"
    last_seen_at: datetime | None = None
    last_error: str | None = None
    upload_count: int = 0
    created_at: datetime
    revoked_at: datetime | None = None


class DocumentQuery(BaseModel):
    """One page of documents the caller may see. ``after`` is (created_at, id) of the previous page's last row."""

    status: DocumentStatus = "active"
    search: str | None = None
    category: str | None = None
    access_class: AccessClass | None = None
    expiring_within_days: int | None = None
    expired: bool | None = None
    tag: str | None = None
    # ``created`` (newest added first), ``updated`` (newest change first) or ``expiry`` (the next expiry first: only documents
    # that expire today or later); ``after`` holds that time (or day) and the id.
    sort: Literal["created", "updated", "expiry"] = "created"
    limit: int = 50
    after: tuple[datetime, str] | None = None


__all__ = [
    "BUILT_IN_CATEGORIES",
    "DATE_CATEGORIES",
    "IDENTITY_CATEGORIES",
    "LINK_TYPES",
    "RECEIPT_CATEGORIES",
    "AccessClass",
    "AccessEvent",
    "AccessGrant",
    "CategoryRecord",
    "DeviceRecord",
    "DocumentQuery",
    "DocumentRecord",
    "ExtractionRecord",
    "LinkRecord",
    "VaultSettings",
    "extraction_kind",
    "is_category",
    "last_four",
    "masked_number",
    "normalize_number",
]
