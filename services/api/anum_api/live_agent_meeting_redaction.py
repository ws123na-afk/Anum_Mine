"""Secret redaction for live meeting text (F07).

Applied to every message before it is stored, so the transcript, the event log,
exports and prompts built from history never carry a credential. Pure and
deterministic: no network, no model. It is a safety net, not a licence to paste secrets.
"""
from __future__ import annotations

import re

MARK = "[redacted]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile("-" * 5 + "BEGIN [A-Z ]*PRIVATE KEY" + "-" * 5 + ".*?(?:" + "-" * 5 + "END [A-Z ]*PRIVATE KEY" + "-" * 5 + r"|\Z)", re.S),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|ant|proj)?[-_]?[A-Za-z0-9]{16,}"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@"),
)
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:api[_ -]?key|secret|password|passwd|token|credential|authorization)s?\s*[:=]\s*)([^\s,;\"']{6,})"
)


def redact_secrets(value: str) -> tuple[str, bool]:
    """Return the text with credentials replaced, and whether anything changed."""
    text = value
    for pattern in _PATTERNS:
        if pattern.pattern.endswith("@"):
            text = pattern.sub(lambda m: m.group(0).split("://")[0] + "://" + MARK + "@", text)
        else:
            text = pattern.sub(MARK, text)
    text = _ASSIGNMENT.sub(lambda m: m.group(1) + MARK, text)
    return text, text != value
