"""The signal catalog the person confirms, and the model-free detectors (docs/business.md#signals).

Harvey's idea (docs/business/HARVEY_PARITY.md), written as ANUM code: the pipeline *proposes*
a catalog of signals it knows how to collect, each with what it costs and which score
criterion it can inform, and only the signals an owner **confirmed** are collected. Every
detection is a regular expression over public HTML the run already holds: no model call,
no browser, no login. A signal is an observation of what a page shows (a fact); what it
means for the lead is a separate, reviewable suggestion.

Deliberately not in the catalog (roadmap section 6, reuse register): people scraped from team
pages, e-mail address guessing and verification, personal phone numbers, review counts or
rankings from paid listing providers, and anything behind a login or a captcha.

Also here: the junk filter for discovered domains (directories, social networks, media,
institutions) and the deterministic score suggestions read from observations.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from .models import Criterion


@dataclass(frozen=True)
class SignalSpec:
    code: str
    label: str
    description: str
    category: str  # discovery | profile | channel
    value_type: str  # bool | num | text
    cost_note: str
    criterion: Criterion | None = None


CATALOG: tuple[SignalSpec, ...] = (
    SignalSpec("FOUND_IN_SEARCH", "Found in a market search", "The company appeared in a web search for your market (the query is kept with the result).",
               "discovery", "text", "Included in the discovery search (counts against the daily search cap)."),
    SignalSpec("TECH_STACK", "Website platform and tools", "What the site is built on and which tools it loads (for example WordPress, Shopify, HubSpot, a chat widget).",
               "profile", "text", "Free: the homepage the run already reads.", Criterion.PROJECT_SCALE),
    SignalSpec("RUNNING_GOOGLE_ADS", "Google Ads tag on the site", "A Google Ads conversion tag is on the homepage: the company spends on advertising today.",
               "profile", "bool", "Free: the homepage the run already reads.", Criterion.PROJECT_SCALE),
    SignalSpec("RUNNING_META_ADS", "Meta pixel on the site", "A Meta (Facebook) pixel is on the homepage: the company runs paid social campaigns.",
               "profile", "bool", "Free: the homepage the run already reads.", Criterion.PROJECT_SCALE),
    SignalSpec("NO_SCHEMA_MARKUP", "No structured data", "The homepage has no JSON-LD structured data: a checkable search visibility gap.",
               "profile", "bool", "Free: the homepage the run already reads.", Criterion.PROBLEM_EVIDENCE),
    SignalSpec("NO_ONLINE_BOOKING", "No online booking", "No booking or scheduling tool was found on the homepage: customers have to call or write.",
               "profile", "bool", "Free: the homepage the run already reads.", Criterion.PROBLEM_EVIDENCE),
    SignalSpec("INCUMBENT_AGENCY", "Agency credited on the site", "An agency is credited in the footer with explicit wording (for example \"Website by\"). Recorded only when the wording is explicit.",
               "profile", "text", "Free: the homepage the run already reads."),
    SignalSpec("CONTACT_FORM", "Contact form on the site", "The company's own site has a contact form: an official business channel.",
               "channel", "text", "Free, or one more page read (the contact page).", Criterion.REACHABLE_CHANNEL),
    SignalSpec("PUBLISHED_BUSINESS_EMAIL", "Business address published on the site", "A role address (info@, sales@, contact@ and similar) on the company's own domain is published on its site. Personal addresses are never collected.",
               "channel", "text", "Free: the pages the run already reads.", Criterion.REACHABLE_CHANNEL),
    SignalSpec("SITE_PAGE_COUNT", "Site size", "How many pages the sitemap lists: a rough measure of how much the company has invested in its site.",
               "profile", "num", "One more page read (sitemap.xml).", Criterion.PROJECT_SCALE),
    SignalSpec("CONTENT_STALE", "Content gone stale", "Days since the newest page in the sitemap changed.",
               "profile", "num", "One more page read (sitemap.xml).", Criterion.PROBLEM_EVIDENCE),
    SignalSpec("BLOCKS_AI_CRAWLERS", "Blocks AI crawlers", "robots.txt blocks AI crawlers, so the company may be missing from AI search answers.",
               "profile", "bool", "One more page read (robots.txt, read before any page anyway).", Criterion.PROBLEM_EVIDENCE),
    SignalSpec("HIRING_ROLE", "Hiring", "An open role on the company's careers page.",
               "profile", "text", "One or two more page reads (the careers page).", Criterion.PROJECT_SCALE),
)
CODES = frozenset(spec.code for spec in CATALOG)
BY_CODE = {spec.code: spec for spec in CATALOG}
# Signals that need pages beyond the homepage, and which.
NEEDS_ROBOTS = frozenset({"BLOCKS_AI_CRAWLERS"})
NEEDS_SITEMAP = frozenset({"SITE_PAGE_COUNT", "CONTENT_STALE"})
NEEDS_CAREERS = frozenset({"HIRING_ROLE"})
NEEDS_CONTACT_PAGE = frozenset({"CONTACT_FORM", "PUBLISHED_BUSINESS_EMAIL"})

CAREERS_PATHS = ("/careers", "/jobs")
CONTACT_PATHS = ("/contact", "/contact-us")


def pages_needed(confirmed: Iterable[str], cap: int) -> int:
    """The most page reads a profile of one lead can make for these signals (home first)."""
    wanted = set(confirmed)
    count = 1
    if wanted & NEEDS_ROBOTS:
        count += 1
    if wanted & NEEDS_SITEMAP:
        count += 1
    if wanted & NEEDS_CAREERS:
        count += len(CAREERS_PATHS)
    if wanted & NEEDS_CONTACT_PAGE:
        count += 1
    return min(count, cap)


# -- HTML helpers --------------------------------------------------------------------------------


def strip_code(html: str) -> str:
    """Scripts, styles and comments out before any text regex (minified code is not prose)."""
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    return re.sub(r"(?s)<!--.*?-->", " ", html)


def visible_text(html: str, limit: int = 20000) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", strip_code(html))
    return " ".join(unescape(text).split())[:limit]


def page_title(html: str) -> str:
    found = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return " ".join(unescape(found.group(1)).split())[:300] if found else ""


def meta_description(html: str) -> str:
    found = re.search(r"""(?is)<meta[^>]+name=["']description["'][^>]+content=["']([^"']{1,600})["']""", html) or re.search(
        r"""(?is)<meta[^>]+content=["']([^"']{1,600})["'][^>]+name=["']description["']""", html
    )
    return " ".join(unescape(found.group(1)).split())[:600] if found else ""


def _around(text: str, start: int, end: int, width: int = 80) -> str:
    return " ".join(text[max(0, start - width) : end + width].split())[:300]


# -- detectors ------------------------------------------------------------------------------------

# Distinctive script hosts and generator tags only, so false positives stay rare.
TECH_FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "WordPress": (r"wp-content/", r"wp-includes/"),
    "Shopify": (r"cdn\.shopify\.com", r"myshopify\.com"),
    "Wix": (r"static\.parastorage\.com",),
    "Squarespace": (r"static1\.squarespace\.com",),
    "Webflow": (r"assets\.website-files\.com", r"data-wf-domain"),
    "Salla": (r"cdn\.salla\.(sa|network)",),
    "Zid": (r"zid\.store", r"cdn\.zid\.sa"),
    "Next.js": (r"/_next/static/",),
    "HubSpot": (r"js\.hs-scripts\.com", r"js\.hsforms\.net"),
    "Google Analytics": (r"googletagmanager\.com/gtag", r"google-analytics\.com/analytics\.js"),
    "Google Tag Manager": (r"googletagmanager\.com/gtm\.js",),
    "Hotjar": (r"static\.hotjar\.com",),
    "Intercom": (r"widget\.intercom\.io", r"js\.intercomcdn\.com"),
    "Zendesk": (r"static\.zdassets\.com",),
    "Tidio": (r"code\.tidio\.co",),
    "WhatsApp chat link": (r"wa\.me/\d", r"api\.whatsapp\.com/send"),
    "Calendly": (r"assets\.calendly\.com",),
    "Stripe": (r"js\.stripe\.com",),
    "WooCommerce": (r"woocommerce", r"wc-ajax"),
    "Cloudflare": (r"/cdn-cgi/",),
}
GOOGLE_ADS = (r"googleadservices\.com", r"gtag/js\?id=AW-", r"google_conversion")
META_PIXEL = (r"connect\.facebook\.net/[^\"']*fbevents\.js", r"fbq\s*\(\s*['\"]init")
BOOKING_HINTS = (r"calendly\.com", r"acuityscheduling", r"setmore", r"simplybook", r"squareup\.com/appointments",
                 r"book(ing)?[-_ ]?now", r"schedule[-_ ]?(an?[-_ ])?(appointment|consultation|visit)", r"احجز")
