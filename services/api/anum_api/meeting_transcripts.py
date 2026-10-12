"""Transcript files into timed, speaker-labelled segments (docs/meetings.md#importing).

Supported uploads: WebVTT (``.vtt``, what Zoom and Microsoft Teams export), SubRip
(``.srt``), plain text (``.txt``) and Word (``.docx``, Teams' "Download as .docx").
Every parser returns :class:`ParsedSegment` items in order; text without timestamps gets
``start_ms = None``. Nothing here calls the network or a model.

Speaker names come from the file when it has them:

* WebVTT voice tags ``<v Jane Doe>Hello</v>`` (Teams), or a ``Name: text`` prefix (Zoom);
* ``Name: text`` lines, optionally after a ``[00:01:02]`` or ``00:01:02`` timestamp;
* Teams .docx/.txt blocks: a ``Name   0:03`` (or ``0:03 Name``) line followed by text.

Word files are read without an XML parser (only the ``<w:t>`` runs of each paragraph
are taken), so entity expansion and external entities never apply; the document part is
read only when its uncompressed size is within the transcript limit.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

MAX_SPEAKER_CHARS = 120
MAX_SEGMENT_CHARS = 4000
MAX_SEGMENTS = 20_000


class TranscriptParseError(ValueError):
    """The file is not a transcript this module can read; the message says why."""


@dataclass(frozen=True)
class ParsedSegment:
    text: str
    speaker: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


TRANSCRIPT_EXTENSIONS = {".vtt", ".srt", ".txt", ".docx"}

_CUE_TIME = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3})",
    re.MULTILINE,
)
# "[00:01:02] Jane: text", "00:01:02 Jane: text", "(1:02) Jane: text"
_LINE_TIME = re.compile(r"^\s*[\[(]?(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})(?:[.,]\d{1,3})?[\])]?\s*[-–]?\s*")
_SPEAKER_PREFIX = re.compile(r"^(?P<speaker>[^:\n]{1,80}?)\s*[:：]\s+(?P<text>\S.*)$")
# Teams docx/txt block headers: "Jane Doe   0:03" or "0:03 Jane Doe" or "Jane Doe 00:01:02"
_NAME_THEN_TIME = re.compile(r"^(?P<speaker>[^\d:][^:]{0,80}?)\s{1,}(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*$")
_TIME_THEN_NAME = re.compile(r"^(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2})\s+(?P<speaker>[^\d:][^:]{0,80}?)\s*$")
_URLISH = re.compile(r"^(https?|mailto|ftp)$", re.IGNORECASE)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN = re.compile(r"[A-Za-z]")


def clock_to_ms(value: str) -> int:
    """``HH:MM:SS.mmm``, ``MM:SS,mmm`` or ``H:MM:SS`` into milliseconds."""
    value = value.strip().replace(",", ".")
    fraction = 0
    if "." in value:
        value, frac = value.split(".", 1)
        fraction = int((frac + "000")[:3])
    parts = [int(part) for part in value.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts[-3:]
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + fraction


def detect_language(text: str) -> str:
    """``ar`` when Arabic letters outnumber Latin ones, else ``en``."""
    arabic = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    return "ar" if arabic > latin else "en"


def strip_tags(text: str) -> str:
    """Drop ``<...>`` markup (VTT voice, class and timestamp tags) by one linear scan.

    A ``<`` with no ``>`` after it is kept as text, as is everything after it: no regex,
    so hostile input such as ``<`` followed by many ``</`` costs one pass.
    """
    out: list[str] = []
    position = 0
    while True:
        start = text.find("<", position)
        if start == -1:
            out.append(text[position:])
            break
        end = text.find(">", start + 1)
        if end == -1:
            out.append(text[position:])
            break
        out.append(text[position:start])
        position = end + 1
    return "".join(out)


def voice_speaker(text: str) -> str | None:
    """The speaker of the first WebVTT voice tag (``<v Jane>``, ``<v.loud Jane>``), or None.

    Linear: each ``<v`` candidate is checked against the next ``>`` (found once and
    reused while it is still ahead), never by a backtracking pattern.
    """
    lower = text.lower()
    position = 0
    closing = -1
    while True:
        start = lower.find("<v", position)
        if start == -1:
            return None
        if closing <= start:
            closing = text.find(">", start + 2)
            if closing == -1:
                return None
        position = start + 2
        inner = text[start + 2 : closing]
        if not inner or not (inner[0] == "." or inner[0].isspace()):
            continue
        if inner[0] == ".":
            # Classes run up to the first whitespace: "<v.loud.blue Jane>".
            cut = next((index for index, char in enumerate(inner) if char.isspace()), -1)
            if cut == -1:
                continue
            inner = inner[cut:]
        name = inner.strip()
        if name:
            return name


def _clean(text: str) -> str:
    return " ".join(html.unescape(strip_tags(text)).split())


def _speaker(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return value[:MAX_SPEAKER_CHARS] or None


def _split_speaker(text: str) -> tuple[str | None, str]:
    match = _SPEAKER_PREFIX.match(text)
    if match is None:
        return None, text
    speaker = match.group("speaker").strip()
    # "Note: ..." style prose and URLs are not speakers: speakers are short names.
    if len(speaker.split()) > 4 or _URLISH.match(speaker):
        return None, text
    return speaker, match.group("text").strip()


def _segment(text: str, speaker: str | None, start: int | None, end: int | None) -> ParsedSegment | None:
    text = text.strip()
    if not text:
        return None
    return ParsedSegment(text=text[:MAX_SEGMENT_CHARS], speaker=_speaker(speaker), start_ms=start, end_ms=end)


def _cap(segments: list[ParsedSegment]) -> list[ParsedSegment]:
    if len(segments) > MAX_SEGMENTS:
        raise TranscriptParseError(f"The transcript has more than {MAX_SEGMENTS} lines; split it into parts.")
    return segments


def parse_cues(text: str) -> list[ParsedSegment]:
    """WebVTT and SubRip: blocks with a ``start --> end`` line, then the cue text."""
    segments: list[ParsedSegment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿"))
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if _CUE_TIME.match(line)), None)
        if timing_index is None:
            continue
        match = _CUE_TIME.match(lines[timing_index])
        if match is None:
            continue
        start, end = clock_to_ms(match.group("start")), clock_to_ms(match.group("end"))
        raw = " ".join(lines[timing_index + 1 :])
        speaker = voice_speaker(raw)
        body = _clean(raw)
        if speaker is None:
            speaker, body = _split_speaker(body)
        segment = _segment(body, speaker, start, end)
        if segment is None:
            continue
        previous = segments[-1] if segments else None
        # Zoom and Teams split one sentence over several short cues; join same-speaker
        # neighbours (up to a sensible length) so the transcript reads naturally.
        if (
            previous is not None
            and previous.speaker == segment.speaker
            and previous.end_ms is not None
            and start - previous.end_ms <= 1500
            and len(previous.text) + len(segment.text) < 600
        ):
            segments[-1] = ParsedSegment(
                text=f"{previous.text} {segment.text}",
                speaker=previous.speaker,
                start_ms=previous.start_ms,
                end_ms=end,
            )
        else:
            segments.append(segment)
    if not segments:
        raise TranscriptParseError("No timed captions were found. Check that this is a .vtt or .srt transcript.")
    return _cap(segments)


def parse_text(text: str) -> list[ParsedSegment]:
    """Plain text: ``[time] Speaker: text`` lines, Teams name/time blocks, or paragraphs."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n")]
    segments: list[ParsedSegment] = []
    block_speaker: str | None = None
    block_start: int | None = None
    block_lines: list[str] = []

    def flush_block() -> None:
        nonlocal block_lines
        if block_lines:
            segment = _segment(" ".join(block_lines), block_speaker, block_start, None)
            if segment is not None:
                segments.append(segment)
        block_lines = []

    for line in lines:
        if not line:
            flush_block()
            continue
        header = _NAME_THEN_TIME.match(line) or _TIME_THEN_NAME.match(line)
        if header is not None and len(header.group("speaker").split()) <= 6:
            flush_block()
            block_speaker = header.group("speaker").strip()
            block_start = clock_to_ms(header.group("time"))
            continue
        start: int | None = None
        timed = _LINE_TIME.match(line)
        rest = line
        if timed is not None and timed.end() < len(line):
            start = clock_to_ms(timed.group("time"))
            rest = line[timed.end() :]
        speaker, body = _split_speaker(rest)
        if speaker is not None or start is not None:
            flush_block()
            block_speaker, block_start = None, None
            segment = _segment(body, speaker, start, None)
            if segment is not None:
                segments.append(segment)
            continue
        block_lines.append(line)
    flush_block()
    if not segments:
        raise TranscriptParseError("The file has no text.")
    return _cap(segments)


