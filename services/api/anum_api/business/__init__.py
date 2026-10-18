"""Business: the internal sales MVP (docs/business.md).

A catalog of products and service packages with priced versions; accounts, contacts and
leads with a state machine, a deterministic six-criterion score, evidence kept apart from
inferences, duplicate detection, do-not-contact and the tenant's opt-out list; a lead
research skill over the existing ``web_search`` tool within daily caps; an opportunity
pipeline with assumed values; versioned proposals whose acceptance is an owner approval
bound to the exact version; projects and milestones from an accepted proposal; outbound
drafts only (sending is off: there is no send path); the owner's KPI summary; change orders
that amend an accepted contract only with an owner's approval; and a client usage and cost
ledger with a profitability view per project (revenue and cash collected kept apart).
"""

from fastapi import APIRouter

from .api import router as _records_router
from .api_changes import router as _changes_router
from .api_deals import router as _deals_router
from .tools import register_business_tools

router = APIRouter()
router.include_router(_records_router)
router.include_router(_deals_router)
router.include_router(_changes_router)

__all__ = ["register_business_tools", "router"]
