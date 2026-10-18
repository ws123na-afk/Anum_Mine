"""Plan Brevo tool calls from a task's own words (docs/brevo.md#planner).

``plan_call`` turns a free-text request into one Brevo tool call, deterministically and
from the user's prompt only (threat model G5: never retrieved text or model output):

* "send the winter newsletter to the customers list" plans ``brevo.draft_campaign``: a
  draft in ANUM with the campaign name, subject, preview text, a content outline, the
  contact lists by name and the requested schedule. Nothing is created in Brevo and
  nothing is sent.
* "how did campaign 101 do", "show the newsletter reports" and "how many subscribers are
  on our lists" plan the read-only ``brevo.campaign_report``, ``brevo.list_campaigns``
  and ``brevo.list_lists``.

The planner never plans ``brevo.send_campaign``: sending is asked for on the Email
campaigns page and runs only after an owner approves the exact draft with the payload hash
they were shown. Every planned call still goes through the runtime's ``ToolPolicy``.

``refusal`` is the planner's check that the workspace has a working Brevo connection; it
reads the store in its own thread and short unit of work, never on the event loop.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from ..agent_tools import ToolCall
from ..schemas import Task, TenantContext, utc_now
from .models import MAX_LISTS, ConnectionStatus
from .service import CAMPAIGNS_TOOL, DRAFT_TOOL, LISTS_TOOL, REPORT_TOOL, SEND_TOOL, load_connection

SKILL_ID = "anum.brevo"
# The tools a plan may name. ``brevo.send_campaign`` is deliberately not one of them.
PLANNED_TOOLS = frozenset({DRAFT_TOOL, LISTS_TOOL, CAMPAIGNS_TOOL, REPORT_TOOL})
RESPOND_TOOL = "anum.respond"
AS_SOON_AS_APPROVED = "as soon as approved"
MAX_OUTLINE = 12

_BREVO_WORDS = re.compile(
    r"\b(brevo|newsletters?|mailings?|mailshots?|subscribers?|email\s+campaigns?|contact\s+lists?)\b|نشرة|النشرة|المشتركين",
    re.IGNORECASE,
)
_REPORT_WORDS = re.compile(
    r"\b(reports?|stats|statistics|results|performance|perform(ed)?|open\s+rates?|opens|clicks?|click\s+rates?|bounces?"
    r"|unsubscribes?|how\s+did)\b|تقرير|نتائج|أداء",
    re.IGNORECASE,
)
_DRAFT_WORDS = re.compile(
    r"\b(draft|write|prepare|compose|create|send|schedule)\b|أرسل|ارسل|اكتب|جهز|جدول",
    re.IGNORECASE,
)
_LIST_WORDS = re.compile(r"\b(lists?|subscribers?|contacts?|audiences?|how\s+many)\b|قوائم|قائمة|المشتركين", re.IGNORECASE)
_CAMPAIGN_WORDS = re.compile(r"\b(campaigns?|newsletters?|sent|recent|last)\b|حملات", re.IGNORECASE)
_CAMPAIGN_ID = re.compile(r"\b(?:campaign|newsletter)\s*(?:#|no\.?|number|id)?\s*(\d{1,9})\b|#(\d{1,9})\b", re.IGNORECASE)
_DRAFT_ID = re.compile(r"\bbrevodraft_[0-9a-f]{8,32}\b")
_TOPIC = re.compile(
    r"\b(?:the|a|an|our|my|this|next)\s+([a-z0-9][\w' -]{0,59}?)\s+(?:email\s+)?(?:newsletter|campaign|email|mailing|mailshot)\b",
    re.IGNORECASE,
)
_TO_LISTS = re.compile(
    r"\bto\s+(?:the\s+|our\s+|my\s+|all\s+)?([\w' ,&-]{1,120}?)\s+(?:contact\s+|mailing\s+)?lists?\b", re.IGNORECASE
)
_NAMED_LIST = re.compile(r"\blists?\s+(?:named|called)\s+[\"']?([\w' &-]{1,60}?)[\"']?(?:[.,;!?]|$|\s+(?:and|for|on|at|tomorrow)\b)",
                         re.IGNORECASE)
_ISO_WHEN = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?\b")
_RELATIVE_WHEN = re.compile(r"\b(today|tomorrow)(?:\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?\b", re.IGNORECASE)
_NOT_TOPIC = frozenset({"email", "new", "brevo", "next", "latest"})


def wants_brevo(prompt: str) -> bool:
    return bool(_BREVO_WORDS.search(prompt) or _DRAFT_ID.search(prompt))


def plan_call(prompt: str, *, now: datetime | None = None) -> ToolCall | None:
    """The one Brevo call ``prompt`` asks for, or None when it is not about Brevo."""
    text = " ".join(prompt.split())
    if not wants_brevo(text):
        return None
    campaign = _CAMPAIGN_ID.search(text)
    if _REPORT_WORDS.search(text):
        if campaign:
            return ToolCall(name=REPORT_TOOL, arguments={"campaign_id": int(campaign.group(1) or campaign.group(2))})
        return ToolCall(name=CAMPAIGNS_TOOL, arguments={"status": "sent", "limit": 10})
    if _DRAFT_ID.search(text):
        # Sending an existing draft is never planned: an owner approves it on the page.
        return _respond(
            "Sending a Brevo campaign needs an owner's approval of the exact draft. Open Marketing, Email campaigns, "
            "Drafts, choose the draft and press Ask to send; an owner then approves it in Approvals."
        )
    if _DRAFT_WORDS.search(text):
        return _draft_call(text, now or utc_now())
    if _LIST_WORDS.search(text):
        return ToolCall(name=LISTS_TOOL, arguments={"limit": 50})
    if _CAMPAIGN_WORDS.search(text) or campaign is None:
        return ToolCall(name=CAMPAIGNS_TOOL, arguments={"limit": 10})
    return ToolCall(name=REPORT_TOOL, arguments={"campaign_id": int(campaign.group(1) or campaign.group(2))})


def _respond(text: str) -> ToolCall:
    return ToolCall(name=RESPOND_TOOL, arguments={"text": text})


def list_names(text: str) -> list[str]:
    """Contact lists named in the prompt ("to the customers and VIP lists")."""
    found: list[str] = []
    for pattern in (_TO_LISTS, _NAMED_LIST):
        for match in pattern.finditer(text):
            for part in re.split(r",|&|\band\b", match.group(1), flags=re.IGNORECASE):
                name = " ".join(part.split()).strip(" '\"")
                if name and name.lower() not in {"the", "our", "my", "all"} and name.lower() not in {item.lower() for item in found}:
                    found.append(name[:100])
    return found[:MAX_LISTS]


def topic(text: str) -> str:
    """What the campaign is about ("winter" in "the winter newsletter"), or ""."""
    match = _TOPIC.search(text)
    if match is None:
        return ""
    words = [word for word in match.group(1).split() if word.lower() not in _NOT_TOPIC]
    return " ".join(words)[:60]


def schedule(text: str, now: datetime) -> str:
    """The requested send time as an ISO time in UTC, or "as soon as approved"."""
    iso = _ISO_WHEN.search(text)
    when: datetime | None = None
    if iso:
        try:
            day = datetime.fromisoformat(iso.group(1))
            when = day.replace(hour=int(iso.group(2) or 9), minute=int(iso.group(3) or 0), tzinfo=UTC)
        except ValueError:
            when = None
    else:
        relative = _RELATIVE_WHEN.search(text)
        if relative and (relative.group(1).lower() == "tomorrow" or relative.group(2)):
            hour = int(relative.group(2) or 9)
            meridiem = (relative.group(4) or "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            if meridiem == "am" and hour == 12:
                hour = 0
            if hour <= 23:
                base = now.astimezone(UTC) + timedelta(days=1 if relative.group(1).lower() == "tomorrow" else 0)
                when = base.replace(hour=hour, minute=int(relative.group(3) or 0), second=0, microsecond=0)
    # A time in the past (or too close to approve in time) is not a schedule.
    if when is None or when <= now.astimezone(UTC) + timedelta(minutes=10):
        return AS_SOON_AS_APPROVED
    return when.isoformat()


def _draft_call(text: str, now: datetime) -> ToolCall:
    lists = list_names(text)
    if not lists:
        return _respond(
            "Which Brevo contact list should the campaign go to? Name it in the request, for example "
            "\"draft the winter newsletter to the customers list\"."
        )
    subject_topic = topic(text)
    title = f"{subject_topic[:1].upper()}{subject_topic[1:]} newsletter" if subject_topic else "Newsletter"
    about = subject_topic or "this month's"
    arguments = {
        "name": title[:200],
        "subject": (f"Our {subject_topic} newsletter" if subject_topic else "Our latest news")[:200],
        "preview_text": f"The {about} news and offers from our team"[:200],
        "outline": [
            f"Opening: a short welcome to the {about} newsletter",
            "Main story: the most important news for these readers",
            "Offer: what readers can do now, with one clear button",
            "Closing: how to reach us and how to unsubscribe",
        ][:MAX_OUTLINE],
        "list_names": lists,
        "schedule": schedule(text, now),
    }
    return ToolCall(name=DRAFT_TOOL, arguments=arguments)


def _context(task: Task) -> TenantContext:
    return TenantContext(tenant_id=task.tenant_id, workspace_id=task.workspace_id, user_id=task.created_by or "", roles=[])


async def refusal(task: Task, call: ToolCall) -> ToolCall | None:
    """An ``anum.respond`` that says what to fix when ``call`` is a Brevo tool and the
    workspace has no working Brevo connection; None when it may go ahead to the runtime."""
    if call.name not in PLANNED_TOOLS and call.name != SEND_TOOL:
        return None
    connection = await asyncio.to_thread(load_connection, _context(task))
    if connection is None:
        return _respond(
            "Brevo is not connected for this workspace, so nothing was drafted or sent. An owner opens Marketing, "
            "Email campaigns, Connection and pastes the Brevo API key (it starts with xkeysib-), then asks again."
        )
    if connection.status == ConnectionStatus.NEEDS_KEY:
        return _respond(
            "Brevo refused the saved API key, so nothing was drafted or sent. An owner saves a new key under "
            "Marketing, Email campaigns, Connection, then asks again."
        )
    return None