AI_CRAWLERS = ("gptbot", "google-extended", "ccbot", "anthropic-ai", "claudebot", "perplexitybot", "bytespider")
ROLE_LOCAL_PARTS = frozenset({"info", "contact", "sales", "hello", "enquiries", "inquiries", "enquiry", "support", "office", "admin", "team", "business"})
STRONG_CREDIT = re.compile(r"\b(powered|website|site|web\s*design|designed|developed|built|created|marketing)\s+(by|&)\b", re.I)
BENIGN_HOSTS = ("facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.", "tiktok.", "snapchat.", "pinterest.",
                "google.", "goo.gl", "wa.me", "whatsapp.", "apple.com", "wordpress.org", "wix.com", "squarespace.com", "shopify.com",
                "webflow.com", "cloudflare", "godaddy", "jquery", "gstatic", "googleapis", "w3.org", "schema.org", "paypal", "stripe.com")
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24})\b")


@dataclass
class Found:
    """One detection: the value and the excerpt it rests on."""

    code: str
    value_num: float | None = None
    value_text: str = ""
    excerpt: str = ""
    confidence: float = 1.0


def _first(html: str, patterns: Sequence[str]) -> re.Match[str] | None:
    for pattern in patterns:
        found = re.search(pattern, html, re.I)
        if found:
            return found
    return None


