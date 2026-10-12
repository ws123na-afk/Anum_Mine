"""HTTP routes for social media marketing: ``/api/v1/marketing`` (docs/marketing.md).

Permissions (``anum_api/authorization.py``): every member reads (``marketing:read``);
members and owners plan, draft, ask the assistant and ask to publish
(``marketing:write``); owners connect and disconnect accounts, set the brand voice and
turn on a campaign's auto-post (``marketing:manage``). Publishing waits for an approval
that an owner decides in the ordinary approvals flow.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from .. import cron
from ..authorization import Permission
from ..dependencies import repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import Approval, ApprovalStatus, TenantContext, new_id, utc_now
from . import analytics, assist, publisher, report_delivery
from .config import marketing_settings
from .models import (
    EDITABLE,
    AccountStatus,
    AssistHashtags,
    AssistIdeas,
    AutoPostRequest,
    Campaign,
    CampaignCreate,
    CampaignStatus,
    CampaignUpdate,
    Language,
    MarketingPreferences,
    OAuthCallback,
    OAuthStart,
    Platform,
    PlatformError,
    Post,
    PostCreate,
    PostReschedule,
    PostSchedule,
    PostStatus,
    PostUpdate,
    PostValidate,
    PreferencesUpdate,
    SocialAccountView,
)
from .platforms import SPECS, Validation, validate, with_utm
from .service import (
    AUTO_POST_TOOL,
    PostView,
    audit,
    auto_post_covers,
    check_account,
    connected_account,
    finish_oauth,
    get_post_or_404,
    http_error,
    post_hash,
    preferences,
    read_media_link,
    refresh_metrics,
    request_approval,
    resolve_media,
    retire_approval,
    revoke_account,
    schedule_post,
    setup_status,
    start_oauth,
    unschedule,
    views,
)
from .store import MAX_PAGE, MAX_RANGE, PostQuery, open_marketing_store

router = APIRouter(prefix="/api/v1/marketing", tags=["marketing"])

PLATFORM_REVIEW = {
    Platform.FACEBOOK: "Meta App Review: pages_manage_posts, pages_read_engagement, read_insights, pages_show_list (Advanced Access), Business Verification.",
    Platform.INSTAGRAM: "Meta App Review: instagram_basic, instagram_content_publish, instagram_manage_insights. The Instagram account must be a professional (Business or Creator) account linked to the Page.",
    Platform.LINKEDIN: "LinkedIn products: Sign In with LinkedIn using OpenID Connect and Share on LinkedIn (member posts); Community Management API (organization posts and statistics, needs LinkedIn's approval).",
    Platform.X: "X Developer Portal: a project with a paid API access tier for posting (Basic or higher, or pay-per-use credits); OAuth 2.0 user authentication with the read and write permission.",
    Platform.TIKTOK: "TikTok for Developers: Login Kit and Content Posting API. Until TikTok's app audit approves Direct Post, ANUM uploads videos to the creator's inbox as drafts.",
    Platform.YOUTUBE: "Google Cloud: YouTube Data API v3 and an OAuth consent screen (verified for the youtube.upload scope). Videos from an unaudited API project are locked private until the project passes YouTube's audit.",
}


def _read(context: TenantContext) -> None:
    require_permission(context, Permission.MARKETING_READ)


def _write(context: TenantContext) -> None:
    require_permission(context, Permission.MARKETING_WRITE)


def _manage(context: TenantContext) -> None:
    require_permission(context, Permission.MARKETING_MANAGE)


# -- setup and accounts -----------------------------------------------------------------------


class PlatformLimits(BaseModel):
    platform: Platform
    name: str
    text_limit: int
    counting: str
    hashtag_limit: int | None
    hashtag_recommended: int | None
    requires_media: bool
    requires_video: bool
    requires_title: bool
    title_limit: int | None
    max_images: int
    max_videos: int
    image_types: list[str]
    video_types: list[str]
    image_ratio: list[float] | None
    video_ratio: list[float] | None
    video_seconds: list[float] | None
    notes: list[str]
    review: str


class MarketingSetup(BaseModel):
    providers: list[dict[str, Any]]
    redirect_url: str | None
    public_media: bool
    tiktok_direct_post: bool
    platforms: list[PlatformLimits]
    can_manage: bool
    can_write: bool


def _limits() -> list[PlatformLimits]:
    return [
        PlatformLimits(
            platform=platform, name=spec.name, text_limit=spec.text_limit, counting=spec.counting, hashtag_limit=spec.hashtag_limit,
            hashtag_recommended=spec.hashtag_recommended, requires_media=spec.requires_media, requires_video=spec.requires_video,
            requires_title=spec.requires_title, title_limit=spec.title_limit, max_images=spec.max_images, max_videos=spec.max_videos,
            image_types=list(spec.image_types), video_types=list(spec.video_types),
            image_ratio=list(spec.image_ratio) if spec.image_ratio else None, video_ratio=list(spec.video_ratio) if spec.video_ratio else None,
            video_seconds=list(spec.video_seconds) if spec.video_seconds else None, notes=list(spec.notes), review=PLATFORM_REVIEW[platform],
        )
        for platform, spec in SPECS.items()
    ]


@router.get("/setup", response_model=MarketingSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> MarketingSetup:
    """Which platform apps this server has (setting names only, never values) and each platform's limits."""
    _read(context)
    from ..dependencies import has_permission

    return MarketingSetup(
        providers=setup_status(),
        redirect_url=marketing_settings.oauth_redirect_url.strip() or None,
        public_media=bool(marketing_settings.public_api_url.strip()),
        tiktok_direct_post=marketing_settings.tiktok_direct_post,
        platforms=_limits(),
        can_manage=has_permission(context, Permission.MARKETING_MANAGE),
        can_write=has_permission(context, Permission.MARKETING_WRITE),
    )


