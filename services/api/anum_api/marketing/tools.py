"""Marketing tools for the agent runtime, chat and voice (docs/marketing.md#assistant).

* ``marketing.draft_social_post`` (low risk): writes a draft for a connected account.
  Nothing leaves ANUM.
* ``marketing.schedule_post`` (high risk): publishes a post at its time. Always paused
  for an approval that shows the account, the time and the exact text; after approval
  the publisher posts it, and only if the content still matches the approved hash.
* ``marketing.enable_auto_post`` (high risk, owners): lets one campaign's posts publish
  at their time without a per-post approval, to the accounts named in the approval.
* ``marketing.summary`` (low risk, read-only): "how did last week's Instagram posts do?".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..schemas import RiskLevel, Task, TenantContext, new_id, utc_now
from .analytics import summarize, summary_text
from .models import (
    AccountStatus,
    AssistIdeas,
    CampaignStatus,
    Language,
    Platform,
    Post,
    PostStatus,
)
from .service import AUTO_POST_TOOL, DRAFT_TOOL, SCHEDULE_TOOL, SUMMARY_TOOL, audit, check, post_hash, schedule_arguments
from .store import PostQuery, open_marketing_store

PLATFORM_WORDS: list[tuple[re.Pattern[str], Platform]] = [
    (re.compile(r"\binstagram\b|\binsta\b|\big\b|إنستغرام|انستغرام|انستقرام|إنستقرام", re.IGNORECASE), Platform.INSTAGRAM),
    (re.compile(r"\bfacebook\b|\bfb\b|فيسبوك|فيس بوك", re.IGNORECASE), Platform.FACEBOOK),
    (re.compile(r"\blinked ?in\b|لينكد", re.IGNORECASE), Platform.LINKEDIN),
    (re.compile(r"\btiktok\b|\btik tok\b|تيك ?توك", re.IGNORECASE), Platform.TIKTOK),
    (re.compile(r"\byoutube\b|\bshorts\b|يوتيوب", re.IGNORECASE), Platform.YOUTUBE),
    (re.compile(r"\btwitter\b|\btweets?\b|\bon x\b|\bx posts?\b|تويتر|تغريد|إكس", re.IGNORECASE), Platform.X),
]
SUMMARY_WORDS = re.compile(
    r"\b(how (did|do|are|is|were)|perform\w*|results?|summary|report|stats|statistics|analytics|reach|impressions|engagement|numbers)\b"
    r"|(كيف كان|أداء|اداء|نتائج|تقرير|إحصائ|احصائ|تفاعل|ملخص)",
    re.IGNORECASE,
)
SCHEDULE_WORDS = re.compile(r"\b(schedule|publish|post it|go live)\b|(جدول|انشر|نشر)", re.IGNORECASE)
MARKETING_WORDS = re.compile(r"\b(marketing|campaigns?|social media|posts?)\b|(تسويق|حملة|حملات|منشور|التواصل الاجتماعي)", re.IGNORECASE)
POST_ID = re.compile(r"\b(mkpost_[A-Za-z0-9_-]{6,80})\b")
ISO_TIME = re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})\b")


def platform_in(text: str) -> Platform | None:
    for pattern, platform in PLATFORM_WORDS:
        if pattern.search(text):
            return platform
    return None


def days_in(text: str) -> int:
    lowered = text.lower()
    if re.search(r"\b(today|اليوم)\b", lowered):
        return 1
    if re.search(r"\b(month|30 days|الشهر)\b", lowered) or "شهر" in lowered:
        return 30
    match = re.search(r"\b(\d{1,3}) days?\b", lowered)
    if match:
        return max(1, min(int(match.group(1)), 365))
    return 7


def is_marketing_question(text: str) -> bool:
    """A question about how posts did (voice and chat answer it from the numbers)."""
    return bool(SUMMARY_WORDS.search(text)) and (platform_in(text) is not None or bool(MARKETING_WORDS.search(text)))


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


# -- definitions ---------------------------------------------------------------------------


def schedule_definition() -> ToolDefinition:
    return ToolDefinition(
        name=SCHEDULE_TOOL,
        description="Publish an approved social media post to the workspace's own connected account at its time.",
        risk_level=RiskLevel.HIGH,
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=60,
        # Publishing is a protected kind: autonomy always asks (docs/autonomy.md).
        category="marketing",
        action_kind="publish",
    )


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=DRAFT_TOOL,
            description="Write a draft social media post (English or Arabic) for a connected account; nothing is published.",
            risk_level=RiskLevel.LOW,
            required_roles=frozenset({"owner", "member"}),
            timeout_seconds=60,
            category="marketing",
            action_kind="draft",
        ),
        schedule_definition(),
        ToolDefinition(
            name=AUTO_POST_TOOL,
            description="Let one campaign's posts publish at their time without a per-post approval, to the named accounts only.",
            risk_level=RiskLevel.HIGH,
            required_roles=frozenset({"owner"}),
            timeout_seconds=30,
            # Turning on auto-post lets posts go out unasked: it always asks first.
            category="marketing",
            action_kind="publish",
        ),
        ToolDefinition(
            name=SUMMARY_TOOL,
            description="Summarise how recent social media posts performed (reach, engagement, clicks).",
            risk_level=RiskLevel.LOW,
            timeout_seconds=30,
            idempotent=True,
            category="marketing",
            action_kind="read",
        ),
    ]


# -- handlers ------------------------------------------------------------------------------


def _failed(summary: str, **output: object) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=dict(output))


async def schedule_post(call: ToolCall, context: TenantContext) -> ToolResult:
    """Runs only after approval: the approved post becomes scheduled for the publisher."""
    args = call.arguments
    post_id = str(args.get("post_id", ""))
    approved = str(args.get("content_hash", ""))
    now = utc_now()
    with open_marketing_store(context) as store:
        post = store.get_post(context, post_id, for_update=True)
        if post is None:
            return _failed("The post was deleted; nothing was scheduled.")
        if post.status not in {PostStatus.PENDING_APPROVAL, PostStatus.DRAFT, PostStatus.FAILED, PostStatus.CANCELLED}:
            return _failed(f"The post is already {post.status.value.replace('_', ' ')}; nothing changed.")
        if post.account_id != args.get("account_id") or post_hash(post) != approved:
            return _failed("The post changed after it was sent for approval; nothing was scheduled. Ask for approval again.")
        account = store.get_account(context, post.account_id)
        if account is None or account.status != AccountStatus.CONNECTED or account.platform != post.platform:
            return _failed("The account is no longer connected; nothing was scheduled. Reconnect it and ask again.")
        if not check(post).ok:
            return _failed("The post no longer meets the platform's limits; nothing was scheduled.")
        post.status = PostStatus.SCHEDULED
        post.approved_hash = approved
        post.approved_by = context.user_id
        post.authorised_by = "approval"
        try:
            requested = datetime.fromisoformat(str(args.get("scheduled_at")))
        except ValueError:
            requested = post.scheduled_at or now
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        post.scheduled_at = max(requested, now)
        post.next_attempt_at = None
        post.last_error = None
        post.updated_at = now
        store.save_post(post)
    audit(None, context, "marketing.post.approved", post.id, metadata={"platform": post.platform.value, "content_hash": approved, "scheduled_at": post.scheduled_at.isoformat()})
    soon = post.scheduled_at - now < timedelta(minutes=2)
    when = "within a minute" if soon else f"at {post.scheduled_at.strftime('%Y-%m-%d %H:%M')} UTC"
    return ToolResult(
        status="succeeded",
        summary=f"Approved. The post goes to {account.display_name} on {post.platform.value} {when}.",
        output={"post_id": post.id, "scheduled_at": post.scheduled_at.isoformat()},
        audit_metadata={"post_id": post.id, "platform": post.platform.value},
    )


async def enable_auto_post(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    campaign_id = str(args.get("campaign_id", ""))
    accounts = [str(item) for item in args.get("account_ids") or []]
    now = utc_now()
    with open_marketing_store(context) as store:
        campaign = store.get_campaign(context, campaign_id, for_update=True)
        if campaign is None:
            return _failed("The campaign was deleted; auto-post was not turned on.")
        if campaign.status != CampaignStatus.ACTIVE:
            return _failed("The campaign is not active; auto-post was not turned on.")
        connected = {account.id for account in store.list_accounts(context) if account.status == AccountStatus.CONNECTED}
        if not accounts or not set(accounts) <= connected:
            return _failed("An account in the approval is no longer connected; auto-post was not turned on.")
        campaign.auto_post = True
        campaign.auto_post_accounts = accounts
        campaign.auto_post_approved_by = context.user_id
        campaign.auto_post_enabled_at = now
        campaign.updated_at = now
        store.save_campaign(campaign)
    audit(None, context, "marketing.campaign.auto_post_enabled", campaign_id, metadata={"accounts": accounts})
    return ToolResult(status="succeeded", summary=f"Auto-post is on for {campaign.name}: its scheduled posts publish at their time to the approved accounts.")


async def draft_social_post(call: ToolCall, context: TenantContext) -> ToolResult:
    from .assist import ideas
    from .service import preferences

    args = call.arguments
    brief = str(args.get("brief", "")).strip()[:2000]
    language = Language.AR if args.get("language") == "ar" else Language.EN
    wanted = args.get("platform")
    with open_marketing_store(context) as store:
        accounts = [account for account in store.list_accounts(context) if account.status == AccountStatus.CONNECTED]
    if wanted:
        accounts = [account for account in accounts if account.platform.value == wanted]
    if not accounts:
        where = f" {wanted}" if wanted else ""
        return _failed(f"No{where} account is connected. Connect one on the Marketing page first.")
    account = accounts[0]
    prefs = preferences(context)
    result = await ideas(
        context,
        AssistIdeas(brief=brief, platforms=[account.platform], languages=[language], count=1),
        brand_voice=prefs.brand_voice,
        default_tags={Language.EN: prefs.default_hashtags_en, Language.AR: prefs.default_hashtags_ar},
        tz=prefs.timezone,
    )
    idea = result.ideas[0]
    text = (idea.ar if language == Language.AR else idea.en) or brief
    now = utc_now()
    post = Post(
        id=new_id("mkpost"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, group_id=new_id("mkgroup"),
        account_id=account.id, platform=account.platform, language=language, text=text,
        hashtags=idea.hashtags_ar if language == Language.AR else idea.hashtags_en, ai_generated=result.generator == "model",
        created_by=context.user_id, created_at=now, updated_at=now,
    )
    with open_marketing_store(context) as store:
        store.add_post(post)
    audit(None, context, "marketing.post.drafted", post.id, metadata={"platform": post.platform.value, "by": "assistant"})
    return ToolResult(
        status="succeeded",
        summary=f"Drafted a {account.platform.value} post for {account.display_name} (draft {post.id}): {text[:280]}",
        output={"post_id": post.id, "platform": post.platform.value, "text": text, "generator": result.generator},
        audit_metadata={"post_id": post.id, "platform": post.platform.value},
    )


async def marketing_summary(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    days = max(1, min(int(args.get("days") or 7), 365))
    platform = Platform(args["platform"]) if args.get("platform") in {item.value for item in Platform} else None
    arabic = args.get("language") == "ar"
    text = summary_for(context, platform=platform, days=days, arabic=arabic)
    return ToolResult(status="succeeded", summary=text, output={"text": text, "days": days, "platform": platform.value if platform else None})


def summary_for(context: TenantContext, *, platform: Platform | None, days: int, arabic: bool) -> str:
    from .service import preferences

    end = utc_now()
    tz = preferences(context).timezone
    summary = summarize(context, start=end - timedelta(days=days), end=end, platform=platform, tz=tz)
    if arabic:
        period = "خلال الأيام السبعة الماضية" if days == 7 else ("اليوم" if days == 1 else f"خلال آخر {days} يومًا")
    else:
        period = "In the last 7 days" if days == 7 else ("Today" if days == 1 else f"In the last {days} days")
    return summary_text(summary, platform=platform, arabic=arabic, period=period)


def register_marketing_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the marketing tools to a runtime's registry (the API's and the Temporal worker's)."""
    handlers = {DRAFT_TOOL: draft_social_post, SCHEDULE_TOOL: schedule_post, AUTO_POST_TOOL: enable_auto_post, SUMMARY_TOOL: marketing_summary}
    for definition in definitions():
        if definition.name not in registry.names:
            registry.register(definition, handlers[definition.name])
    return registry