def detect_tech(html: str, max_bytes: int = 300_000) -> list[str]:
    sample = html[:max_bytes]
    return sorted(name for name, patterns in TECH_FINGERPRINTS.items() if _first(sample, patterns))


def detect_agency(html: str, domain: str) -> tuple[str, str] | None:
    """(name, excerpt) of an agency credited in the footer with explicit wording, or None."""
    cleaned = strip_code(html)
    footer = re.search(r"(?is)<footer\b.*?</footer>", cleaned)
    region = footer.group(0) if footer else cleaned[-12000:]
    for link in re.finditer(r"""(?is)<a\b[^>]*href=["'](https?://[^"']+)["'][^>]*>(.*?)</a>""", region):
        href = link.group(1)
        anchor = " ".join(unescape(re.sub(r"(?s)<[^>]+>", " ", link.group(2))).split())
        host = (urlsplit(href).hostname or "").lower().removeprefix("www.")
        if not host or not anchor or len(anchor) > 80 or host.endswith(domain) or domain.endswith(host):
            continue
        if any(benign in host for benign in BENIGN_HOSTS):
            continue
        context = " ".join(unescape(re.sub(r"(?s)<[^>]+>", " ", region[max(0, link.start() - 120) : link.start()])).split())
        if STRONG_CREDIT.search(f"{context} {anchor}"):
            return anchor[:80], f"{context[-80:]} {anchor}".strip()[:300]
    return None


def business_emails(html: str, domain: str) -> list[str]:
    """Role addresses on the company's own domain only; personal-looking addresses are skipped."""
    found: list[str] = []
    for match in EMAIL.finditer(unescape(strip_code(html))):
        local, host = match.group(1).lower(), match.group(2).lower().removeprefix("www.")
        if local in ROLE_LOCAL_PARTS and (host == domain or host.endswith("." + domain)):
            address = f"{local}@{host}"
            if address not in found:
                found.append(address)
    return found[:3]


def detect_home(html: str, domain: str, confirmed: set[str]) -> list[Found]:
    """Every confirmed signal the homepage shows. Absence signals need a page that was read."""
    out: list[Found] = []
    if "TECH_STACK" in confirmed:
        tech = detect_tech(html)
        if tech:
            out.append(Found("TECH_STACK", value_text=", ".join(tech)[:300], excerpt="Detected from script hosts and generator tags: " + ", ".join(tech)[:250]))
    if "RUNNING_GOOGLE_ADS" in confirmed:
        hit = _first(html, GOOGLE_ADS)
        out.append(Found("RUNNING_GOOGLE_ADS", value_num=1.0 if hit else 0.0,
                         excerpt=_around(html, hit.start(), hit.end(), 40) if hit else "No Google Ads tag on the homepage."))
    if "RUNNING_META_ADS" in confirmed:
        hit = _first(html, META_PIXEL)
        out.append(Found("RUNNING_META_ADS", value_num=1.0 if hit else 0.0,
                         excerpt=_around(html, hit.start(), hit.end(), 40) if hit else "No Meta pixel on the homepage."))
    if "NO_SCHEMA_MARKUP" in confirmed:
        has_schema = bool(re.search(r"application/ld\+json", html, re.I) and re.search(r'"@type"', html))
        out.append(Found("NO_SCHEMA_MARKUP", value_num=0.0 if has_schema else 1.0,
                         excerpt="JSON-LD structured data found." if has_schema else "No JSON-LD structured data on the homepage."))
    if "NO_ONLINE_BOOKING" in confirmed:
        hit = _first(html, BOOKING_HINTS)
        out.append(Found("NO_ONLINE_BOOKING", value_num=0.0 if hit else 1.0,
                         excerpt=_around(html, hit.start(), hit.end(), 40) if hit else "No booking or scheduling tool on the homepage."))
    if "INCUMBENT_AGENCY" in confirmed:
        agency = detect_agency(html, domain)
        if agency:
            out.append(Found("INCUMBENT_AGENCY", value_text=agency[0], excerpt=agency[1], confidence=0.9))
    out.extend(detect_channels(html, domain, confirmed))
    return out


