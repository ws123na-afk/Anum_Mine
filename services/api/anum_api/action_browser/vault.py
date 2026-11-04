"""Saved site logins and payment cards (docs/action-browser.md#saved-logins-and-cards).

Owner only. Each record's secret parts are one Fernet value (``ANUM_SECRETS_KEY``,
``secret_box``) in ``action_browser_credentials.secret_ciphertext``; lists show the handle, the
site and a masked hint and never decrypt. :func:`resolve_secret` is the one place a value is
decrypted: by the tool, at the moment of typing, in its own short unit of work.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..audit import AuditRecord
from ..schemas import TenantContext, new_id, utc_now
from ..secret_box import secret_cipher
from .handles import Handle, card_hint, luhn_ok, mask_username, normalize_site, valid_handle_name
from .store import CredentialRecord, open_action_store

EXPIRY = re.compile(r"^(0[1-9]|1[0-2])/(\d{2})$")


class CredentialProblem(ValueError):
    """A write the vault refuses. The message is safe to show."""


class CredentialWrite(BaseModel):
    """A new login or card, or changes to one. Secret parts left empty keep the saved ones."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["login", "card"]
    handle: str = Field(min_length=1, max_length=40)
    site: str | None = Field(default=None, max_length=300)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=200, repr=False)
    name: str | None = Field(default=None, max_length=100)
    number: str | None = Field(default=None, max_length=30, repr=False)
    expiry: str | None = Field(default=None, max_length=5)
    cvc: str | None = Field(default=None, max_length=4, repr=False)


class CredentialView(BaseModel):
    id: str
    kind: str
    handle: str
    site: str | None = None
    hint: str
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str


def view(record: CredentialRecord) -> CredentialView:
    return CredentialView(
        id=record.id, kind=record.kind, handle=record.handle, site=record.site, hint=record.hint, created_by=record.created_by,
        created_at=record.created_at.isoformat(), updated_by=record.updated_by, updated_at=record.updated_at.isoformat(),
    )


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _secret_parts(write: CredentialWrite, current: dict[str, str] | None) -> tuple[dict[str, str], str, str | None]:
    """The secret JSON, the masked hint and the site, from a write over the saved parts."""
    saved = dict(current or {})
    if write.kind == "login":
        site = normalize_site(write.site or "")
        if site is None:
            raise CredentialProblem("Give the site this login is for, for example example-air.com.")
        username = _clean(write.username) or saved.get("username", "")
        password = write.password if write.password else saved.get("password", "")
        if not username:
            raise CredentialProblem("Give the user name or email of this login.")
        if not password:
            raise CredentialProblem("Give the password of this login.")
        if any(ord(ch) < 32 for ch in username + password):
            raise CredentialProblem("The user name and password cannot hold control characters.")
        return {"username": username, "password": password}, f"{mask_username(username)} on {site}", site
    number = re.sub(r"[\s-]", "", write.number or "") or saved.get("number", "")
    expiry = _clean(write.expiry) or saved.get("expiry", "")
    cvc = _clean(write.cvc) or saved.get("cvc", "")
    name = _clean(write.name) or saved.get("name", "")
    if not luhn_ok(number):
        raise CredentialProblem("The card number is not valid (13 to 19 digits that pass the card check).")
    if not EXPIRY.match(expiry):
        raise CredentialProblem("Give the expiry as MM/YY, for example 08/29.")
    if not re.fullmatch(r"\d{3,4}", cvc):
        raise CredentialProblem("The security code (CVC) is 3 or 4 digits.")
    if not name or any(ord(ch) < 32 for ch in name):
        raise CredentialProblem("Give the name on the card.")
    return {"number": number, "expiry": expiry, "cvc": cvc, "name": name}, card_hint(number, expiry), None


def _audit(store: Any, context: TenantContext, action: str, record: CredentialRecord) -> None:
    store.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=f"action_browser_credential:{record.id}", outcome="success", correlation_id=new_id("correlation"),
        created_at=utc_now(), metadata={"kind": record.kind, "handle": record.handle, "site": record.site},
    ))


def list_credentials(context: TenantContext) -> list[CredentialView]:
    with open_action_store(context) as store:
        return [view(record) for record in store.credentials(context)]


