"""No figure is spoken unless this turn's data holds it (docs/voice.md#figures-and-brevity).

The model is told never to invent numbers, but a prompt is not a guarantee: a spoken turn once
quoted counts of tyres due, overdue inspections and critical alerts that no lookup of that
turn had returned. So the server checks the reply itself, sentence by sentence, before a word
of it is sent to the client:

* **Evidence** is everything this turn read: the labeled data blocks (the workspace snapshot,
  the tyre lookup), every tool result, the workspace counts in the rules, and the numbers the
  user spoke. Nothing from earlier turns counts on its own (a repeated figure is checked against
  this turn's data again). The snapshot and lookups ARE checked data: a sentence that retracts
  figures ("I hadn't run any lookup") is withheld when the turn has a source.
* **A sentence** with a figure that is not in the evidence is withheld. A reply whose every
  sentence was withheld is replaced by one plain "could not confirm" line.
* **Strict** applies to a question that asks for figures (tyres, fleet, tasks, approvals, alerts,
  counts, money) and to every turn in which a lookup failed or timed out. A question that is not
  about the workspace's data ("how many days in a week") is not checked.
* **Brevity.** A spoken reply is at most :data:`MAX_SPOKEN_SENTENCES` sentences.
* **A failed lookup** is named in one short line when the model did not say so itself.

The check is a net, not a parser of meaning: it sees digits (Latin or Arabic-Indic) and number
words next to the things the workspace counts. A figure the model computes from the evidence
(a sum) is withheld, which is the safe side.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_SPOKEN_SENTENCES = 3

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d[\d,٬]*(?:[.٫]\d+)?")
_BOUNDARY = re.compile(r"(?<=[.!?؟۔…])\s+")

_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "million": 1_000_000,
}
_COUNTED = r"(?:tyres?|tires?|vehicles?|trucks?|alerts?|tasks?|approvals?|inspections?|agents?|orders?|invoices?|emails?)"
_WORD_FIGURE = re.compile(
    rf"\b(?P<big>twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?)\b"
    rf"|\b(?P<small>{'|'.join(w for w, v in _WORDS.items() if v < 20)})\b(?=\W+(?:\w+\W+){{0,2}}{_COUNTED}\b)",
    re.IGNORECASE,
)

_NEEDS_FIGURES = re.compile(
    r"\b(how many|how much|count|total|number of|overdue|due|critical|alerts?|tasks?|approvals?|agents?|"
    r"inspections?|orders?|invoices?|sales|revenue|stock|cost|costs|budget|spend|emails?|unread|pending|waiting|"
    r"running|failed|figures?|numbers?|percent|average|summary|report|status)\b"
    r"|(كم|عدد|مهام|مهمة|موافقات|متأخر|تنبيه|تنبيهات|ميزانية|تكلفة|ملخص|حالة)",
    re.IGNORECASE,
)

_ACKNOWLEDGED = re.compile(
    r"couldn'?t|could not|can'?t|cannot|unable|timed out|took too long|not able|failed|"
    r"لم أستطع|لم أتمكن|تعذر|تعذّر|لا أستطيع",
    re.IGNORECASE,
)

# The model once retracted correct, snapshot-sourced figures ("I hadn't run any lookup"). Data
# blocks and lookup results of the turn ARE checked data, so such a sentence is withheld when
# the turn has a source. A failed lookup ("I couldn't check") is not a retraction.
_RETRACTION = re.compile(
    r"correct myself|need to correct|let me correct|i (?:must |have to )?(?:retract|apolog\w+)|"
    r"i hadn'?t (?:actually )?(?:run|checked|looked|done)|i had not (?:actually )?(?:run|checked|looked)|"
    r"i did not (?:actually )?(?:run|check|look)|(?:were|was|are|is) not from (?:the|your) data|"
    r"not (?:actually )?(?:from|based on) (?:the|your) (?:data|lookup)|earlier i said i had checked|"
    r"(?:earlier|previous) (?:figures?|numbers?|answers?|replies|reply) (?:was|were) (?:wrong|incorrect|not)|"
    r"أصحح|تصحيح لما قلت|لم أقم بأي (?:بحث|فحص)",
    re.IGNORECASE,
)

_LABELS = {
    "en": {
        "tyre_fleet_summary": "the fleet summary", "tyres_due": "the tyres due", "purchase_plan": "the purchase plan",
        "tyre_alerts": "the tyre alerts", "workspace_tasks": "the tasks", "workspace_approvals": "the approvals",
        "workspace_agents": "the agents", "knowledge_search": "the knowledge base",
    },
    "ar": {
        "tyre_fleet_summary": "ملخص الأسطول", "tyres_due": "الإطارات المستحقة", "purchase_plan": "خطة الشراء",
        "tyre_alerts": "تنبيهات الإطارات", "workspace_tasks": "المهام", "workspace_approvals": "الموافقات",
        "workspace_agents": "الوكلاء", "knowledge_search": "قاعدة المعرفة",
    },
}
_GENERIC = {"en": "some of the data", "ar": "بعض البيانات"}


def figures_in(text: str) -> set[float]:
    """The numbers written as digits in ``text`` (Latin or Arabic-Indic, with separators)."""
    found: set[float] = set()
    for match in _NUMBER.finditer(text.translate(_ARABIC_DIGITS)):
        raw = match.group(0).replace("٬", "").replace(",", "").replace("٫", ".")
        try:
            found.add(float(raw))
        except ValueError:
            continue
    return found


def word_figures(text: str) -> set[float]:
    """Number words that state a count (``twenty``, ``three tyres``): a net for spelled-out figures."""
    found: set[float] = set()
    for match in _WORD_FIGURE.finditer(text):
        word = (match.group("big") or match.group("small")).lower()
        value = _WORDS.get(word) or _WORDS.get(word.rstrip("s"))
        found.add(float(value) if value else -1.0)
    return found


def needs_figures(question: str) -> bool:
    """A question that asks for the workspace's figures (checked strictly)."""
    from .tyre.tools import mentions_tyres

    return mentions_tyres(question) or bool(_NEEDS_FIGURES.search(question))


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