def detect_channels(html: str, domain: str, confirmed: set[str]) -> list[Found]:
    out: list[Found] = []
    if "CONTACT_FORM" in confirmed:
        form = re.search(r"(?is)<form\b[^>]*>", strip_code(html))
        if form and re.search(r"(?is)<(textarea|input)\b", strip_code(html)[form.start():form.start() + 5000]):
            out.append(Found("CONTACT_FORM", value_text="form", excerpt=_around(strip_code(html), form.start(), form.end(), 20)))
    if "PUBLISHED_BUSINESS_EMAIL" in confirmed:
        for address in business_emails(html, domain):
            out.append(Found("PUBLISHED_BUSINESS_EMAIL", value_text=address, excerpt=f"Published on the company's site: {address}"))
    return out


def detect_robots(text: str) -> Found:
    lowered = text.lower()
    blocked = [bot for bot in AI_CRAWLERS if re.search(rf"user-agent:\s*{re.escape(bot)}[\s\S]{{0,200}}?disallow:\s*/\s", lowered + "\n")]
    return Found("BLOCKS_AI_CRAWLERS", value_num=1.0 if blocked else 0.0,
                 excerpt=("robots.txt disallows: " + ", ".join(blocked)) if blocked else "robots.txt does not block the common AI crawlers.")


def detect_sitemap(xml: str, confirmed: set[str], today: date) -> list[Found]:
    out: list[Found] = []
    locs = re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml)
    if "SITE_PAGE_COUNT" in confirmed and locs:
        out.append(Found("SITE_PAGE_COUNT", value_num=float(len(locs)), excerpt=f"The sitemap lists {len(locs)} page(s)."))
    if "CONTENT_STALE" in confirmed:
        dates = re.findall(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})", xml)
        if dates:
            newest = max(dates)
            try:
                age = (today - date.fromisoformat(newest)).days
            except ValueError:
                return out
            out.append(Found("CONTENT_STALE", value_num=float(max(0, age)), value_text=newest, excerpt=f"Newest page changed on {newest}."))
    return out


HIRING_WORDS = re.compile(r"\b(hiring|apply|position|opening|vacanc|manager|engineer|developer|designer|sales|marketing|accountant|technician|coordinator)\w*|وظيف|مطلوب", re.I)


def detect_hiring(html: str) -> list[Found]:
    roles: list[str] = []
    for match in re.finditer(r"(?is)<(h[2-5]|li|a)[^>]*>\s*([^<]{6,80})\s*</\1>", strip_code(html)):
        text = " ".join(unescape(match.group(2)).split())
        if HIRING_WORDS.search(text) and text.casefold() not in {role.casefold() for role in roles}:
            roles.append(text)
        if len(roles) >= 3:
            break
    return [Found("HIRING_ROLE", value_text=role[:300], excerpt=f"Careers page lists: {role}"[:300]) for role in roles]


# -- discovery junk filter -------------------------------------------------------------------------

JUNK_DOMAIN_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"(^|\.)(yelp|yellowpages|manta|bbb|angi|thumbtack|houzz|nextdoor|tripadvisor|trustpilot|foursquare|mapquest)\.",
    r"(^|\.)(facebook|instagram|twitter|x|linkedin|youtube|tiktok|pinterest|snapchat|threads)\.",
    r"(^|\.)(indeed|glassdoor|bayt|naukrigulf|gulftalent|ziprecruiter|craigslist|dubizzle|opensooq|haraj)\.",
    r"(^|\.)(wikipedia|reddit|quora|medium|substack|forbes|zawya|gulfnews|khaleejtimes|arabnews|argaam)\.",
    r"(^|\.)(google|bing|yahoo|duckduckgo|amazon|apple|microsoft|noon)\.",
    r"(directory|listings?|find-?a-|top\d+|best-?of|yellow)\.",
    r"\.(edu|gov|mil)(\.[a-z]{2})?$",
    r"(wordpress|wix|squarespace|shopify|godaddy|weebly|webflow|blogspot)\.com$",
))
JUNK_NAME_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"^\s*(top|best|\d+)\s+\d*\s*(best\s+)?\w+.*\b(in|near|of)\b",
    r"\b(directory|listings?|reviews? of|compare|find a)\b",
))


