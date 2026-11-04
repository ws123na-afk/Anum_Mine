"""Optional AI drafts of a daily or weekly summary (docs/attendance.md#ai-drafts).

Off by default: the person turns it on under Attendance, Settings. The draft is written only
from the person's own daily report entries (tasks done and pending, issues, notes, project, time
they noted). Arrival and departure times, sites and any location data are never sent to the
model, so a draft cannot invent work from where someone was. With no entries the model is not
called at all.

Every call goes through the workspace's budgeted model gateway (checked against the monthly
budget before, metered after). The entries reach the model inside a labelled untrusted block,
so text a person pasted into a report is data, never instructions. The draft is returned to
the person to edit; nothing is saved by this module.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..current_time import language_of, time_line_async, with_time_line
from ..model_gateway import ModelGateway, ModelRoute, route_gateway
from ..prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from ..punctuation import plain_dashes
from ..schemas import TenantContext
from .models import DailyReport

_gateway: ModelGateway | None = None
MAX_ENTRY_CHARS = 12_000

INSTRUCTIONS = (
    "You write a short work summary for one person from their own daily report entries. "
    "Use only what the entries say. Never add tasks, places, hours, people or results that are not written there. "
    "Do not guess where the person was or what they did from times or places. "
    "If the entries are thin, the summary is short. Write plain sentences, no headings, at most 8 short lines. "
    "Write in the language the request asks for."
)


def default_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        from ..model_gateway import build_default_model_gateway

        _gateway = build_default_model_gateway()
    return _gateway


def workspace_gateway(context: TenantContext) -> ModelGateway:
    from ..onboarding import budgeted_model_gateway

    return budgeted_model_gateway(context, default_gateway())


def model_connected(context: TenantContext) -> bool:
    return getattr(workspace_gateway(context), "provider", "mock") != "mock"


def entries_text(reports: Sequence[DailyReport]) -> str:
    """The person's own entries as plain lines, oldest first. No times, no sites, no places."""
    lines: list[str] = []
    for report in sorted(reports, key=lambda r: r.day):
        lines.append(f"Day {report.day.isoformat()}" + (f", project {report.project}" if report.project else ""))
        lines.extend(f"Done: {task}" for task in report.tasks_done)
        lines.extend(f"Pending: {task}" for task in report.tasks_pending)
        if report.issues:
            lines.append(f"Issues: {report.issues}")
        if report.time_spent_minutes:
            lines.append(f"Time noted: {report.time_spent_minutes} minutes")
        if report.notes:
            lines.append(f"Notes: {report.notes}")
        if report.follow_up_on:
            lines.append(f"Follow up on {report.follow_up_on.isoformat()}")
    return "\n".join(lines)


def has_entries(reports: Sequence[DailyReport]) -> bool:
    return any(r.tasks_done or r.tasks_pending or r.issues or r.notes for r in reports)


async def draft(context: TenantContext, reports: Sequence[DailyReport], *, period: str, language: str) -> str:
    gateway = route_gateway(workspace_gateway(context), ModelRoute.FAST)
    block = label_untrusted(entries_text(reports), source=Provenance.TOOL_OUTPUT, origin="attendance daily reports",
                            max_chars=MAX_ENTRY_CHARS)
    full = with_untrusted_rules(INSTRUCTIONS, block)
    ask = "Arabic" if language == "ar" else "English"
    prompt = f"Write the {period} summary in {ask} from the entries above."
    prompt = with_time_line(prompt, await time_line_async(context, language_of(prompt)))
    generate = getattr(gateway, "generate_with_instructions", None)
    response = await generate(full, prompt) if callable(generate) else await gateway.generate_text(f"{full}\n\n{prompt}")
    return plain_dashes(response.text.strip())[:4000]
