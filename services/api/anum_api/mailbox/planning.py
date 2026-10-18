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
from .mime import valid_address

MAIL_TOOLS = frozenset({SUMMARIZE_TOOL, CLASSIFY_TOOL, PAGE_TOOL, DRAFT_TOOL, REQUEST_TOOL})
# A request to the assistant is short; anything longer is cut before any pattern runs, and the
# patterns below never put two overlapping repetitions next to each other (no backtracking).
MAX_PROMPT = 2_000
_EDGE = "<>()[]{},;:!?\"'`."
_COUNT_BEFORE = frozenset({"last", "latest", "newest", "recent", "top", "first"})
_COUNT_AFTER = frozenset({"latest", "newest", "recent", "last"})
_MAIL_WORDS = frozenset({"email", "emails", "e-mail", "e-mails", "messages", "mails"})
_ADDRESS_GAPS = re.compile(r"[\s,;:<>()\[\]{}\"'`]+")
_INSTRUCTION_WORDS = re.compile(r"\b(?:saying|that says|to say|and say|telling them|telling her|telling him)\b", re.IGNORECASE)
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
    number = _count(text)
    count = number if number is not None else (10 if many else 1)
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


def _words(text: str) -> list[str]:
    return [word.strip(_EDGE) for word in text.split()]


def _count(text: str) -> int | None:
    """"last 5", "latest 12", "5 emails", "3 latest messages": the number of messages asked for."""
    words = _words(text)
    for index, word in enumerate(words):
        if not (word.isdigit() and len(word) <= 2):
            continue
        if index > 0 and words[index - 1] in _COUNT_BEFORE:
            return int(word)
        following = words[index + 1 : index + 3]
        if following and (following[0] in _MAIL_WORDS or (following[0] in _COUNT_AFTER and following[1:2] and following[1] in _MAIL_WORDS)):
            return int(word)
    return None


def addresses(text: str, limit: int = 10) -> list[str]:
    """The e-mail addresses the person typed, in order, each once (word by word, no search)."""
    found: list[str] = []
    for piece in _ADDRESS_GAPS.split(text[:MAX_PROMPT]):
        word = piece.strip(".!?")
        if "@" in word and valid_address(word) and word not in found:
            found.append(word)
            if len(found) == limit:
                break
    return found


def _instructions(prompt: str) -> str | None:
    """What follows "saying ...", a colon or "يقول": the person's own words for the reply."""
    prompt = prompt[:MAX_PROMPT]
    starts: list[int] = []
    phrase = _INSTRUCTION_WORDS.search(prompt)
    if phrase:
        starts.append(phrase.end())
    colon = prompt.find(":")
    if colon >= 0 and prompt[colon + 1 :].strip():
        starts.append(colon + 1)
    arabic = prompt.find("يقول")
    if arabic >= 0 and prompt[arabic + 4 : arabic + 5].isspace():
        starts.append(arabic + 4)
    if not starts:
        return None
    return prompt[min(starts) :].strip()[:1000] or None


def plan_mail_call(prompt: str, available_tools: Collection[str]) -> ToolCall | None:
    """The Email Center tool for this request, from the person's words only; None otherwise."""
    if not MAIL_TOOLS <= set(available_tools):
        return None
    text = " ".join(prompt[:MAX_PROMPT].split())
    if _has(_FORWARD, text):
        recipients = addresses(text)
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