@router.get("/accounts", response_model=list[SocialAccountView])
async def list_accounts(context: TenantContext = Depends(tenant_context)) -> list[SocialAccountView]:
    _read(context)
    with open_marketing_store(context) as store:
        return [SocialAccountView.of(account) for account in store.list_accounts(context)]


class AuthorizeUrl(BaseModel):
    authorize_url: str


@router.post("/oauth/start", response_model=AuthorizeUrl)
async def oauth_start(payload: OAuthStart, context: TenantContext = Depends(tenant_context)) -> AuthorizeUrl:
    """Where to send the browser to connect accounts; the platform sends it back to the
    redirect URL with a code that ``/oauth/callback`` exchanges."""
    _manage(context)
    return AuthorizeUrl(authorize_url=await start_oauth(context, payload.provider))


@router.post("/oauth/callback", response_model=list[SocialAccountView])
async def oauth_callback(
    payload: OAuthCallback,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[SocialAccountView]:
    _manage(context)
    return [SocialAccountView.of(account) for account in await finish_oauth(context, payload.code, payload.state, repository)]


@router.post("/accounts/{account_id}/check", response_model=SocialAccountView)
async def check(account_id: str, context: TenantContext = Depends(tenant_context)) -> SocialAccountView:
    """Health check: refreshes the token when it is about to expire, then asks the platform."""
    _manage(context)
    return SocialAccountView.of(await check_account(context, account_id))


@router.delete("/accounts/{account_id}", response_model=SocialAccountView)
async def disconnect(
    account_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SocialAccountView:
    """Revoke at the platform (where it offers that) and forget the tokens; queued posts go back to draft."""
    _manage(context)
    return SocialAccountView.of(await revoke_account(context, account_id, repository))


# -- preferences --------------------------------------------------------------------------------


@router.get("/preferences", response_model=MarketingPreferences)
async def get_preferences(context: TenantContext = Depends(tenant_context)) -> MarketingPreferences:
    _read(context)
    return preferences(context)


@router.put("/preferences", response_model=MarketingPreferences)
async def put_preferences(payload: PreferencesUpdate, context: TenantContext = Depends(tenant_context)) -> MarketingPreferences:
    _manage(context)
    try:
        cron.zone(payload.timezone)
    except cron.CronError:
        raise http_error(422, f"Unknown time zone {payload.timezone!r}; use a name such as Asia/Riyadh or Asia/Dubai.") from None
    from .assist import _clean_tags

    saved = MarketingPreferences(
        brand_voice=payload.brand_voice.strip(), timezone=payload.timezone,
        default_hashtags_en=_clean_tags(payload.default_hashtags_en, 30), default_hashtags_ar=_clean_tags(payload.default_hashtags_ar, 30),
        updated_by=context.user_id, updated_at=utc_now(),
    )
    with open_marketing_store(context) as store:
        return store.save_preferences(context, saved)


# -- campaigns -------------------------------------------------------------------------------------


class CampaignView(Campaign):
    counts: dict[str, int] = Field(default_factory=dict)
    auto_post_approval: Approval | None = None


def _campaign_view(campaign: Campaign, counts: dict[str, int], repository: AnumRepository, context: TenantContext) -> CampaignView:
    approval = None
    if campaign.auto_post_approval_id and not campaign.auto_post:
        found = repository.get_approval(campaign.auto_post_approval_id, context)
        if found is not None and found.status == ApprovalStatus.PENDING and (found.expires_at is None or found.expires_at > utc_now()):
            approval = found
    return CampaignView(**campaign.model_dump(), counts=counts, auto_post_approval=approval)


def _campaign_or_404(store, context: TenantContext, campaign_id: str, *, for_update: bool = False) -> Campaign:  # type: ignore[no-untyped-def]
    campaign = store.get_campaign(context, campaign_id, for_update=for_update)
    if campaign is None:
        raise http_error(404, "Campaign not found")
    return campaign


def _dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise http_error(422, "The campaign ends before it starts.")


@router.get("/campaigns", response_model=list[CampaignView])
async def list_campaigns(
    campaign_status: CampaignStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE),
    before: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[CampaignView]:
    _read(context)
    with open_marketing_store(context) as store:
        cursor = store.get_campaign(context, before) if before else None
        if before and cursor is None:
            raise http_error(400, "Unknown cursor")
        items = store.list_campaigns(context, status=campaign_status, text=(q or "").strip() or None, limit=limit, before=cursor)
        counts = {item.id: store.count_posts(context, item.id) for item in items}
    return [_campaign_view(item, counts[item.id], repository, context) for item in items]


@router.post("/campaigns", response_model=CampaignView, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CampaignView:
    _write(context)
    _dates(payload.start_date, payload.end_date)
    now = utc_now()
    campaign = Campaign(id=new_id("mkcamp"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_by=context.user_id, created_at=now, updated_at=now, **payload.model_dump())
    if not campaign.utm_campaign:
        campaign.utm_campaign = "".join(char if char.isalnum() else "_" for char in campaign.name.lower())[:60].strip("_") or "campaign"
    with open_marketing_store(context) as store:
        store.add_campaign(campaign)
    audit(repository, context, "marketing.campaign.created", campaign.id, metadata={"name": campaign.name})
    return _campaign_view(campaign, {}, repository, context)


@router.get("/campaigns/{campaign_id}", response_model=CampaignView)
async def get_campaign(campaign_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> CampaignView:
    _read(context)
    with open_marketing_store(context) as store:
        campaign = _campaign_or_404(store, context, campaign_id)
        counts = store.count_posts(context, campaign_id)
    return _campaign_view(campaign, counts, repository, context)


@router.put("/campaigns/{campaign_id}", response_model=CampaignView)
async def update_campaign(
    campaign_id: str,
    payload: CampaignUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CampaignView:
    _write(context)
    with open_marketing_store(context) as store:
        campaign = _campaign_or_404(store, context, campaign_id, for_update=True)
        changes = payload.model_dump(exclude_unset=True)
        for name, value in changes.items():
            setattr(campaign, name, value)
        _dates(campaign.start_date, campaign.end_date)
        if campaign.status != CampaignStatus.ACTIVE and campaign.auto_post:
            # Pausing or ending a campaign also stops its auto-post.
            campaign.auto_post = False
        campaign.updated_at = utc_now()
        store.save_campaign(campaign)
        counts = store.count_posts(context, campaign_id)
    audit(repository, context, "marketing.campaign.updated", campaign_id, metadata={"fields": sorted(changes)})
    return _campaign_view(campaign, counts, repository, context)


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete a campaign; its posts stay (without a campaign). Refused while posts are queued."""
    _write(context)
    with open_marketing_store(context) as store:
        _campaign_or_404(store, context, campaign_id)
        counts = store.count_posts(context, campaign_id)
        queued = sum(counts.get(state.value, 0) for state in (PostStatus.SCHEDULED, PostStatus.PENDING_APPROVAL, PostStatus.PUBLISHING))
        if queued:
            raise http_error(409, f"{queued} posts of this campaign are scheduled or waiting for approval. Cancel them first, or archive the campaign.")
        store.delete_campaign(context, campaign_id)
    audit(repository, context, "marketing.campaign.deleted", campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/campaigns/{campaign_id}/auto-post", response_model=CampaignView)
async def set_auto_post(
    campaign_id: str,
    payload: AutoPostRequest,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CampaignView:
    """Turning auto-post off is immediate; turning it on asks for an owner's approval first."""
    _manage(context)
    with open_marketing_store(context) as store:
        campaign = _campaign_or_404(store, context, campaign_id, for_update=True)
        if not payload.enabled:
            campaign.auto_post = False
            campaign.updated_at = utc_now()
            store.save_campaign(campaign)
            previous = (campaign.auto_post_task_id, campaign.auto_post_approval_id)
        else:
            previous = None
            if campaign.status != CampaignStatus.ACTIVE:
                raise http_error(409, "Activate the campaign first: auto-post works for active campaigns only.")
            accounts = {account.id: account for account in store.list_accounts(context)}
            chosen = list(dict.fromkeys(payload.account_ids))
            if not chosen:
                raise http_error(422, "Choose the accounts this campaign may post to without asking.")
            for account_id in chosen:
                account = accounts.get(account_id)
                if account is None or account.status != AccountStatus.CONNECTED:
                    raise http_error(422, "Every chosen account must be connected to this workspace.")
        counts = store.count_posts(context, campaign_id)
    if previous is not None:
        retire_approval(repository, context, *previous)
        audit(repository, context, "marketing.campaign.auto_post_disabled", campaign_id)
        return _campaign_view(campaign, counts, repository, context)
    retire_approval(repository, context, campaign.auto_post_task_id, campaign.auto_post_approval_id)
    names = [f"{accounts[item].display_name} ({accounts[item].platform.value})" for item in chosen]
    task, approval = request_approval(
        repository, context, tool=AUTO_POST_TOOL,
        arguments={"campaign_id": campaign.id, "campaign": campaign.name, "account_ids": chosen, "accounts": names, "until": campaign.end_date.isoformat() if campaign.end_date else None},
        title=f"Auto-post for campaign {campaign.name}",
        prompt=f"Let campaign {campaign.name} publish its scheduled posts to {', '.join(names)} without a per-post approval.",
        metadata={"marketing_campaign_id": campaign.id},
    )
    with open_marketing_store(context) as store:
        current = _campaign_or_404(store, context, campaign_id, for_update=True)
        current.auto_post_approval_id, current.auto_post_task_id = approval.id, task.id
        current.updated_at = utc_now()
        store.save_campaign(current)
    audit(repository, context, "marketing.campaign.auto_post_requested", campaign_id, metadata={"approval_id": approval.id, "accounts": chosen})
    return _campaign_view(current, counts, repository, context)


# -- posts --------------------------------------------------------------------------------------------


@router.get("/posts", response_model=list[PostView])
async def list_posts(
    post_status: list[PostStatus] = Query(default_factory=list, alias="status"),
    campaign_id: str | None = Query(default=None, max_length=80),
    platform: Platform | None = None,
    account_id: str | None = Query(default=None, max_length=80),
    group_id: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=200),
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=30, ge=1, le=MAX_RANGE),
    before: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[PostView]:
    """Newest first (``before`` pages), or a calendar window (``from``/``to``, by time)."""
    _read(context)
    calendar = start is not None or end is not None
    if calendar and start and end and (end - start) > timedelta(days=62):
        raise http_error(422, "Ask for at most 62 days at a time.")
    with open_marketing_store(context) as store:
        cursor = store.get_post(context, before) if before else None
        if before and cursor is None:
            raise http_error(400, "Unknown cursor")
        posts = store.list_posts(context, PostQuery(
            statuses=set(post_status), campaign_id=campaign_id, platform=platform, account_id=account_id, group_id=group_id,
            text=(q or "").strip() or None, start=start, end=end, limit=limit if calendar else min(limit, MAX_PAGE), before=cursor,
            order="schedule" if calendar else "recent",
        ))
        result = views(context, store, posts, repository)
    for item in result:
        if publisher.stale(item):
            item.status = PostStatus.FAILED
            item.last_error = PlatformError(code="publish_interrupted", message="Publishing was interrupted; the post may or may not be live.", fix="Check the platform. If it is not there, retry.")
    return result


def _view_one(context: TenantContext, post_id: str, repository: AnumRepository) -> PostView:
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id)
        return views(context, store, [post], repository)[0]


@router.post("/posts", response_model=list[PostView], status_code=status.HTTP_201_CREATED)
async def create_posts(
    payload: PostCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[PostView]:
    """One post per variant (account and language), grouped so the composer edits them together."""
    _write(context)
    media = {item.file_id: item for variant in payload.variants for item in variant.media}
    refs = {ref.file_id: ref for ref in resolve_media(context, list(media.values()))} if media else {}
    now = utc_now()
    group = new_id("mkgroup")
    created: list[Post] = []
    with open_marketing_store(context) as store:
        if payload.campaign_id:
            _campaign_or_404(store, context, payload.campaign_id)
        for variant in payload.variants:
            account = connected_account(store, context, variant.account_id)
            post = Post(
                id=new_id("mkpost"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, campaign_id=payload.campaign_id,
                group_id=group, account_id=account.id, platform=account.platform, language=variant.language, text=variant.text.strip(),
                title=variant.title.strip(), hashtags=variant.hashtags, link_url=(variant.link_url or "").strip() or None,
                media=[refs[item.file_id] for item in variant.media], scheduled_at=payload.scheduled_at,
                ai_generated=payload.ai_generated, untrusted_source=payload.untrusted_source, created_by=context.user_id,
                created_at=now, updated_at=now,
            )
            store.add_post(post)
            created.append(post)
        result = views(context, store, created, repository)
    audit(repository, context, "marketing.post.created", group, metadata={"posts": [post.id for post in created], "platforms": sorted({post.platform.value for post in created})})
    return result


@router.post("/posts/validate", response_model=Validation)
async def validate_post(payload: PostValidate, context: TenantContext = Depends(tenant_context)) -> Validation:
    """The platform's limits checked against a draft (the composer's live check)."""
    _read(context)
    refs = resolve_media(context, payload.media) if payload.media else []
    return validate(payload.platform, text=payload.text, hashtags=payload.hashtags, link_url=payload.link_url, media=refs, title=payload.title)


@router.get("/posts/{post_id}", response_model=PostView)
async def get_post(post_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PostView:
    _read(context)
    return _view_one(context, post_id, repository)


@router.put("/posts/{post_id}", response_model=PostView)
async def update_post(
    post_id: str,
    payload: PostUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PostView:
    """Edit a post that is not out yet. Changing the content of a scheduled or pending post
    takes it back to draft: the earlier approval covered the earlier content only."""
    _write(context)
    refs = resolve_media(context, payload.media) if payload.media else []
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status not in EDITABLE:
            raise http_error(409, f"This post is {post.status.value.replace('_', ' ')} and cannot be edited.")
        if payload.campaign_id:
            _campaign_or_404(store, context, payload.campaign_id)
        before = post_hash(post)
        post.text, post.title, post.hashtags = payload.text.strip(), payload.title.strip(), payload.hashtags
        post.link_url = (payload.link_url or "").strip() or None
        post.media = refs
        if payload.language:
            post.language = payload.language
        if payload.clear_campaign:
            post.campaign_id = None
        elif payload.campaign_id:
            post.campaign_id = payload.campaign_id
        if post_hash(post) != before and post.status in {PostStatus.SCHEDULED, PostStatus.PENDING_APPROVAL}:
            unschedule(post, repository, context)
            post.last_error = PlatformError(code="edited", message="Edited after it was scheduled, so it went back to draft.", fix="Schedule it again to ask for approval of the new content.")
        post.updated_at = utc_now()
        store.save_post(post)
        result = views(context, store, [post], repository)[0]
    audit(repository, context, "marketing.post.updated", post_id, metadata={"status": result.status.value})
    return result


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Remove a post from ANUM. A published post stays on the platform (delete it there)."""
    _write(context)
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status == PostStatus.PUBLISHING:
            raise http_error(409, "This post is being published right now. Try again in a minute.")
        retire_approval(repository, context, post.approval_task_id, post.approval_id)
        store.delete_post(context, post_id)
    audit(repository, context, "marketing.post.deleted", post_id, metadata={"status": post.status.value, "platform": post.platform.value})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/posts/{post_id}/schedule", response_model=PostView)
async def schedule(
    post_id: str,
    payload: PostSchedule,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PostView:
    """Ask to publish at ``scheduled_at`` (now when empty). Waits for an owner's approval
    unless the post's campaign has an approved auto-post for this account."""
    _write(context)
    return await schedule_post(context, post_id, payload.scheduled_at, repository)


@router.post("/posts/{post_id}/reschedule", response_model=PostView)
async def reschedule(
    post_id: str,
    payload: PostReschedule,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PostView:
    """Move a post on the calendar. The approval binds the content, not the time, so an
    approved post keeps its approval; a past time is refused."""
    _write(context)
    if payload.scheduled_at < utc_now() - timedelta(minutes=1):
        raise http_error(422, "Pick a time in the future.")
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status not in {PostStatus.DRAFT, PostStatus.PENDING_APPROVAL, PostStatus.SCHEDULED, PostStatus.FAILED, PostStatus.CANCELLED}:
            raise http_error(409, f"This post is {post.status.value.replace('_', ' ')} and cannot be moved.")
        post.scheduled_at = payload.scheduled_at
        post.next_attempt_at = None
        if post.status == PostStatus.SCHEDULED and post.authorised_by == "auto_post":
            campaign = store.get_campaign(context, post.campaign_id) if post.campaign_id else None
            if not auto_post_covers(campaign, post, utc_now()):
                unschedule(post, repository, context)
                post.last_error = PlatformError(code="auto_post_window", message="The new time is outside the campaign's auto-post.", fix="Schedule it again to ask for approval.")
        post.updated_at = utc_now()
        store.save_post(post)
        result = views(context, store, [post], repository)[0]
    audit(repository, context, "marketing.post.rescheduled", post_id, metadata={"scheduled_at": payload.scheduled_at.isoformat()})
    return result


@router.post("/posts/{post_id}/cancel", response_model=PostView)
async def cancel(post_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PostView:
    """Stop a scheduled or pending post; it stays as a cancelled post that can be scheduled again."""
    _write(context)
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status not in {PostStatus.SCHEDULED, PostStatus.PENDING_APPROVAL}:
            raise http_error(409, "Only scheduled posts or posts waiting for approval can be cancelled.")
        unschedule(post, repository, context, reason=PostStatus.CANCELLED)
        post.updated_at = utc_now()
        store.save_post(post)
        result = views(context, store, [post], repository)[0]
    audit(repository, context, "marketing.post.cancelled", post_id)
    return result


@router.post("/posts/{post_id}/publish", response_model=PostView)
async def publish_now(post_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PostView:
    """Publish an already approved (scheduled) post right away instead of at its time."""
    _write(context)
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status != PostStatus.SCHEDULED or not post.approved_hash:
            raise http_error(409, "Only approved, scheduled posts can be published now. Schedule it first to ask for approval.")
        post.scheduled_at = utc_now()
        post.next_attempt_at = None
        store.save_post(post)
    await publisher.publish(context, post_id)
    return _view_one(context, post_id, repository)


@router.post("/posts/{post_id}/retry", response_model=PostView)
async def retry(post_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PostView:
    """Try a failed post again. The approval still covers it if the content is unchanged;
    otherwise it has to be scheduled (and approved) again."""
    _write(context)
    with open_marketing_store(context) as store:
        post = get_post_or_404(store, context, post_id, for_update=True)
        if post.status != PostStatus.FAILED and not publisher.stale(post):
            raise http_error(409, "Only failed posts can be retried.")
        if not post.approved_hash or post.approved_hash != post_hash(post):
            raise http_error(409, "The approval no longer covers this post. Schedule it again to ask for approval.")
        post.status = PostStatus.SCHEDULED
        post.scheduled_at = utc_now()
        post.next_attempt_at = None
        post.attempts = 0
        store.save_post(post)
    audit(repository, context, "marketing.post.retried", post_id)
    await publisher.publish(context, post_id)
    return _view_one(context, post_id, repository)


# -- media ------------------------------------------------------------------------------------------


class MediaFile(BaseModel):
    id: str
    name: str
    content_type: str
    kind: Literal["image", "video"]
    size_bytes: int
    created_at: datetime


@router.get("/media", response_model=list[MediaFile])
async def list_media(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    context: TenantContext = Depends(tenant_context),
) -> list[MediaFile]:
    """Images and videos from the workspace's Files (upload new ones through ``/api/v1/files``)."""
    _read(context)
    from ..files import open_file_metadata_store

    with open_file_metadata_store(context) as files:
        records = files.list_files(context, 500)
    needle = (q or "").strip().lower()
    result = [
        MediaFile(id=record.id, name=record.name, content_type=record.content_type, kind="image" if record.content_type.startswith("image/") else "video", size_bytes=record.size_bytes, created_at=record.created_at)
        for record in records
        if record.content_type.startswith(("image/", "video/")) and (not needle or needle in record.name.lower())
    ]
    result.sort(key=lambda item: item.created_at, reverse=True)
    return result[:limit]


@router.get("/media/{link}", include_in_schema=True)
async def public_media(link: str) -> Response:
    """A file behind a signed, expiring link, for platforms that fetch media by URL (Instagram).

    No sign-in: the link itself is the credential (encrypted with ``ANUM_SECRETS_KEY``,
    naming one file of one workspace, valid for ``ANUM_MARKETING_MEDIA_LINK_SECONDS``)."""
    from ..files import file_store, open_file_metadata_store

    if len(link) > 1000:
        raise http_error(404, "Not found")
    context, file_id = read_media_link(link)
    with open_file_metadata_store(context) as files:
        record = files.get(context, file_id)
    if record is None or not record.content_type.startswith(("image/", "video/")):
        raise http_error(404, "Not found")
    try:
        content = file_store.storage.get(record.storage_key)
    except FileNotFoundError:
        raise http_error(404, "Not found") from None
    return Response(content, media_type=record.content_type, headers={"Cache-Control": "private, max-age=300"})


class UtmRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    platform: Platform
    campaign: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=100)


class UtmLink(BaseModel):
    url: str


@router.post("/utm", response_model=UtmLink)
async def utm(payload: UtmRequest, context: TenantContext = Depends(tenant_context)) -> UtmLink:
    """The link with campaign tracking (store links keep attribution in their own parameters)."""
    _read(context)
    try:
        return UtmLink(url=with_utm(payload.url, source=payload.platform.value, campaign=payload.campaign, content=payload.content))
    except ValueError as exc:
        raise http_error(422, str(exc)) from None


# -- AI assist ---------------------------------------------------------------------------------------


@router.post("/assist/ideas", response_model=assist.IdeasResult)
async def assist_ideas(payload: AssistIdeas, context: TenantContext = Depends(tenant_context)) -> assist.IdeasResult:
    """Post ideas with English and Arabic copy and hashtags, through the budgeted model gateway."""
    _write(context)
    prefs = preferences(context)
    if payload.campaign_id:
        with open_marketing_store(context) as store:
            campaign = _campaign_or_404(store, context, payload.campaign_id)
        payload = payload.model_copy(update={
            "brief": payload.brief or campaign.brief, "product": payload.product or campaign.product,
        })
    if not (payload.brief.strip() or payload.product.strip() or payload.feature.strip()):
        raise http_error(422, "Describe what to promote: a brief, a product or a feature.")
    return await assist.ideas(
        context, payload, brand_voice=prefs.brand_voice,
        default_tags={Language.EN: prefs.default_hashtags_en, Language.AR: prefs.default_hashtags_ar}, tz=prefs.timezone,
    )


@router.post("/assist/hashtags", response_model=assist.HashtagResult)
async def assist_hashtags(payload: AssistHashtags, context: TenantContext = Depends(tenant_context)) -> assist.HashtagResult:
    _write(context)
    prefs = preferences(context)
    return await assist.hashtags(context, payload, defaults=prefs.default_hashtags_ar if payload.language == Language.AR else prefs.default_hashtags_en)


# -- analytics ----------------------------------------------------------------------------------------


@router.get("/analytics/summary", response_model=analytics.Summary)
async def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    platform: Platform | None = None,
    campaign_id: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> analytics.Summary:
    _read(context)
    end = utc_now()
    return analytics.summarize(context, start=end - timedelta(days=days), end=end, platform=platform, campaign_id=campaign_id, tz=preferences(context).timezone)


class RefreshRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
    campaign_id: str | None = Field(default=None, max_length=80)


@router.post("/analytics/refresh")
async def analytics_refresh(payload: RefreshRequest, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Pull reach, impressions, engagement and clicks from each platform for recent posts."""
    _write(context)
    result = await refresh_metrics(context, days=payload.days, campaign_id=payload.campaign_id)
    return result.model_dump(mode="json")


@router.get("/analytics/best-times", response_model=list[analytics.TimeSlot])
async def analytics_best_times(platform: Platform | None = None, context: TenantContext = Depends(tenant_context)) -> list[analytics.TimeSlot]:
    _read(context)
    return analytics.best_times(context, platform=platform, tz=preferences(context).timezone)


@router.get("/reports/weekly", response_model=analytics.WeeklyReport)
async def weekly_report(
    week_start: date | None = None,
    language: Language = Language.EN,
    context: TenantContext = Depends(tenant_context),
) -> analytics.WeeklyReport:
    """The weekly report (last full week from Sunday by default, the Gulf work week)."""
    _read(context)
    tz = preferences(context).timezone
    if week_start is None:
        today = utc_now().astimezone(analytics._zone(tz)).date()
        week_start = today - timedelta(days=(today.weekday() + 1) % 7 + 7)
    return analytics.weekly_report(context, week_start=week_start, tz=tz, arabic=language == Language.AR)


@router.post("/reports/weekly/deliver", response_model=report_delivery.DeliveryResult)
async def deliver_weekly_report(
    payload: report_delivery.DeliveryRequest,
    week_start: date | None = None,
    language: Language = Language.EN,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> report_delivery.DeliveryResult:
    """Ask to send the weekly report by email and/or WhatsApp. Each channel waits for approval."""
    report = await weekly_report(week_start=week_start, language=language, context=context)
    return await report_delivery.deliver(context, repository, report, payload)


class AssistantAsk(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class AssistantAnswer(BaseModel):
    text: str
    platform: Platform | None
    days: int


@router.post("/assistant/summary", response_model=AssistantAnswer)
async def assistant_summary(payload: AssistantAsk, context: TenantContext = Depends(tenant_context)) -> AssistantAnswer:
    """``marketing_summary`` for chat and voice clients: "how did last week's Instagram posts do?"."""
    _read(context)
    from .tools import days_in, is_arabic, platform_in, summary_for

    platform = platform_in(payload.text)
    days = days_in(payload.text)
    return AssistantAnswer(text=summary_for(context, platform=platform, days=days, arabic=is_arabic(payload.text)), platform=platform, days=days)
