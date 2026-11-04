"""The link point between vault documents and finance transactions (docs/documents.md#links).

A document can point at a vehicle, person, business or purchase today (a reference the person
types; those modules will resolve them when they exist). A link to a finance transaction belongs to
the ANUM Finance ledger (PR #364 and the shared ``finance_links`` foundation): the ledger decides
whether a transaction exists, which chain root it has and whether the link may be confirmed, and
it never posts money because a receipt was added.

Until that foundation is merged :func:`finance_links` returns :class:`NotConnected`, which refuses
with 409 and a plain reason. The foundation PR replaces it with an adapter that implements
:class:`FinanceLinkPort` (propose a link, answer ``proposed``; the owner confirms in Finance).
"""

from __future__ import annotations

from typing import Literal, Protocol

from fastapi import HTTPException

from ..schemas import TenantContext

NOT_CONNECTED = (
    "Linking a document to a finance transaction is not connected yet: it arrives with the finance ledger links. "
    "Link it to a vehicle, person, business or purchase for now."
)


class FinanceLinkPort(Protocol):
    def propose(self, context: TenantContext, document_id: str, transaction_ref: str) -> Literal["proposed", "confirmed"]:
        """Ask the ledger to attach a document as evidence of a transaction. Never posts money."""
        ...

    def withdraw(self, context: TenantContext, document_id: str, transaction_ref: str) -> None:
        """The document's link was removed in the vault: the ledger's evidence link is voided too."""
        ...


class NotConnected:
    def propose(self, context: TenantContext, document_id: str, transaction_ref: str) -> Literal["proposed", "confirmed"]:
        raise HTTPException(409, NOT_CONNECTED)

    def withdraw(self, context: TenantContext, document_id: str, transaction_ref: str) -> None:
        return None  # nothing was ever linked on the ledger side


_port: FinanceLinkPort = NotConnected()


def finance_links() -> FinanceLinkPort:
    return _port


def set_finance_links(port: FinanceLinkPort) -> None:
    """Called once by the finance links foundation at import time (and by tests)."""
    global _port
    _port = port


__all__ = ["NOT_CONNECTED", "FinanceLinkPort", "NotConnected", "finance_links", "set_finance_links"]