_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.DOTALL)
_RUN_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.DOTALL)
_BREAK = re.compile(r"<w:(?:br|cr|tab)\b[^>]*/>")


def docx_text(content: bytes, max_bytes: int) -> str:
    """The paragraphs of a .docx as lines (empty paragraphs keep block boundaries)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise TranscriptParseError("This .docx file could not be opened.") from exc
    with archive:
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise TranscriptParseError("This .docx file has no document text.") from exc
        if info.file_size > max_bytes * 4:
            raise TranscriptParseError("The document is too large to import.")
        with archive.open(info) as handle:
            xml = handle.read(max_bytes * 4 + 1).decode("utf-8", errors="replace")
    lines: list[str] = []
    for paragraph in _PARAGRAPH.findall(xml):
        paragraph = _BREAK.sub(" ", paragraph)
        lines.append(html.unescape("".join(_RUN_TEXT.findall(paragraph))))
    return "\n".join(lines)


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and not content.startswith((b"\xff\xfe", b"\xfe\xff")):
            break
        return text
    raise TranscriptParseError("The file is not UTF-8 text. Save it as UTF-8 and try again.")


def parse_transcript(file_name: str, content: bytes, *, max_bytes: int) -> list[ParsedSegment]:
    """Parse an uploaded transcript by its extension (sniffing WebVTT/SRT in .txt)."""
    if not content.strip():
        raise TranscriptParseError("The file is empty.")
    suffix = PurePath(file_name).suffix.lower()
    if suffix not in TRANSCRIPT_EXTENSIONS:
        raise TranscriptParseError("Upload a .vtt, .srt, .txt or .docx transcript.")
    if suffix == ".docx":
        return parse_text(docx_text(content, max_bytes))
    text = decode_text(content)
    if suffix in {".vtt", ".srt"} or text.lstrip("﻿").startswith("WEBVTT") or _CUE_TIME.search(text[:2000] or ""):
        try:
            return parse_cues(text)
        except TranscriptParseError:
            if suffix in {".vtt", ".srt"}:
                raise
    return parse_text(text)
