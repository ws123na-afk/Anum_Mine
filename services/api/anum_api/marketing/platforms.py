"""What each platform accepts, checked before a post can be scheduled (docs/marketing.md).

The limits are the platforms' documented API limits (mid-2025). ``validate`` returns
every problem with words for the owner; ``error`` issues block scheduling, ``warning``
issues do not. ``apps/web/src/lib/marketing.ts`` mirrors this table for live counters.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from .models import Language, MediaKind, MediaRef, Platform


@dataclass(frozen=True)
class PlatformSpec:
    name: str
    text_limit: int
    # How text is counted: characters, or X's weighted count (URLs are 23, CJK 2).
    counting: Literal["chars", "x_weighted", "utf8_bytes"] = "chars"
    hashtag_limit: int | None = None
    hashtag_recommended: int | None = None
    requires_media: bool = False
    requires_video: bool = False
    requires_title: bool = False
    title_limit: int | None = None
    max_images: int = 0
    max_videos: int = 0
    mixed_media: bool = False
    image_types: tuple[str, ...] = ("image/jpeg", "image/png")
    video_types: tuple[str, ...] = ("video/mp4", "video/quicktime")
    # Width / height bounds; None means any.
    image_ratio: tuple[float, float] | None = None
    video_ratio: tuple[float, float] | None = None
    video_seconds: tuple[float, float] | None = None
    image_max_bytes: int | None = None
    video_max_bytes: int | None = None
    link_preview: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


SPECS: dict[Platform, PlatformSpec] = {
    Platform.FACEBOOK: PlatformSpec(
        name="Facebook Page",
        text_limit=63206,
        hashtag_recommended=3,
        max_images=10,
        max_videos=1,
        image_types=("image/jpeg", "image/png", "image/gif", "image/webp"),
        image_max_bytes=10 * 1024 * 1024,
        video_seconds=(1, 240 * 60),
        notes=("Images 1.91:1 to 4:5 show best in the feed.",),
    ),
    Platform.INSTAGRAM: PlatformSpec(
        name="Instagram",
        text_limit=2200,
        hashtag_limit=30,
        hashtag_recommended=10,
        requires_media=True,
        max_images=10,
        max_videos=1,
        image_types=("image/jpeg",),
        image_ratio=(0.8, 1.91),
        video_ratio=(0.01, 10.0),
        video_seconds=(3, 15 * 60),
        image_max_bytes=8 * 1024 * 1024,
        video_max_bytes=300 * 1024 * 1024,
        link_preview=False,
        notes=("Links in captions are not clickable on Instagram.", "Reels show best at 9:16."),
    ),
    Platform.LINKEDIN: PlatformSpec(
        name="LinkedIn",
        text_limit=3000,
        hashtag_recommended=5,
        max_images=20,
        max_videos=1,
        image_types=("image/jpeg", "image/png", "image/gif"),
        image_ratio=(1 / 3, 3.0),
        video_ratio=(1 / 2.4, 2.4),
        video_seconds=(3, 30 * 60),
        image_max_bytes=36 * 1024 * 1024,
        video_max_bytes=500 * 1024 * 1024,
    ),
    Platform.X: PlatformSpec(
        name="X",
        text_limit=280,
        counting="x_weighted",
        hashtag_recommended=2,
        max_images=4,
        max_videos=1,
        image_types=("image/jpeg", "image/png", "image/gif", "image/webp"),
        video_seconds=(0.5, 140),
        image_max_bytes=5 * 1024 * 1024,
        video_max_bytes=512 * 1024 * 1024,
        notes=("Hashtags and links count toward the 280 characters; a link always counts as 23.",),
    ),
    Platform.TIKTOK: PlatformSpec(
        name="TikTok",
        text_limit=2200,
        hashtag_recommended=5,
        requires_media=True,
        requires_video=True,
        max_videos=1,
        video_types=("video/mp4", "video/quicktime", "video/webm"),
        video_ratio=(0.5, 0.6),
        video_seconds=(3, 10 * 60),
        video_max_bytes=4 * 1024 * 1024 * 1024,
        link_preview=False,
        notes=("Vertical 9:16 video.",),
    ),
    Platform.YOUTUBE: PlatformSpec(
        name="YouTube",
        text_limit=5000,
        counting="utf8_bytes",
        hashtag_limit=60,
        hashtag_recommended=3,
        requires_media=True,
        requires_video=True,
        requires_title=True,
        title_limit=100,
        max_videos=1,
        video_types=("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"),
        video_seconds=(1, 12 * 3600),
        notes=("A vertical or square video up to 3 minutes is published as a Short.",),
    ),
}

URL_PATTERN = re.compile(r"https?://\S{1,2048}", re.IGNORECASE)
HASHTAG_PATTERN = re.compile(r"(?<![\w&])#([\w؀-ۿ]{1,140})", re.UNICODE)
# Code points X counts as one (Latin, punctuation and general symbols); everything else is two.
_X_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def x_weighted_length(text: str) -> int:
    """X's weighted length: every URL counts 23, light code points 1, others 2."""
    length = 0
    remainder = URL_PATTERN.sub("", text)
    length += 23 * len(URL_PATTERN.findall(text))
    for char in remainder:
        point = ord(char)
        length += 1 if any(low <= point <= high for low, high in _X_LIGHT) else 2
    return length


