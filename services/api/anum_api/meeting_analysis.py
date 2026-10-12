"""Summary, decisions, action items and a follow-up email from a transcript (docs/meetings.md#processing).

One model call through the workspace's *budgeted* gateway (``onboarding.budgeted_model_gateway``):
the monthly model budget is checked before it and the usage recorded after it. The
transcript goes into the prompt as a provenance-labelled untrusted block
(``Provenance.MEETING_TRANSCRIPT``), after the rules that say text inside it is data, never
instructions. The model returns JSON only; nothing it says can create a task, send an
email or approve anything. Those stay explicit user actions in the API.

The mock provider has no model to summarise with, so :func:`analysis_unavailable` says so
and the meeting waits for a model instead of receiving invented text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .meeting_transcripts import ParsedSegment
from .model_gateway import ModelRoute, route_gateway
from .prompt_provenance import Provenance, label_untrusted, with_untrusted_rules

MAX_SUMMARY_CHARS = 4000
MAX_DECISIONS = 20
MAX_ACTION_ITEMS = 30
MAX_ITEM_CHARS = 500
MAX_OWNER_CHARS = 120
MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 6000

INSTRUCTIONS = """You turn a meeting transcript into notes for the person who imported it.
Answer with one JSON object and nothing else, in this exact shape:
{"summary": "...", "decisions": ["..."], "action_items": [{"text": "...", "owner": "name or null", "due_date": "YYYY-MM-DD or null"}], "follow_up_email": {"subject": "...", "body": "..."}}
Rules:
- Write every value in {language_name}.
- summary: 3 to 8 sentences on what was discussed and concluded.
- decisions: only decisions the participants actually made; [] if none.
- action_items: concrete next steps that someone agreed to do. owner is the person's name as said in the transcript, or null when nobody took it. due_date only when a date was stated or is unambiguous from the meeting date ({meeting_date}); otherwise null. [] if none.
- follow_up_email: a short, polite recap to send to the participants (greeting, the decisions, the action items with owners and dates, closing) without a signature name.
- Do not invent names, numbers, dates or commitments that are not in the transcript.
"""


@dataclass
class AnalysisResult:
    summary: str
    decisions: list[str] = field(default_factory=list)
    action_items: list[dict[str, Any]] = field(default_factory=list)
    email_subject: str = ""
    email_body: str = ""


class AnalysisParseError(ValueError):
    """The model's answer was not the JSON object asked for."""


def _clock(ms: int | None) -> str:
    if ms is None:
        return ""
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"[{hours}:{minutes:02d}:{seconds:02d}] " if hours else f"[{minutes:02d}:{seconds:02d}] "


def transcript_text(segments: list[ParsedSegment]) -> str:
    return "\n".join(
        f"{_clock(segment.start_ms)}{segment.speaker + ': ' if segment.speaker else ''}{segment.text}" for segment in segments
    )


def build_messages(
    *, title: str, language: str, meeting_date: date | None, segments: list[ParsedSegment], max_chars: int
) -> tuple[list[dict[str, str]], bool, int]:
    """Chat messages for the analysis call, whether the transcript was cut, and its length."""
    text = transcript_text(segments)
    truncated = len(text) > max_chars
    block = label_untrusted(text, source=Provenance.MEETING_TRANSCRIPT, origin="meeting", max_chars=max_chars)
    instructions = INSTRUCTIONS.replace("{language_name}", "Arabic" if language == "ar" else "English").replace(
        "{meeting_date}", meeting_date.isoformat() if meeting_date else "unknown"
    )
    system = with_untrusted_rules(instructions)
    title_block = label_untrusted(title, source=Provenance.MEETING_TRANSCRIPT, origin="meeting-title", max_chars=200)
    user = f"Meeting title:\n{title_block}\n\nTranscript:\n{block}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], truncated, len(text)


def _text(value: Any, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value)).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _due(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def parse_answer(raw: str) -> AnalysisResult:
    """The model's JSON, tolerating a code fence or prose around the object, bounded."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AnalysisParseError("no JSON object in the answer")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AnalysisParseError("the answer is not valid JSON") from exc
    if not isinstance(data, dict):
        raise AnalysisParseError("the answer is not a JSON object")
    summary = _text(data.get("summary"), MAX_SUMMARY_CHARS)
    if not summary:
        raise AnalysisParseError("the answer has no summary")
    decisions = [
        item for item in (_text(value, MAX_ITEM_CHARS) for value in (data.get("decisions") or [])[:MAX_DECISIONS]) if item
    ] if isinstance(data.get("decisions"), list) else []
    actions: list[dict[str, Any]] = []
    for value in (data.get("action_items") or [])[:MAX_ACTION_ITEMS] if isinstance(data.get("action_items"), list) else []:
        if isinstance(value, str):
            value = {"text": value}
        if not isinstance(value, dict):
            continue
        item_text = _text(value.get("text"), MAX_ITEM_CHARS)
        if not item_text:
            continue
        owner = _text(value.get("owner"), MAX_OWNER_CHARS)
        actions.append(
            {"text": item_text, "owner": owner if owner and owner.lower() not in {"null", "none"} else None, "due_date": _due(value.get("due_date"))}
        )
    email = data.get("follow_up_email") if isinstance(data.get("follow_up_email"), dict) else {}
    body = str(email.get("body") or "").strip()[:MAX_BODY_CHARS] if isinstance(email.get("body"), str) else ""
    return AnalysisResult(
        summary=summary,
        decisions=decisions,
        action_items=actions,
        email_subject=_text(email.get("subject"), MAX_SUBJECT_CHARS),
        email_body=body,
    )


def analysis_unavailable(gateway: Any) -> bool:
    """True when the workspace has no real model (the mock provider)."""
    return str(getattr(gateway, "provider", "")) == "mock"


async def analyze(
    gateway: Any,
    *,
    title: str,
    language: str,
    meeting_date: date | None,
    segments: list[ParsedSegment],
    max_chars: int,
    max_tokens: int,
) -> tuple[AnalysisResult, dict[str, Any]]:
    """Run the call and parse it; returns the result and its provenance record."""
    messages, truncated, length = build_messages(
        title=title, language=language, meeting_date=meeting_date, segments=segments, max_chars=max_chars
    )
    routed = route_gateway(gateway, ModelRoute.CAPABLE)
    response = await routed.complete_chat(messages, max_tokens=max_tokens)
    result = parse_answer(response.text)
    provenance = {
        "label": "ai_generated",
        "source": str(Provenance.MEETING_TRANSCRIPT),
        "provider": response.usage.provider,
        "model": response.usage.model,
        "transcript_chars": length,
        "truncated": truncated,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result, provenance