class FigureGuard:
    """Sentence-level gate between the model's text and the client (see the module docstring)."""

    def __init__(self, question: str, *, arabic: bool, evidence: Iterable[str] = ()) -> None:
        self.arabic = arabic
        self._strict_question = needs_figures(question)
        self._supported: set[float] = figures_in(question)
        for text in evidence:
            self.add_evidence(text)
        self.failed: list[str] = []
        self.withheld = 0
        self.retracted = 0
        # True once this turn has a successful source (the snapshot or a lookup).
        self.sourced = False
        self.spoken = ""
        self._pending = ""
        self._sentences = 0

    # evidence ---------------------------------------------------------------------------

    def add_evidence(self, text: str) -> None:
        self._supported |= figures_in(text)

    def failure(self, tool: str) -> None:
        if tool not in self.failed:
            self.failed.append(tool)

    @property
    def strict(self) -> bool:
        return self._strict_question or bool(self.failed)

    def unsupported(self, sentence: str) -> list[float]:
        if not self.strict:
            return []
        wanted = figures_in(sentence) | word_figures(sentence)
        return sorted(value for value in wanted if value not in self._supported)

    # the stream -------------------------------------------------------------------------

    @property
    def full(self) -> bool:
        return self._sentences >= MAX_SPOKEN_SENTENCES

    def feed(self, chunk: str) -> list[str]:
        """Text now safe to send: the complete sentences so far that passed the check."""
        self._pending += chunk
        out: list[str] = []
        while not self.full:
            match = _BOUNDARY.search(self._pending)
            if match is None:
                break
            sentence, self._pending = self._pending[: match.end()], self._pending[match.end():]
            out.extend(self._judge(sentence))
        return out

    def finish(self) -> list[str]:
        """The tail of the reply, then the closing notes (failed lookups, withheld figures)."""
        out: list[str] = []
        if self._pending.strip() and not self.full:
            out.extend(self._judge(self._pending))
        self._pending = ""
        out.extend(self._notes())
        return out

    def _judge(self, sentence: str) -> list[str]:
        if not sentence.strip():
            return [sentence] if self.spoken else []
        if self.sourced and _RETRACTION.search(sentence):
            self.retracted += 1
            return []
        if self.unsupported(sentence):
            self.withheld += 1
            return []
        self._sentences += 1
        self.spoken += sentence
        return [sentence]

    def _notes(self) -> list[str]:
        language = "ar" if self.arabic else "en"
        notes: list[str] = []
        lead = " " if self.spoken and not self.spoken[-1:].isspace() else ""
        if self.failed and not _ACKNOWLEDGED.search(self.spoken):
            labels = _LABELS[language]
            names = [labels.get(tool, _GENERIC[language]) for tool in self.failed]
            joined = ("، " if self.arabic else ", ").join(dict.fromkeys(names))
            notes.append(
                f"لم أتمكن من التحقق من {joined} في الوقت المناسب، لذا لا أملك أرقامًا لها." if self.arabic
                else f"I couldn't check {joined} in time, so I have no figures for that."
            )
        elif (self.withheld or self.retracted) and not self.spoken.strip():
            notes.append(
                "لم أستطع تأكيد الأرقام من بياناتك الآن، لذا لن أخمّن. حاول مرة أخرى بعد لحظة." if self.arabic
                else "I couldn't confirm those figures from your data just now, so I won't guess. Please try again in a moment."
            )
        elif self.withheld:
            notes.append(
                "تركت أرقامًا لم أستطع تأكيدها." if self.arabic else "I left out figures I couldn't confirm."
            )
        if notes:
            notes[0] = lead + notes[0]
            self.spoken += notes[0]
        return notes
