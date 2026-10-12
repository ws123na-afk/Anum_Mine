"""Marketing records and request bodies (docs/marketing.md)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Platform(StrEnum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    X = "x"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"


class Provider(StrEnum):
    """The OAuth app an account was connected through (Meta covers Facebook and Instagram)."""

    META = "meta"
    LINKEDIN = "linkedin"
    X = "x"
    TIKTOK = "tiktok"
    GOOGLE = "google"


PROVIDER_PLATFORMS: dict[Provider, tuple[Platform, ...]] = {
    Provider.META: (Platform.FACEBOOK, Platform.INSTAGRAM),
    Provider.LINKEDIN: (Platform.LINKEDIN,),
    Provider.X: (Platform.X,),
    Provider.TIKTOK: (Platform.TIKTOK,),
    Provider.GOOGLE: (Platform.YOUTUBE,),
}
PLATFORM_PROVIDER: dict[Platform, Provider] = {
    platform: provider for provider, platforms in PROVIDER_PLATFORMS.items() for platform in platforms
}


class AccountStatus(StrEnum):
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"
    ERROR = "error"
    REVOKED = "revoked"


class Language(StrEnum):
    EN = "en"
    AR = "ar"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class PostStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    # TikTok before its app audit: the video is in the creator's inbox to finish in TikTok.
    SENT_TO_INBOX = "sent_to_inbox"
    FAILED = "failed"
    CANCELLED = "cancelled"


# A post whose content can still change (anything later is out of the planner's hands).
EDITABLE = frozenset({PostStatus.DRAFT, PostStatus.PENDING_APPROVAL, PostStatus.SCHEDULED, PostStatus.FAILED, PostStatus.CANCELLED})
DONE = frozenset({PostStatus.PUBLISHED, PostStatus.SENT_TO_INBOX})


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class MediaRef(BaseModel):
    """A file from the workspace's Files used by a post (the bytes stay in Files)."""

    file_id: str = Field(min_length=1, max_length=80)
    kind: MediaKind
    name: str = ""
    content_type: str = ""
    size_bytes: int = 0
    width: int | None = Field(default=None, ge=1, le=20000)
    height: int | None = Field(default=None, ge=1, le=20000)
    duration_seconds: float | None = Field(default=None, ge=0, le=36000)


class PlatformError(BaseModel):
    """What a platform refused and how to fix it, in words for the owner."""

    code: str
    message: str
    fix: str
    retryable: bool = False
    status: int | None = None


class PostMetrics(BaseModel):
    impressions: int | None = None
    reach: int | None = None
    engagement: int | None = None
    clicks: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    views: int | None = None
    # Why a number is missing (the platform does not report it for this kind of post).
    note: str | None = None


class SocialAccount(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    platform: Platform
    provider: Provider
    external_id: str
    display_name: str
    handle: str | None = None
    status: AccountStatus = AccountStatus.CONNECTED
    scopes: list[str] = Field(default_factory=list)
    # Never returned by the API: the view model below drops them.
    access_token_ciphertext: str | None = None
    refresh_token_ciphertext: str | None = None
    token_expires_at: datetime | None = None
    # Platform capabilities learned at connect or check time (TikTok direct post, limits).
    details: dict[str, Any] = Field(default_factory=dict)
    last_checked_at: datetime | None = None
    last_error: PlatformError | None = None
    connected_by: str
    created_at: datetime
    updated_at: datetime


class SocialAccountView(BaseModel):
    id: str
    platform: Platform
    provider: Provider
    external_id: str
    display_name: str
    handle: str | None
    status: AccountStatus
    scopes: list[str]
    token_expires_at: datetime | None
    details: dict[str, Any]
    last_checked_at: datetime | None
    last_error: PlatformError | None
    connected_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, account: SocialAccount) -> SocialAccountView:
        return cls(**account.model_dump(exclude={"access_token_ciphertext", "refresh_token_ciphertext", "tenant_id", "workspace_id"}))


