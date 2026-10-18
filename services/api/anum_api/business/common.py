"""Request shapes, permission checks and record lookups shared by the Business routes."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, require_permission
from ..schemas import TenantContext
from .service import http_error
from .store import MAX_PAGE, BusinessStore

T = TypeVar("T")

READ_DENIED = "Business is for workspace owners and members: it holds contacts' personal data. Ask an owner to make you a member."
MANAGE_DENIED = "Only workspace owners can do this (the catalog, research caps, deletions and removing a do-not-contact mark)."


def can_read(context: TenantContext) -> bool:
    return has_permission(context, Permission.BUSINESS_READ)


def read(context: TenantContext) -> None:
    if not can_read(context):
        raise http_error(403, READ_DENIED)
    require_permission(context, Permission.BUSINESS_READ)


def write(context: TenantContext) -> None:
    read(context)
    if not has_permission(context, Permission.BUSINESS_WRITE):
        raise http_error(403, "Only workspace owners and members can change Business records.")


def manage(context: TenantContext) -> None:
    read(context)
    if not has_permission(context, Permission.BUSINESS_MANAGE):
        raise http_error(403, MANAGE_DENIED)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


def page_bounds(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_PAGE)), max(0, offset)


def get_or_404(store: BusinessStore, kind: str, item_id: str, label: str, *, for_update: bool = False) -> Any:
    found = store.get(kind, item_id, for_update=for_update)
    if found is None:
        raise http_error(404, f"{label} not found")
    return found


class Reason(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def clean_list(values: list[str], limit: int = 300) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:limit]
        if text and text not in seen:
            seen.append(text)
    return seen