def compose_text(text: str, hashtags: list[str], link_url: str | None, platform: Platform) -> str:
    """The text a platform receives: the copy, then hashtags not already in it, then the link."""
    body = text.strip()
    present = {tag.lower() for tag in HASHTAG_PATTERN.findall(body)}
    extra = [f"#{tag}" for tag in hashtags if tag.lower() not in present]
    if extra:
        body = f"{body}\n\n{' '.join(extra)}" if body else " ".join(extra)
    if link_url and link_url not in body:
        # Instagram and TikTok show it as text (validate warns); the others link it.
        body = f"{body}\n{link_url}" if body else link_url
    return body


def text_length(text: str, platform: Platform) -> int:
    spec = SPECS[platform]
    if spec.counting == "x_weighted":
        return x_weighted_length(text)
    if spec.counting == "utf8_bytes":
        return len(text.encode("utf-8"))
    return len(text)


class Issue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str


class Validation(BaseModel):
    platform: Platform
    length: int
    limit: int
    hashtags: int
    ok: bool
    issues: list[Issue]
    composed: str


def validate(
    platform: Platform,
    *,
    text: str,
    hashtags: list[str],
    link_url: str | None,
    media: list[MediaRef],
    title: str = "",
) -> Validation:
    spec = SPECS[platform]
    issues: list[Issue] = []

    def error(code: str, message: str) -> None:
        issues.append(Issue(code=code, severity="error", message=message))

    def warn(code: str, message: str) -> None:
        issues.append(Issue(code=code, severity="warning", message=message))

    composed = compose_text(text, hashtags, link_url, platform)
    length = text_length(composed, platform)
    if not composed.strip() and not media:
        error("empty", "Write the post or attach media.")
    if length > spec.text_limit:
        unit = "bytes" if spec.counting == "utf8_bytes" else "characters"
        error("too_long", f"{spec.name} allows {spec.text_limit} {unit}; this post has {length}. Shorten it by {length - spec.text_limit}.")
    tag_count = len({tag.lower() for tag in HASHTAG_PATTERN.findall(composed)})
    if spec.hashtag_limit is not None and tag_count > spec.hashtag_limit:
        error("too_many_hashtags", f"{spec.name} allows at most {spec.hashtag_limit} hashtags; this post has {tag_count}.")
    elif spec.hashtag_recommended is not None and tag_count > spec.hashtag_recommended:
        warn("many_hashtags", f"{tag_count} hashtags: {spec.name} posts usually do best with {spec.hashtag_recommended} or fewer.")
    if spec.requires_title:
        if not title.strip():
            error("title_required", f"{spec.name} needs a title.")
        elif spec.title_limit and len(title) > spec.title_limit:
            error("title_too_long", f"{spec.name} titles are at most {spec.title_limit} characters; this one has {len(title)}.")
        if "<" in title or ">" in title:
            error("title_characters", f"{spec.name} titles cannot contain < or >.")
    if link_url:
        parsed = urlsplit(link_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            error("bad_link", "The link must be a full http or https address.")
        elif not spec.link_preview:
            warn("link_not_clickable", f"Links in {spec.name} captions are not clickable; use the profile link or a story.")

    images = [item for item in media if item.kind == MediaKind.IMAGE]
    videos = [item for item in media if item.kind == MediaKind.VIDEO]
    if spec.requires_video and not videos:
        error("video_required", f"{spec.name} posts need a video.")
    elif spec.requires_media and not media:
        error("media_required", f"{spec.name} posts need an image or a video.")
    if images and spec.max_images == 0:
        error("images_unsupported", f"{spec.name} does not take images here; attach a video.")
    elif len(images) > spec.max_images:
        error("too_many_images", f"{spec.name} takes at most {spec.max_images} images; {len(images)} are attached.")
    if len(videos) > spec.max_videos:
        error("too_many_videos", f"{spec.name} takes one video per post.")
    if images and videos and not spec.mixed_media:
        error("mixed_media", f"{spec.name} posts take images or one video, not both.")
    for item in images:
        label = item.name or item.file_id
        if item.content_type and item.content_type not in spec.image_types:
            readable = ", ".join(kind.split("/")[1].upper() for kind in spec.image_types)
            error("image_type", f"{label}: {spec.name} takes {readable} images.")
        if spec.image_max_bytes and item.size_bytes > spec.image_max_bytes:
            error("image_too_large", f"{label}: images are at most {spec.image_max_bytes // (1024 * 1024)} MB on {spec.name}.")
        _check_ratio(spec.image_ratio, item, label, spec.name, "image", error, warn)
    for item in videos:
        label = item.name or item.file_id
        if item.content_type and item.content_type not in spec.video_types:
            error("video_type", f"{label}: {spec.name} takes MP4 or MOV video.")
        if spec.video_max_bytes and item.size_bytes > spec.video_max_bytes:
            error("video_too_large", f"{label}: videos are at most {spec.video_max_bytes // (1024 * 1024)} MB on {spec.name}.")
        _check_ratio(spec.video_ratio, item, label, spec.name, "video", error, warn)
        if spec.video_seconds and item.duration_seconds is not None:
            low, high = spec.video_seconds
            if not low <= item.duration_seconds <= high:
                error("video_length", f"{label}: {spec.name} videos are {_seconds(low)} to {_seconds(high)} long; this one is {_seconds(item.duration_seconds)}.")
        elif spec.video_seconds and item.duration_seconds is None:
            warn("video_length_unknown", f"{label}: the video's length is unknown; {spec.name} takes {_seconds(spec.video_seconds[0])} to {_seconds(spec.video_seconds[1])}.")
    return Validation(
        platform=platform,
        length=length,
        limit=spec.text_limit,
        hashtags=tag_count,
        ok=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        composed=composed,
    )


def _check_ratio(bounds, item: MediaRef, label: str, name: str, kind: str, error, warn) -> None:  # type: ignore[no-untyped-def]
    if bounds is None:
        return
    if not item.width or not item.height:
        warn(f"{kind}_size_unknown", f"{label}: the {kind}'s size is unknown, so its shape could not be checked.")
        return
    ratio = item.width / item.height
    low, high = bounds
    if not low - 0.005 <= ratio <= high + 0.005:
        error(f"{kind}_ratio", f"{label}: {name} takes {kind}s with a width/height ratio from {_ratio(low)} to {_ratio(high)}; this one is {_ratio(ratio)} ({item.width}x{item.height}).")


def _ratio(value: float) -> str:
    for label, exact in (("9:16", 9 / 16), ("4:5", 0.8), ("1:1", 1.0), ("16:9", 16 / 9), ("1.91:1", 1.91)):
        if abs(value - exact) < 0.01:
            return label
    return f"{value:.2f}:1"


def _seconds(value: float) -> str:
    if value >= 3600:
        return f"{value / 3600:g} h"
    if value >= 60:
        return f"{value / 60:g} min"
    return f"{value:g} s"


def content_hash(post_text: str, title: str, hashtags: list[str], link_url: str | None, media: list[MediaRef], account_id: str) -> str:
    """What an approval binds: the words, the media, the link and the account. Not the time."""
    document = {
        "account_id": account_id,
        "hashtags": hashtags,
        "link_url": link_url,
        "media": [item.file_id for item in media],
        "text": post_text,
        "title": title,
    }
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# -- UTM links ------------------------------------------------------------------------


def with_utm(url: str, *, source: str, medium: str = "social", campaign: str = "", content: str = "") -> str:
    """``url`` with campaign tracking for analytics, store links included.

    Google Play keeps install attribution in its ``referrer`` parameter (URL-encoded
    utm pairs); the App Store uses ``ct`` (campaign token); every other link gets
    ``utm_*`` query parameters. Existing parameters are kept; ours replace same-named ones.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("The link must be a full http or https address.")
    tracking = {"utm_source": source, "utm_medium": medium}
    if campaign:
        tracking["utm_campaign"] = campaign
    if content:
        tracking["utm_content"] = content
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    host = parts.netloc.lower()
    if host == "play.google.com":
        query["referrer"] = urlencode(tracking)
    elif host in {"apps.apple.com", "itunes.apple.com"}:
        token = "-".join(value for value in (campaign or "anum", source) if value)
        query["ct"] = token[:40]
    else:
        query.update(tracking)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote), parts.fragment))


# -- LinkedIn "little text" -------------------------------------------------------------

_LITTLE_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def linkedin_commentary(text: str) -> str:
    """LinkedIn's Posts API reads commentary as "little text": reserved characters are
    escaped, and ``#tag`` becomes a hashtag element so it links."""
    escaped = _LITTLE_RESERVED.sub(r"\\\1", text)
    return HASHTAG_PATTERN.sub(lambda match: "{hashtag|\\#|" + match.group(1) + "}", escaped)


# -- image sizes ------------------------------------------------------------------------


def image_size(content: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG, JPEG, GIF or WebP image from its header; None otherwise."""
    try:
        if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
            width, height = struct.unpack(">II", content[16:24])
            return width, height
        if content[:6] in (b"GIF87a", b"GIF89a"):
            width, height = struct.unpack("<HH", content[6:10])
            return width, height
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            chunk = content[12:16]
            if chunk == b"VP8X":
                width = 1 + int.from_bytes(content[24:27], "little")
                height = 1 + int.from_bytes(content[27:30], "little")
                return width, height
            if chunk == b"VP8 ":
                width, height = struct.unpack("<HH", content[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L":
                bits = int.from_bytes(content[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return None
        if content[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(content):
                if content[index] != 0xFF:
                    index += 1
                    continue
                marker = content[index + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = struct.unpack(">H", content[index + 2 : index + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", content[index + 5 : index + 9])
                    return width, height
                index += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def media_kind(content_type: str) -> MediaKind | None:
    if content_type.startswith("image/"):
        return MediaKind.IMAGE
    if content_type.startswith("video/"):
        return MediaKind.VIDEO
    return None


def default_language_hashtags(language: Language) -> list[str]:
    return ["KSA", "UAE"] if language == Language.EN else ["السعودية", "الإمارات"]
