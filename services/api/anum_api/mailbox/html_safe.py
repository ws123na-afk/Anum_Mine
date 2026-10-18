"""Mail HTML made safe to show (docs/email.md#email-center).

An allowlist sanitizer on the standard library's HTML parser (no extra dependency):

* Only formatting tags survive. ``script``, ``style``, ``iframe``, ``object``, ``svg``,
  forms and the like are removed with their content; comments and doctypes are dropped.
* Only listed attributes survive; every ``on*`` handler is dropped. ``style`` keeps a small
  set of layout and colour properties and loses any value that could load or run something.
* Links keep ``http``, ``https`` and ``mailto`` targets only and open in a new tab.
* Remote images (``http``/``https``) and ``cid:`` inline parts are removed and counted, so
  opening a message never tells the sender it was read. Small ``data:`` images stay.

The web app shows the result in a sandboxed iframe with a ``default-src 'none'`` policy, so
even a sanitizer miss cannot run script or fetch anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "del",
    "div", "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "li", "ol",
    "p", "pre", "q", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "u", "ul",
})
# Removed together with everything inside them.
DROP_WITH_CONTENT = frozenset({
    "script", "style", "head", "title", "template", "noscript", "iframe", "frame", "frameset", "object", "embed",
    "applet", "svg", "math", "textarea", "select", "button", "audio", "video", "canvas", "map", "base", "link", "meta",
})
VOID_TAGS = frozenset({"br", "hr", "img", "col"})
ATTRIBUTES = frozenset({
    "title", "dir", "lang", "align", "valign", "colspan", "rowspan", "width", "height", "color", "bgcolor",
    "border", "cellpadding", "cellspacing", "alt", "style", "href", "src", "face", "size",
})
STYLE_PROPERTIES = frozenset({
    "color", "background-color", "font-weight", "font-style", "font-size", "font-family", "text-align",
    "text-decoration", "line-height", "vertical-align", "width", "max-width", "min-width", "height",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "border", "border-top", "border-right", "border-bottom", "border-left", "border-collapse", "border-color",
    "border-width", "border-style", "border-radius", "display", "white-space", "word-break", "letter-spacing",
})
_UNSAFE_STYLE_VALUE = re.compile(r"url\s*\(|expression|javascript:|@import|\\|/\*|<|>|behavior|-moz-binding", re.I)
_SIZE = re.compile(r"^\d{1,4}%?$")
_COLOUR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)
MAX_DATA_IMAGE_CHARS = 512 * 1024


@dataclass(frozen=True)
class SafeHtml:
    html: str
    remote_images_blocked: int
    inline_images_hidden: int


def _clean_style(value: str) -> str | None:
    kept: list[str] = []
    for declaration in value.split(";"):
        name, _, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if not name or not raw or name not in STYLE_PROPERTIES or _UNSAFE_STYLE_VALUE.search(raw):
            continue
        kept.append(f"{name}: {raw}")
    return "; ".join(kept) or None


def _safe_link(value: str) -> str | None:
    target = value.strip()
    lowered = re.sub(r"[\s\x00-\x1f]", "", target).lower()
    if lowered.startswith(("http://", "https://", "mailto:")):
        return target
    return None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.dropping: list[str] = []
        self.remote_images = 0
        self.inline_images = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.dropping:
            if tag in DROP_WITH_CONTENT and tag not in VOID_TAGS and tag not in {"base", "link", "meta"}:
                self.dropping.append(tag)
            return
        if tag in DROP_WITH_CONTENT:
            if tag not in {"base", "link", "meta"}:
                self.dropping.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return
        kept: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            if value is None or name not in ATTRIBUTES:
                continue
            if name == "style":
                cleaned = _clean_style(value)
                if cleaned:
                    kept.append((name, cleaned))
            elif name == "href":
                if tag == "a" and (link := _safe_link(value)):
                    kept.append((name, link))
            elif name == "src":
                if tag != "img":
                    continue
                source = value.strip()
                if _DATA_IMAGE.match(source) and len(source) <= MAX_DATA_IMAGE_CHARS:
                    kept.append((name, source))
                elif source.lower().startswith("cid:"):
                    self.inline_images += 1
                else:
                    self.remote_images += 1
            elif name in {"width", "height", "border", "cellpadding", "cellspacing", "colspan", "rowspan", "size"}:
                if _SIZE.match(value.strip()):
                    kept.append((name, value.strip()))
            elif name in {"color", "bgcolor"}:
                if _COLOUR.match(value.strip()):
                    kept.append((name, value.strip()))
            else:
                kept.append((name, value[:500]))
        if tag == "img" and not any(name == "src" for name, _ in kept):
            alt = next((value for name, value in kept if name == "alt"), "")
            if alt:
                self.out.append(f"<span>[{escape(alt[:120])}]</span>")
            return
        if tag == "a":
            kept += [("target", "_blank"), ("rel", "noopener noreferrer nofollow")]
        rendered = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in kept)
        self.out.append(f"<{tag}{rendered}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS and self.open and self.open[-1] == tag.lower():
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.dropping:
            if tag == self.dropping[-1]:
                self.dropping.pop()
            return
        if tag not in self.open:
            return
        while self.open:
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(escape(data, quote=False))

    def close_all(self) -> str:
        self.close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize_html(source: str) -> SafeHtml:
    """The allowlisted form of ``source`` with remote and inline images counted."""
    parser = _Sanitizer()
    parser.feed(source)
    html = parser.close_all()
    return SafeHtml(html=html, remote_images_blocked=parser.remote_images, inline_images_hidden=parser.inline_images)


def html_to_text(source: str) -> str:
    """Plain text of an HTML part, for quoting in replies and as the text view."""
    from ..email_providers import html_to_text as convert

    return convert(source)
