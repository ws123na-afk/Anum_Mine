"""Business: the internal sales MVP (docs/business.md).

A catalog of products and service packages with priced versions; accounts, contacts and
leads with a state machine, a deterministic six-criterion score, evidence kept apart from
inferences, duplicate detection, do-not-contact and the tenant's opt-out list; a lead
research skill over the existing ``web_search`` tool within daily caps; the prospecting
pipeline (discover and profile runs that are estimated, confirmed and kept within budget,
over ``web_search`` and the read-only ``web.fetch_page`` tool, collecting only the signals
an owner confirmed); an opportunity pipeline with assumed values; versioned proposals whose
acceptance is an owner approval bound to the exact version; projects and milestones from an
accepted proposal; outbound drafts and the outreach outbox (a deterministic gate, an
approval bound to the payload hash for every message, sending off by default, quotas,
stop-on-reply and a bounce kill switch); the owner's KPI summary; change orders that amend
an accepted contract only with an owner's approval; a client usage and cost ledger with a
profitability view per project (revenue and cash collected kept apart); support tickets
with a working-hours SLA whose replies go through the inboxes' own approval paths; and
invoices from the contract, the ledger and the catalog whose sending is an owner approval,
with manual payments, a printable copy and a customer portal (docs/business-portal.md).
"""

from fastapi import APIRouter

from .api import router as _records_router
from .api_changes import router as _changes_router
from .api_deals import router as _deals_router
from .api_invoices import router as _invoices_router
from .api_outreach import router as _outreach_router
from .api_pipeline import router as _pipeline_router
from .api_portal_admin import router as _portal_admin_router
from .api_support import router as _support_router
from .portal import router as _portal_router
from .tools import register_business_tools

router = APIRouter()
router.include_router(_records_router)
router.include_router(_deals_router)
router.include_router(_pipeline_router)
router.include_router(_outreach_router)
router.include_router(_changes_router)
router.include_router(_invoices_router)
router.include_router(_portal_admin_router)
router.include_router(_portal_router)
router.include_router(_support_router)

__all__ = ["register_business_tools", "router"]