def is_junk(domain: str, name: str = "") -> bool:
    """True when a search result is a directory, social network, media site or institution."""
    if not domain or "." not in domain or re.fullmatch(r"[\d.]+", domain):
        return True
    if any(pattern.search(domain) for pattern in JUNK_DOMAIN_PATTERNS):
        return True
    return bool(name) and any(pattern.search(name) for pattern in JUNK_NAME_PATTERNS)


def company_name(title: str, domain: str) -> str:
    """A company name from a result title ("Gulf Mixers | Fleet" becomes "Gulf Mixers"), else from the domain."""
    head = re.split("\\s[|\\-:\u2013\u2014\u00b7]\\s|\\s[|\u00b7]\\s?|:\\s", title or "", maxsplit=1)[0].strip()
    if 2 <= len(head) <= 120:
        return head
    base = domain.split(".")[0].replace("-", " ").strip()
    return (base.title() or domain)[:120]


# -- deterministic score suggestions --------------------------------------------------------------


@dataclass
class Suggestion:
    criterion: Criterion
    score: int
    reason: str
    rule: str
    observation_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


def latest(observations: Sequence[Any]) -> dict[str, list[Any]]:
    """The newest day's observations per signal (older days are history)."""
    newest: dict[str, date] = {}
    for item in observations:
        if item.signal_code not in newest or item.observed_on > newest[item.signal_code]:
            newest[item.signal_code] = item.observed_on
    grouped: dict[str, list[Any]] = {}
    for item in sorted(observations, key=lambda o: (o.signal_code, o.id)):
        if item.observed_on == newest[item.signal_code]:
            grouped.setdefault(item.signal_code, []).append(item)
    return grouped


def _ids(items: Sequence[Any]) -> tuple[list[str], list[str]]:
    return [item.id for item in items], sorted({item.evidence_id for item in items if item.evidence_id})


def suggest_scores(observations: Sequence[Any]) -> list[Suggestion]:
    """Score suggestions from confirmed observations: same observations, same suggestions.

    Only reachable channel, problem evidence and project scale are ever suggested; industry fit
    is the person's call, and urgency and the buyer's authority come only from a conversation.
    A person applies a suggestion (it then cites the same evidence); nothing is scored here.
    """
    by_code = latest(observations)
    found: list[Suggestion] = []

    emails = by_code.get("PUBLISHED_BUSINESS_EMAIL", [])
    forms = by_code.get("CONTACT_FORM", [])
    if emails:
        obs, ev = _ids(emails)
        found.append(Suggestion(Criterion.REACHABLE_CHANNEL, 4, "The company publishes a business address on its own site.", "published role address on own domain = 4", obs, ev))
    elif forms:
        obs, ev = _ids(forms)
        found.append(Suggestion(Criterion.REACHABLE_CHANNEL, 3, "The company's own site has a contact form.", "contact form on own site = 3", obs, ev))

    gaps: list[Any] = []
    labels: list[str] = []
    for code, label in (("NO_ONLINE_BOOKING", "no online booking"), ("NO_SCHEMA_MARKUP", "no structured data"), ("BLOCKS_AI_CRAWLERS", "blocks AI crawlers")):
        hits = [item for item in by_code.get(code, []) if (item.value_num or 0) >= 1]
        if hits:
            gaps.extend(hits)
            labels.append(label)
    stale = [item for item in by_code.get("CONTENT_STALE", []) if (item.value_num or 0) >= 365]
    if stale:
        gaps.extend(stale)
        labels.append("content unchanged for a year or more")
    if gaps:
        obs, ev = _ids(gaps)
        found.append(Suggestion(Criterion.PROBLEM_EVIDENCE, min(3, len(labels)),
                                f"Checkable gaps on the site: {', '.join(labels)}. Count them only if your offer fixes them.",
                                "one point per checkable gap, at most 3", obs, ev))

    scale_items: list[Any] = []
    score = 0
    pages = by_code.get("SITE_PAGE_COUNT", [])
    if pages:
        count = max(item.value_num or 0 for item in pages)
        score = 3 if count >= 200 else 2 if count >= 50 else 1
        scale_items.extend(pages)
    ads = [item for code in ("RUNNING_GOOGLE_ADS", "RUNNING_META_ADS") for item in by_code.get(code, []) if (item.value_num or 0) >= 1]
    if ads:
        score += 1
        scale_items.extend(ads)
    hiring = by_code.get("HIRING_ROLE", [])
    if hiring:
        score += 1
        scale_items.extend(hiring)
    if scale_items and score > 0:
        obs, ev = _ids(scale_items)
        parts = []
        if pages:
            parts.append(f"{int(max(item.value_num or 0 for item in pages))} page(s) in the sitemap")
        if ads:
            parts.append("paid advertising tags")
        if hiring:
            parts.append("open roles")
        found.append(Suggestion(Criterion.PROJECT_SCALE, min(5, score), "Signs of size: " + ", ".join(parts) + ".",
                                "sitemap pages (1 to 3) + 1 for ad tags + 1 for open roles, at most 5", obs, ev))
    return found