class Campaign(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    objective: str = ""
    product: str = ""
    brief: str = ""
    languages: list[Language] = Field(default_factory=lambda: [Language.EN, Language.AR])
    platforms: list[Platform] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    status: CampaignStatus = CampaignStatus.DRAFT
    utm_campaign: str = ""
    # Auto-post (owner, after an approval): approved posts of this campaign publish at
    # their time without a per-post approval, to these accounts only.
    auto_post: bool = False
    auto_post_accounts: list[str] = Field(default_factory=list)
    auto_post_approved_by: str | None = None
    auto_post_enabled_at: datetime | None = None
    auto_post_approval_id: str | None = None
    auto_post_task_id: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class Post(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    campaign_id: str | None = None
    group_id: str
    account_id: str
    platform: Platform
    language: Language = Language.EN
    status: PostStatus = PostStatus.DRAFT
    text: str = ""
    title: str = ""
    hashtags: list[str] = Field(default_factory=list)
    link_url: str | None = None
    media: list[MediaRef] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    next_attempt_at: datetime | None = None
    published_at: datetime | None = None
    external_id: str | None = None
    external_url: str | None = None
    attempts: int = 0
    last_error: PlatformError | None = None
    approval_id: str | None = None
    approval_task_id: str | None = None
    approved_hash: str | None = None
    approved_by: str | None = None
    # How the publish was authorised: a per-post approval or the campaign's auto-post.
    authorised_by: Literal["approval", "auto_post"] | None = None
    metrics: PostMetrics | None = None
    metrics_updated_at: datetime | None = None
    ai_generated: bool = False
    # The copy was built from external data (a listing, a web page): review it before posting.
    untrusted_source: bool = False
    created_by: str
    created_at: datetime
    updated_at: datetime


class MarketingPreferences(BaseModel):
    brand_voice: str = ""
    timezone: str = "Asia/Riyadh"
    default_hashtags_en: list[str] = Field(default_factory=list)
    default_hashtags_ar: list[str] = Field(default_factory=list)
    updated_by: str | None = None
    updated_at: datetime | None = None


# -- request bodies -------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthStart(_Body):
    provider: Provider


class OAuthCallback(_Body):
    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=10, max_length=4000)


class CampaignCreate(_Body):
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(default="", max_length=500)
    product: str = Field(default="", max_length=200)
    brief: str = Field(default="", max_length=4000)
    languages: list[Language] = Field(default_factory=lambda: [Language.EN, Language.AR], min_length=1, max_length=2)
    platforms: list[Platform] = Field(default_factory=list, max_length=6)
    start_date: date | None = None
    end_date: date | None = None
    status: CampaignStatus = CampaignStatus.DRAFT
    utm_campaign: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_.\-]*$")


class CampaignUpdate(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    objective: str | None = Field(default=None, max_length=500)
    product: str | None = Field(default=None, max_length=200)
    brief: str | None = Field(default=None, max_length=4000)
    languages: list[Language] | None = Field(default=None, min_length=1, max_length=2)
    platforms: list[Platform] | None = Field(default=None, max_length=6)
    start_date: date | None = None
    end_date: date | None = None
    status: CampaignStatus | None = None
    utm_campaign: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_.\-]*$")


class AutoPostRequest(_Body):
    enabled: bool
    account_ids: list[str] = Field(default_factory=list, max_length=20)


class MediaInput(_Body):
    file_id: str = Field(min_length=1, max_length=80)
    # Measured by the client for video (the API reads image sizes itself).
    width: int | None = Field(default=None, ge=1, le=20000)
    height: int | None = Field(default=None, ge=1, le=20000)
    duration_seconds: float | None = Field(default=None, ge=0, le=36000)


class PostContent(_Body):
    text: str = Field(default="", max_length=70000)
    title: str = Field(default="", max_length=200)
    hashtags: list[str] = Field(default_factory=list, max_length=60)
    link_url: str | None = Field(default=None, max_length=2000)
    media: list[MediaInput] = Field(default_factory=list, max_length=20)

    @field_validator("hashtags")
    @classmethod
    def _clean_hashtags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            tag = value.strip().lstrip("#").strip()
            if tag and tag not in cleaned:
                cleaned.append(tag[:100])
        return cleaned


class PostVariant(PostContent):
    account_id: str = Field(min_length=1, max_length=80)
    language: Language = Language.EN


class PostCreate(_Body):
    campaign_id: str | None = Field(default=None, max_length=80)
    scheduled_at: datetime | None = None
    ai_generated: bool = False
    untrusted_source: bool = False
    variants: list[PostVariant] = Field(min_length=1, max_length=12)


class PostUpdate(PostContent):
    language: Language | None = None
    campaign_id: str | None = Field(default=None, max_length=80)
    clear_campaign: bool = False


class PostSchedule(_Body):
    scheduled_at: datetime | None = None


class PostReschedule(_Body):
    scheduled_at: datetime


class PostValidate(PostContent):
    platform: Platform


class PreferencesUpdate(_Body):
    brand_voice: str = Field(default="", max_length=2000)
    timezone: str = Field(default="Asia/Riyadh", min_length=1, max_length=80)
    default_hashtags_en: list[str] = Field(default_factory=list, max_length=30)
    default_hashtags_ar: list[str] = Field(default_factory=list, max_length=30)


class AssistIdeas(_Body):
    brief: str = Field(default="", max_length=4000)
    product: str = Field(default="", max_length=200)
    feature: str = Field(default="", max_length=500)
    campaign_id: str | None = Field(default=None, max_length=80)
    platforms: list[Platform] = Field(default_factory=lambda: [Platform.INSTAGRAM], min_length=1, max_length=6)
    languages: list[Language] = Field(default_factory=lambda: [Language.EN, Language.AR], min_length=1, max_length=2)
    count: int = Field(default=3, ge=1, le=6)
    # Text from outside the workspace (a store listing, a web page, a competitor post):
    # passed to the model as labelled untrusted data and flagged on the result.
    source_text: str = Field(default="", max_length=8000)
    source_label: str = Field(default="", max_length=120)


class AssistHashtags(_Body):
    text: str = Field(min_length=1, max_length=5000)
    platform: Platform
    language: Language = Language.EN
