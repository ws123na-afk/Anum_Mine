"""Campaign dashboard, best posting times and the weekly report (docs/marketing.md#analytics).

Numbers come from the metrics last pulled from each platform (``service.refresh_metrics``,
``POST /api/v1/marketing/analytics/refresh``); nothing here calls a platform.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from ..schemas import TenantContext, utc_now
from .clients import PLATFORM_LABEL
from .models import Platform, Post, PostMetrics, PostStatus
from .store import PostQuery, open_marketing_store

MAX_POSTS = 2000
PLATFORM_AR = {
    Platform.FACEBOOK: "فيسبوك",
    Platform.INSTAGRAM: "إنستغرام",
    Platform.LINKEDIN: "لينكدإن",
    Platform.X: "إكس",
    Platform.TIKTOK: "تيك توك",
    Platform.YOUTUBE: "يوتيوب",
}
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_AR = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")


class Totals(BaseModel):
    posts: int = 0
    impressions: int = 0
    reach: int = 0
    engagement: int = 0
    clicks: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    # Engagements per impression (or view); null without impressions.
    engagement_rate: float | None = None
    # Posts whose numbers were never pulled.
    without_metrics: int = 0

    def add(self, metrics: PostMetrics | None) -> None:
        self.posts += 1
        if metrics is None:
            self.without_metrics += 1
            return
        for name in ("impressions", "reach", "engagement", "clicks", "likes", "comments", "shares", "views"):
            setattr(self, name, getattr(self, name) + (getattr(metrics, name) or 0))

    def finish(self) -> Totals:
        seen = self.impressions or self.views
        self.engagement_rate = round(self.engagement / seen, 4) if seen else None
        return self


class PlatformTotals(Totals):
    platform: Platform


class CampaignTotals(Totals):
    campaign_id: str | None
    name: str


class TopPost(BaseModel):
    id: str
    platform: Platform
    account_id: str
    excerpt: str
    published_at: datetime | None
    external_url: str | None
    metrics: PostMetrics | None


class DayPoint(BaseModel):
    day: date
    posts: int
    impressions: int
    engagement: int


class Summary(BaseModel):
    start: datetime
    end: datetime
    totals: Totals
    previous: Totals
    platforms: list[PlatformTotals]
    campaigns: list[CampaignTotals]
    top_posts: list[TopPost]
    series: list[DayPoint]
    upcoming: int
    pending_approval: int
    failed: int
    metrics_updated_at: datetime | None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Riyadh")


def _published(context: TenantContext, start: datetime, end: datetime, *, platform: Platform | None, campaign_id: str | None) -> list[Post]:
    with open_marketing_store(context) as store:
        posts = store.list_posts(context, PostQuery(statuses={PostStatus.PUBLISHED, PostStatus.SENT_TO_INBOX}, published_since=start, platform=platform, campaign_id=campaign_id, limit=MAX_POSTS))
    return [post for post in posts if post.published_at is not None and post.published_at < end]


def _excerpt(text: str, limit: int = 140) -> str:
    words = " ".join(text.split())
    return words if len(words) <= limit else words[: limit - 1].rstrip() + "…"


def summarize(
    context: TenantContext,
    *,
    start: datetime,
    end: datetime,
    platform: Platform | None = None,
    campaign_id: str | None = None,
    tz: str = "Asia/Riyadh",
) -> Summary:
    zone = _zone(tz)
    posts = _published(context, start, end, platform=platform, campaign_id=campaign_id)
    previous_posts = _published(context, start - (end - start), start, platform=platform, campaign_id=campaign_id)
    with open_marketing_store(context) as store:
        names = {campaign.id: campaign.name for campaign in store.list_campaigns(context, status=None, text=None, limit=500, before=None)}
        upcoming = store.list_posts(context, PostQuery(statuses={PostStatus.SCHEDULED}, platform=platform, campaign_id=campaign_id, limit=MAX_POSTS))
        waiting = store.list_posts(context, PostQuery(statuses={PostStatus.PENDING_APPROVAL}, platform=platform, campaign_id=campaign_id, limit=MAX_POSTS))
        failed = store.list_posts(context, PostQuery(statuses={PostStatus.FAILED}, platform=platform, campaign_id=campaign_id, limit=MAX_POSTS))
    totals, previous = Totals(), Totals()
    by_platform: dict[Platform, PlatformTotals] = {}
    by_campaign: dict[str | None, CampaignTotals] = {}
    days: dict[date, DayPoint] = {}
    for post in posts:
        totals.add(post.metrics)
        by_platform.setdefault(post.platform, PlatformTotals(platform=post.platform)).add(post.metrics)
        by_campaign.setdefault(post.campaign_id, CampaignTotals(campaign_id=post.campaign_id, name=names.get(post.campaign_id or "", "No campaign"))).add(post.metrics)
        day = (post.published_at or start).astimezone(zone).date()
        point = days.setdefault(day, DayPoint(day=day, posts=0, impressions=0, engagement=0))
        point.posts += 1
        point.impressions += (post.metrics.impressions or post.metrics.views or 0) if post.metrics else 0
        point.engagement += (post.metrics.engagement or 0) if post.metrics else 0
    for post in previous_posts:
        previous.add(post.metrics)
    ranked = sorted(posts, key=lambda post: ((post.metrics.engagement or 0) if post.metrics else -1, post.published_at or start), reverse=True)
    updated = [post.metrics_updated_at for post in posts if post.metrics_updated_at]
    series = []
    cursor = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    while cursor <= last and len(series) < 400:
        series.append(days.get(cursor, DayPoint(day=cursor, posts=0, impressions=0, engagement=0)))
        cursor += timedelta(days=1)
    return Summary(
        start=start,
        end=end,
        totals=totals.finish(),
        previous=previous.finish(),
        platforms=sorted((item.finish() for item in by_platform.values()), key=lambda item: item.engagement, reverse=True),  # type: ignore[misc]
        campaigns=sorted((item.finish() for item in by_campaign.values()), key=lambda item: item.engagement, reverse=True),  # type: ignore[misc]
        top_posts=[
            TopPost(id=post.id, platform=post.platform, account_id=post.account_id, excerpt=_excerpt(post.text or post.title),
                    published_at=post.published_at, external_url=post.external_url, metrics=post.metrics)
            for post in ranked[:5]
        ],
        series=series,
        upcoming=len(upcoming),
        pending_approval=len(waiting),
        failed=len(failed),
        metrics_updated_at=min(updated) if updated else None,
    )


# -- best times ------------------------------------------------------------------------------


class TimeSlot(BaseModel):
    platform: Platform | None
    weekday: int  # 0 = Monday
    hour: int
    posts: int
    engagement_rate: float | None
    basis: str  # "history" (this workspace's own posts) or "default" (Gulf audience defaults)


# Defaults for a Gulf audience before there is history: the weekend is Friday-Saturday,
# evenings after Isha and after work do best. Used until a platform has 5 measured posts.
GULF_DEFAULTS: list[tuple[int, int]] = [(6, 21), (2, 20), (3, 22)]  # Sunday 21:00, Wednesday 20:00, Thursday 22:00
MIN_HISTORY = 5


def best_times(context: TenantContext, *, platform: Platform | None = None, tz: str = "Asia/Riyadh", lookback_days: int = 180) -> list[TimeSlot]:
    """The three best weekday and hour slots from this workspace's own published posts,
    by engagement rate; Gulf defaults until there are enough measured posts."""
    zone = _zone(tz)
    now = utc_now()
    posts = [post for post in _published(context, now - timedelta(days=lookback_days), now, platform=platform, campaign_id=None) if post.metrics is not None]
    if len(posts) < MIN_HISTORY:
        return [TimeSlot(platform=platform, weekday=day, hour=hour, posts=0, engagement_rate=None, basis="default") for day, hour in GULF_DEFAULTS]
    slots: dict[tuple[int, int], list[float]] = defaultdict(list)
    for post in posts:
        metrics = post.metrics or PostMetrics()
        seen = metrics.impressions or metrics.views or 0
        if not seen:
            continue
        local = (post.published_at or now).astimezone(zone)
        slots[(local.weekday(), local.hour)].append((metrics.engagement or 0) / seen)
    ranked = sorted(slots.items(), key=lambda item: (sum(item[1]) / len(item[1]), len(item[1])), reverse=True)
    result = [
        TimeSlot(platform=platform, weekday=day, hour=hour, posts=len(rates), engagement_rate=round(sum(rates) / len(rates), 4), basis="history")
        for (day, hour), rates in ranked[:3]
    ]
    return result or [TimeSlot(platform=platform, weekday=day, hour=hour, posts=0, engagement_rate=None, basis="default") for day, hour in GULF_DEFAULTS]


def next_slot(slot: TimeSlot, tz: str, after: datetime | None = None) -> datetime:
    """The next occurrence of ``slot`` in ``tz`` after ``after``, in UTC."""
    zone = _zone(tz)
    base = (after or utc_now()).astimezone(zone)
    candidate = base.replace(hour=slot.hour, minute=0, second=0, microsecond=0) + timedelta(days=(slot.weekday - base.weekday()) % 7)
    if candidate <= base:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


# -- words -----------------------------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}"


def summary_text(summary: Summary, *, platform: Platform | None, arabic: bool, period: str | None = None) -> str:
    """One or two spoken sentences (voice, chat, the summary tool)."""
    totals = summary.totals
    if arabic:
        where = f"على {PLATFORM_AR[platform]}" if platform else "على كل المنصات"
        when = period or "في هذه الفترة"
        if totals.posts == 0:
            return f"لم يُنشر شيء {where} {when}." + (f" لديك {summary.upcoming} منشورًا مجدولًا." if summary.upcoming else "")
        text = f"{when} {where}: {totals.posts} منشورات، وصلت إلى {_n(totals.reach or totals.impressions)} شخص، مع {_n(totals.engagement)} تفاعل و{_n(totals.clicks)} نقرة."
        if totals.engagement_rate is not None:
            text += f" معدل التفاعل {totals.engagement_rate * 100:.1f}٪."
        if totals.without_metrics:
            text += f" {totals.without_metrics} منشورات بلا أرقام بعد؛ حدّث التحليلات."
        if summary.top_posts and summary.top_posts[0].metrics:
            text += f" الأفضل: «{_excerpt(summary.top_posts[0].excerpt, 60)}»."
        return text
    where = f"on {PLATFORM_LABEL[platform]}" if platform else "across your accounts"
    when = period or "In this period"
    if totals.posts == 0:
        return f"{when}, nothing was published {where}." + (f" {summary.upcoming} posts are scheduled." if summary.upcoming else "")
    plural = "post" if totals.posts == 1 else "posts"
    text = f"{when}, {totals.posts} {plural} {where} reached {_n(totals.reach or totals.impressions)} people, with {_n(totals.engagement)} engagements and {_n(totals.clicks)} link clicks."
    if totals.engagement_rate is not None:
        text += f" That is a {totals.engagement_rate * 100:.1f}% engagement rate"
        if summary.previous.engagement_rate is not None:
            change = (totals.engagement_rate - summary.previous.engagement_rate) * 100
            text += f", {'up' if change >= 0 else 'down'} {abs(change):.1f} points on the period before."
        else:
            text += "."
    if totals.without_metrics:
        text += f" {totals.without_metrics} of them have no numbers yet; refresh analytics."
    if summary.top_posts and summary.top_posts[0].metrics:
        best = summary.top_posts[0]
        text += f" Best: \"{_excerpt(best.excerpt, 60)}\" with {_n(best.metrics.engagement or 0)} engagements."  # type: ignore[union-attr]
    return text


class WeeklyReport(BaseModel):
    week_start: date
    week_end: date
    language: str
    headline: str
    lines: list[str]
    summary: Summary
    best_times: list[TimeSlot]
    # Which channels can carry the report: "available" when that integration is set up in
    # this workspace (POST /reports/weekly/deliver asks for approval), else "not_connected".
    delivery: dict[str, str]


def weekly_report(context: TenantContext, *, week_start: date, tz: str, arabic: bool) -> WeeklyReport:
    zone = _zone(tz)
    start = datetime.combine(week_start, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    end = start + timedelta(days=7)
    summary = summarize(context, start=start, end=end, tz=tz)
    slots = best_times(context, tz=tz)
    period = ("الأسبوع الذي بدأ " if arabic else "The week of ") + week_start.isoformat()
    lines = [summary_text(summary, platform=None, arabic=arabic, period=period)]
    for item in summary.platforms:
        label = PLATFORM_AR[item.platform] if arabic else PLATFORM_LABEL[item.platform]
        rate = f"{item.engagement_rate * 100:.1f}%" if item.engagement_rate is not None else "-"
        lines.append(
            f"{label}: {item.posts} منشورات، {_n(item.impressions or item.views)} ظهور، {_n(item.engagement)} تفاعل ({rate})."
            if arabic else f"{label}: {item.posts} posts, {_n(item.impressions or item.views)} impressions, {_n(item.engagement)} engagements ({rate})."
        )
    if summary.failed:
        lines.append(f"{summary.failed} منشورات فشلت؛ افتح التسويق لمعرفة السبب." if arabic else f"{summary.failed} posts failed; open Marketing to see why and retry.")
    if summary.pending_approval:
        lines.append(f"{summary.pending_approval} منشورات بانتظار الموافقة." if arabic else f"{summary.pending_approval} posts are waiting for approval.")
    if slots:
        days = WEEKDAYS_AR if arabic else WEEKDAYS_EN
        when = "، ".join(f"{days[slot.weekday]} {slot.hour:02d}:00" for slot in slots) if arabic else ", ".join(f"{days[slot.weekday]} {slot.hour:02d}:00" for slot in slots)
        lines.append(("أفضل أوقات النشر: " if arabic else "Best times to post: ") + when + ("" if slots[0].basis == "history" else (" (افتراضيات الخليج حتى تتوفر بيانات كافية)" if arabic else " (Gulf defaults until there is enough history)")))
    return WeeklyReport(
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        language="ar" if arabic else "en",
        headline=("تقرير التسويق الأسبوعي" if arabic else "Weekly marketing report"),
        lines=lines,
        summary=summary,
        best_times=slots,
        delivery=delivery_channels(context),
    )


def delivery_channels(context: TenantContext) -> dict[str, str]:
    """Best effort: a channel whose store cannot be read counts as not connected."""
    channels = {"email": "not_connected", "whatsapp": "not_connected"}
    try:
        from .. import email_inbox

        with email_inbox.open_email_store(context) as store:
            if any(a.status == email_inbox.AccountStatus.CONNECTED for a in email_inbox.visible_accounts(store, context)):
                channels["email"] = "available"
    except Exception:  # nosec B110 - delivery is best effort; the report itself still renders
        pass
    try:
        from .. import whatsapp

        with whatsapp.open_whatsapp_store(context) as store:
            if store.get_account() is not None and not whatsapp.setup_problems():
                channels["whatsapp"] = "available"
    except Exception:  # nosec B110 - as above
        pass
    return channels
