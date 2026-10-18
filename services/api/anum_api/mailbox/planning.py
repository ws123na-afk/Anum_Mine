"""The assistant's mail tools, chosen from the person's own words (docs/email.md#assistant).

``plan_mail_call`` turns a request such as "summarise my latest email from Sara" or "forward
the last email to omar@example.com" into one tool call. Deterministic, no model and no mail:
the tool, the selector (folder, how many, sender, unread) and any recipients or instructions
come only from the prompt (threat model G5), so nothing in a message can choose a tool or an
address. Forward, archive, export and delete are planned as ``request_mail_action``, which only
asks for the approval of exactly the picked messages; sending a written draft stays on the
Email page. ``None`` means the request is not one of these (the inbox search answers it).
"""

from __future__ import annotations

import re
from collections.abc import Collection

from ..agent_tools import ToolCall
from .ai import CLASSIFY_TOOL, DRAFT_TOOL, MAX_SELECT, PAGE_TOOL, REQUEST_TOOL, SUMMARIZE_TOOL

MAIL_TOOLS = frozenset({SUMMARIZE_TOOL, CLASSIFY_TOOL, PAGE_TOOL, DRAFT_TOOL, REQUEST_TOOL})
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SEND_ONLY = (
    "Sending starts from the message on the Email page: open the draft, check it and press Send. "
    "Every send waits for an owner to approve the exact message."
)

_SUMMARISE = r"\b(summari[sz]e|summary|sum\s+up|recap|tl;?dr)\b|لخص|لخّص|ملخص"
_LABEL = r"\b(label|labels|classify|categori[sz]e|prioriti[sz]e|triage|sort)\b|صنف|صنّف|رتب"
_DRAFT = r"\b(draft|write|prepare)\b.*\brepl(y|ies)\b|\brepl(y|ies)\s+to\b|\banswer\b|اكتب\s+رد|رد\s+على|جاوب"
_FORWARD = r"\b(forward|fwd)\b|أعد\s+توجيه|حوّل|حول\s+(?:البريد|الرسالة|الإيميل)"
_ARCHIVE = r"\barchive\b|أرشف|ارشف"
_EXPORT = r"\b(export|download)\b|صدّر|صدر|نزّل"
_DELETE = r"\b(delete|erase|purge|empty)\b|احذف|امسح"
_SEND = r"\bsend\b|أرسل|ارسل"


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.IGNORECASE))


def _selector(prompt: str, *, many: bool) -> dict[str, object]:
    text = prompt.lower()
    role = "inbox"
    for word, value in (("junk", "junk"), ("spam", "junk"), ("trash", "trash"), ("bin", "trash"), ("sent", "sent"), ("المهملات", "trash"), ("المرسلة", "sent")):
        if re.search(rf"\b(in|from)\s+(the\s+|my\s+)?{word}\b", text) or (word in {"المهملات", "المرسلة"} and word in text):
            role = value
            break
    if re.search(r"\bin\s+(the\s+|my\s+)?archive\b", text):
        role = "archive"
    count = 1
    number = re.search(r"\b(?:last|latest|newest|recent|top|first)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:latest|newest|recent|last)?\s*(?:e-?mails?|messages|mails)\b", text)
    if number:
        count = int(number.group(1) or number.group(2))
    elif many:
        count = 10
    selector: dict[str, object] = {"folder_role": role, "count": max(1, min(count, MAX_SELECT))}
    if re.search(r"\bunread\b|غير\s+المقروء", text):
        selector["unread"] = True
    sender = re.search(r"\bfrom\s+([^\s,.;:?!]+(?:\s+[A-Z][\w'-]+)?)", prompt)
    if sender and sender.group(1).lower() not in {"the", "my", "junk", "spam", "trash", "sent", "inbox", "archive"}:
        selector["from"] = sender.group(1).strip()[:120]
    else:
        arabic = re.search(r"من\s+([^\s،,.؟?]+)", prompt)
        if arabic and arabic.group(1) not in {"البريد", "الوارد", "المهملات"}:
            selector["from"] = arabic.group(1)[:120]
    return selector


def _instructions(prompt: str) -> str | None:
    found = re.search(r"\b(?:saying|that says|to say|and say|telling (?:them|her|him))\b\s*(.+)$|:\s*(.+)$|يقول\s+(.+)$", prompt, re.IGNORECASE | re.DOTALL)
    if not found:
        return None
    text = next((group for group in found.groups() if group), "").strip()
    return text[:1000] or None


def plan_mail_call(prompt: str, available_tools: Collection[str]) -> ToolCall | None:
    """The Email Center tool for this request, from the person's words only; None otherwise."""
    if not MAIL_TOOLS <= set(available_tools):
        return None
    text = " ".join(prompt.split())
    if _has(_FORWARD, text):
        recipients = list(dict.fromkeys(ADDRESS.findall(text)))[:10]
        return ToolCall(name=REQUEST_TOOL, arguments={"action": "forward", "selector": _selector(text, many=False), "to": recipients})
    for pattern, action in ((_EXPORT, "export"), (_DELETE, "delete"), (_ARCHIVE, "archive")):
        if _has(pattern, text):
            return ToolCall(name=REQUEST_TOOL, arguments={"action": action, "selector": _selector(text, many=False)})
    if _has(_SEND, text) and not _has(r"\b(draft|write|prepare)\b|اكتب", text):
        return ToolCall(name="anum.respond", arguments={"text": SEND_ONLY})
    if _has(_DRAFT, text):
        args: dict[str, object] = {"selector": _selector(text, many=False), "kind": "reply_all" if _has(r"\breply\s+all\b|\beveryone\b|الجميع", text) else "reply"}
        instructions = _instructions(text)
        if instructions:
            args["instructions"] = instructions
        return ToolCall(name=DRAFT_TOOL, arguments=args)
    if _has(_SUMMARISE, text):
        return ToolCall(name=SUMMARIZE_TOOL, arguments={"selector": _selector(text, many=False), "thread": _has(r"\b(thread|conversation)\b|محادثة|المحادثة", text)})
    if _has(_LABEL, text):
        return ToolCall(name=PAGE_TOOL, arguments={"selector": _selector(text, many=True)})
    return None