def create_credential(context: TenantContext, write: CredentialWrite) -> CredentialView:
    handle = write.handle.strip().lower()
    if not valid_handle_name(handle):
        raise CredentialProblem("A handle is lowercase letters, digits and dashes, starting with a letter or digit (at most 40).")
    parts, hint, site = _secret_parts(write, None)
    now = utc_now()
    record = CredentialRecord(
        id=new_id("abcred"), kind=write.kind, handle=handle, site=site, hint=hint,
        secret_ciphertext=secret_cipher().encrypt(json.dumps(parts)), created_by=context.user_id, created_at=now,
        updated_by=context.user_id, updated_at=now,
    )
    with open_action_store(context) as store:
        try:
            store.save_credential(context, record)
        except ValueError:
            raise CredentialProblem(f"There is already a saved {write.kind} called {handle}.") from None
        _audit(store, context, "action_browser.credential_created", record)
    return view(record)


def update_credential(context: TenantContext, credential_id: str, write: CredentialWrite) -> CredentialView | None:
    with open_action_store(context) as store:
        current = store.credential(context, credential_id)
        if current is None:
            return None
        if current.kind != write.kind:
            raise CredentialProblem("A login cannot become a card or the other way round; add a new one.")
        handle = write.handle.strip().lower()
        if not valid_handle_name(handle):
            raise CredentialProblem("A handle is lowercase letters, digits and dashes, starting with a letter or digit (at most 40).")
        saved = json.loads(secret_cipher().decrypt(current.secret_ciphertext))
        if write.kind == "login" and not write.site:
            write = write.model_copy(update={"site": current.site})
        parts, hint, site = _secret_parts(write, saved)
        record = current.model_copy(update={
            "handle": handle, "site": site, "hint": hint, "secret_ciphertext": secret_cipher().encrypt(json.dumps(parts)),
            "updated_by": context.user_id, "updated_at": utc_now(),
        })
        try:
            store.save_credential(context, record)
        except ValueError:
            raise CredentialProblem(f"There is already a saved {write.kind} called {handle}.") from None
        _audit(store, context, "action_browser.credential_updated", record)
    return view(record)


def delete_credential(context: TenantContext, credential_id: str) -> bool:
    with open_action_store(context) as store:
        current = store.credential(context, credential_id)
        if current is None:
            return False
        store.delete_credential(context, credential_id)
        _audit(store, context, "action_browser.credential_deleted", current)
    return True


def scope_for(context: TenantContext, sites: list[str], card_names: list[str], *, any_card: bool) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """The logins saved for one of ``sites``, the cards named (or the only card when ``any_card``), and their hints."""
    from .handles import host_in_sites

    with open_action_store(context) as store:
        records = store.credentials(context)
    logins = {r.handle: r.site for r in records if r.kind == "login" and r.site and host_in_sites(r.site, sites)}
    cards_saved = [r for r in records if r.kind == "card"]
    cards = [r.handle for r in cards_saved if r.handle in card_names]
    if not cards and any_card and len(cards_saved) == 1:
        cards = [cards_saved[0].handle]
    hints = {f"{r.kind}:{r.handle}": r.hint for r in records if (r.kind == "login" and r.handle in logins) or (r.kind == "card" and r.handle in cards)}
    return {k: v for k, v in logins.items() if v}, cards, hints


def hints_for(context: TenantContext) -> dict[str, str]:
    with open_action_store(context) as store:
        return {f"{r.kind}:{r.handle}": r.hint for r in store.credentials(context)}


def resolve_secret(context: TenantContext, handle: Handle) -> str | None:
    """The value ``handle`` stands for, decrypted now, or None when it is not saved any more."""
    with open_action_store(context) as store:
        record = store.credential_by_handle(context, handle.kind, handle.name)
    if record is None:
        return None
    parts = json.loads(secret_cipher().decrypt(record.secret_ciphertext))
    if handle.kind == "login":
        return parts.get(handle.part)
    expiry = str(parts.get("expiry") or "")
    month, _, year = expiry.partition("/")
    derived = {"exp_month": month, "exp_year": f"20{year}" if year else ""}
    value = derived.get(handle.part) if handle.part in derived else parts.get(handle.part)
    return str(value) if value else None


__all__ = [
    "CredentialProblem", "CredentialView", "CredentialWrite", "create_credential", "delete_credential", "hints_for", "list_credentials",
    "resolve_secret", "scope_for", "update_credential",
]