# -- planner -------------------------------------------------------------------------------


def _when(prompt: str) -> datetime | None:
    match = ISO_TIME.search(prompt)
    if match:
        try:
            return datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}:{match.group(3)}:00").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def planned_call(task: Task, prompt: str) -> ToolCall:
    """The marketing tool for a task's prompt: a summary question, a schedule request
    (bound to the named or newest draft), or a draft. Chosen from the user's own words only."""
    platform = platform_in(prompt)
    language = "ar" if is_arabic(prompt) else "en"
    if SUMMARY_WORDS.search(prompt) and not SCHEDULE_WORDS.search(prompt):
        return ToolCall(name=SUMMARY_TOOL, arguments={"platform": platform.value if platform else None, "days": days_in(prompt), "language": language})
    if SCHEDULE_WORDS.search(prompt):
        call = _schedule_call(task, prompt, platform)
        if call is not None:
            return call
    return ToolCall(name=DRAFT_TOOL, arguments={"brief": " ".join(prompt.split())[:2000], "platform": platform.value if platform else None, "language": language})


def _schedule_call(task: Task, prompt: str, platform: Platform | None) -> ToolCall | None:
    context = TenantContext(tenant_id=task.tenant_id, workspace_id=task.workspace_id, user_id=task.created_by or "", roles=[])
    explicit = POST_ID.search(prompt)
    with open_marketing_store(context) as store:
        post = store.get_post(context, explicit.group(1)) if explicit else None
        if post is None:
            drafts = store.list_posts(context, PostQuery(statuses={PostStatus.DRAFT}, platform=platform, limit=20))
            mine = [item for item in drafts if item.created_by == task.created_by] or drafts
            post = mine[0] if mine else None
        if post is None:
            return None
        account = store.get_account(context, post.account_id)
        campaign = store.get_campaign(context, post.campaign_id) if post.campaign_id else None
    if account is None:
        return None
    when = _when(prompt)
    if when is not None:
        post.scheduled_at = when
    return ToolCall(name=SCHEDULE_TOOL, arguments=schedule_arguments(post, account, campaign))
