"""HTTP routes for refunds of a Business invoice's card payment (``/api/v1/business``).

* Refunds from ANUM (docs/business.md#refunds-from-anum): a member or owner asks for a refund of part or all of a
  card payment; it waits for an owner's approval with the payload hash, then refunds through Moyasar or Stripe and is
  recorded as the provider's webhook would record it (``invoice_refund_requests.py``). A request waiting for approval
  can be withdrawn by the person who asked or an owner.
* Refunds ANUM could not match (docs/business.md#refunds-anum-could-not-match): a refund the provider's verified
  webhook reported for a payment ANUM never recorded is kept for an owner to look at; an owner marks it checked
  (with a note) or opens it again.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..punctuation import plain_dashes
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from .api_invoices import InvoiceView, invoice_view
from .common import Page, manage, page_bounds, read, write
from .invoice_models import RefundRequest, RefundReview, RefundReviewStatus
from .invoice_refund_requests import cancel_request, request_refund
from .models import Money
from .service import audit, http_error
from .store import Query, open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])


class RefundAsk(BaseModel):
    amount: Money = Field(gt=0)
    reason: str = Field(min_length=3, max_length=300)


class RefundAnswer(BaseModel):
    request: RefundRequest
    invoice: InvoiceView


@router.post("/invoices/{invoice_id}/payments/{payment_id}/refund-requests", response_model=RefundAnswer, status_code=status.HTTP_201_CREATED)
def ask_for_refund(
    invoice_id: str, payment_id: str, body: RefundAsk, response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$"),
    context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> RefundAnswer:
    """Ask for a refund of a card payment. Nothing is refunded until an owner approves it."""
    write(context)
    reason = plain_dashes(" ".join(body.reason.split()))[:300]
    if len(reason) < 3:
        raise http_error(422, "Say why the money goes back (at least 3 characters).")
    request, replay = request_refund(context, repository, invoice_id, payment_id, amount=Decimal(body.amount), reason=reason, idempotency_key=idempotency_key)
    if replay:
        response.status_code = status.HTTP_200_OK
    with open_business_store(context) as store:
        invoice = store.get("invoices", invoice_id)
        if invoice is None:
            raise http_error(404, "Invoice not found")
        view = invoice_view(store, repository, context, invoice)
    return RefundAnswer(request=request, invoice=view)


@router.post("/invoices/{invoice_id}/refund-requests/{request_id}/cancel", response_model=InvoiceView)
def withdraw_refund(invoice_id: str, request_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    write(context)
    cancel_request(context, repository, invoice_id, request_id)
    with open_business_store(context) as store:
        invoice = store.get("invoices", invoice_id)
        if invoice is None:
            raise http_error(404, "Invoice not found")
        return invoice_view(store, repository, context, invoice)


# -- refunds ANUM could not match --------------------------------------------------------------------


class ReviewNote(BaseModel):
    note: str = Field(default="", max_length=300)


@router.get("/refund-reviews", response_model=Page[RefundReview])
def list_refund_reviews(state: Literal["open", "checked", "all"] = "open", limit: int = Q(default=25, ge=1, le=100), offset: int = Q(default=0, ge=0),
                        context: TenantContext = Depends(tenant_context)) -> Page[RefundReview]:
    """Refunds the provider reported for card payments ANUM has no record of, newest first."""
    read(context)
    limit, offset = page_bounds(limit, offset)
    where = {} if state == "all" else {"status": RefundReviewStatus(state)}
    with open_business_store(context) as store:
        items, total = store.find("refund_reviews", Query(where=where, order_by="created_at", descending=True, limit=limit, offset=offset))
    return Page(items=items, total=total, limit=limit, offset=offset)


def _decide(context: TenantContext, repository: AnumRepository, review_id: str, checked: bool, note: str) -> RefundReview:
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        review: RefundReview | None = store.get("refund_reviews", review_id, for_update=True)
        if review is None:
            raise http_error(404, "Refund to check not found")
        update: dict[str, object] = ({"status": RefundReviewStatus.CHECKED, "checked_by": context.user_id, "checked_at": now,
                                      "note": plain_dashes(" ".join(note.split()))[:300]} if checked else
                                     {"status": RefundReviewStatus.OPEN, "checked_by": None, "checked_at": None})
        review = review.model_copy(update={**update, "updated_at": now})
        store.save("refund_reviews", review)
    audit(repository, context, "business.refund_review.checked" if checked else "business.refund_review.reopened", review_id)
    return review


@router.post("/refund-reviews/{review_id}/check", response_model=RefundReview)
def check_refund_review(review_id: str, body: ReviewNote, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> RefundReview:
    """An owner looked at it (in the provider's dashboard, with the customer) and says what they found."""
    return _decide(context, repository, review_id, True, body.note)


@router.post("/refund-reviews/{review_id}/reopen", response_model=RefundReview)
def reopen_refund_review(review_id: str, context: TenantContext = Depends(tenant_context),
                         repository: AnumRepository = Depends(repository_context)) -> RefundReview:
    return _decide(context, repository, review_id, False, "")