# -- product fit (several products per lead) --------------------------------------------------------

GCC_TLDS = {"sa": "saudi arabia", "ae": "united arab emirates", "qa": "qatar", "kw": "kuwait", "bh": "bahrain", "om": "oman"}
REGION_ALIASES = {"uae": "united arab emirates", "ksa": "saudi arabia", "saudi": "saudi arabia", "emirates": "united arab emirates",
                  "dubai": "united arab emirates", "abu dhabi": "united arab emirates", "sharjah": "united arab emirates",
                  "riyadh": "saudi arabia", "jeddah": "saudi arabia", "dammam": "saudi arabia", "doha": "qatar", "muscat": "oman", "manama": "bahrain"}


@dataclass
class Fit:
    product_id: str
    product_name: str
    score: int | None  # 0 to 5; None when the product has no target profile
    disqualified: bool = False
    reasons: list[str] = field(default_factory=list)


def _regions_of(text: str, domain: str | None) -> set[str]:
    folded = text.casefold()
    found = {name for alias, name in REGION_ALIASES.items() if re.search(rf"\b{re.escape(alias)}\b", folded)}
    found |= {name for name in GCC_TLDS.values() if name in folded}
    tld = (domain or "").rsplit(".", 1)[-1]
    if tld in GCC_TLDS:
        found.add(GCC_TLDS[tld])
    return found


def product_fit(profile: Any, product_id: str, product_name: str, account: Any, texts: Sequence[str]) -> Fit:
    """How well one lead matches one product's target profile, from recorded facts only (deterministic).

    A disqualifier named anywhere: 0. Otherwise industry match 2, region match 1, and one point per
    keyword the company's own pages or results use (at most 2). Capped at 5."""
    if profile is None:
        return Fit(product_id=product_id, product_name=product_name, score=None, reasons=["This product has no target customer profile yet."])
    blob = " ".join([account.name, account.industry, account.city, account.country, *texts])
    folded = blob.casefold()
    fit = Fit(product_id=product_id, product_name=product_name, score=0)
    hits = [word for word in profile.disqualifiers if word.strip() and word.casefold() in folded]
    if hits:
        fit.disqualified = True
        fit.reasons.append(f"Disqualified: mentions {', '.join(hits[:3])}.")
        return fit
    score = 0
    industries = [word for word in profile.industries if word.strip() and word.casefold() in folded]
    if industries:
        score += 2
        fit.reasons.append(f"Industry matches: {', '.join(industries[:3])}.")
    wanted = {REGION_ALIASES.get(region.casefold(), region.casefold()) for region in profile.regions if region.strip()}
    regions = sorted(wanted & _regions_of(blob, account.domain))
    if regions:
        score += 1
        fit.reasons.append(f"In a target region: {', '.join(regions)}.")
    keywords = [word for word in profile.keywords if word.strip() and word.casefold() in folded]
    if keywords:
        score += min(2, len(keywords))
        fit.reasons.append(f"Uses your keywords: {', '.join(keywords[:4])}.")
    fit.score = min(5, score)
    if not fit.reasons:
        fit.reasons.append("Nothing recorded matches this product's target profile yet.")
    return fit


def disqualified_by(profile: Any, text: str) -> str | None:
    if profile is None:
        return None
    folded = text.casefold()
    return next((word for word in profile.disqualifiers if word.strip() and word.casefold() in folded), None)
