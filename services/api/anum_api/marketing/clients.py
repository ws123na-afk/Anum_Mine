"""The platforms' official APIs: OAuth, publishing, health and post metrics (docs/marketing.md).

One connector per OAuth provider (Meta covers Facebook Pages and Instagram professional
accounts). Every call goes through ``_call``, which turns a refusal into a
``PlatformError`` that says what failed and how to fix it, and marks network failures,
rate limits and server errors as retryable. Tokens travel in ``Authorization`` headers
or form bodies, never in URLs, and are never logged.

Tests replace ``config.platform_transport`` with an ``httpx.MockTransport``; nothing
here reaches a platform in CI.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from ..schemas import utc_now
from . import config
from .config import marketing_settings, secret
from .models import MediaKind, MediaRef, Platform, PlatformError, PostMetrics, PostStatus, Provider, SocialAccount

# Seconds between status polls while a platform processes uploaded media (tests set 0).
POLL_SECONDS = 2.0
POLL_ATTEMPTS = 30

PLATFORM_LABEL = {
    Platform.FACEBOOK: "Facebook",
    Platform.INSTAGRAM: "Instagram",
    Platform.LINKEDIN: "LinkedIn",
    Platform.X: "X",
    Platform.TIKTOK: "TikTok",
    Platform.YOUTUBE: "YouTube",
}
PROVIDER_LABEL = {
    Provider.META: "Meta (Facebook and Instagram)",
    Provider.LINKEDIN: "LinkedIn",
    Provider.X: "X",
    Provider.TIKTOK: "TikTok",
    Provider.GOOGLE: "YouTube (Google)",
}


class PlatformCallError(Exception):
    def __init__(self, error: PlatformError) -> None:
        self.error = error
        super().__init__(error.message)


def platform_error(code: str, message: str, fix: str, *, retryable: bool = False, status: int | None = None) -> PlatformCallError:
    return PlatformCallError(PlatformError(code=code, message=message, fix=fix, retryable=retryable, status=status))


@dataclass
class DiscoveredAccount:
    platform: Platform
    external_id: str
    display_name: str
    handle: str | None
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None


@dataclass
class MediaPayload:
    ref: MediaRef
    content: bytes
    # A signed HTTPS link to the bytes (Instagram fetches media by URL); None when unset.
    public_url: str | None = None


@dataclass
class PublishResult:
    external_id: str
    external_url: str | None
    status: PostStatus = PostStatus.PUBLISHED
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishRequest:
    account: SocialAccount
    access_token: str
    text: str
    title: str
    hashtags: list[str]
    link_url: str | None
    media: list[MediaPayload]
    language: str = "en"


# -- shared HTTP ----------------------------------------------------------------------------


def _expires(seconds: Any) -> datetime | None:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return None
    return utc_now() + timedelta(seconds=value) if value > 0 else None


def _message(body: Any) -> tuple[str, str]:
    """The platform's own error code and message from its error body, when it has one."""
    if not isinstance(body, dict):
        return "", ""
    error = body.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("error_subcode") or error.get("status") or "")
        return code, str(error.get("message") or error.get("error_user_msg") or "")
    if isinstance(error, str):
        return error, str(body.get("error_description") or body.get("message") or "")
    if isinstance(body.get("errors"), list) and body["errors"]:
        first = body["errors"][0] if isinstance(body["errors"][0], dict) else {}
        return str(first.get("code") or first.get("type") or body.get("title") or ""), str(first.get("message") or first.get("detail") or body.get("detail") or "")
    return str(body.get("serviceErrorCode") or body.get("code") or body.get("title") or ""), str(body.get("message") or body.get("detail") or "")


def map_error(platform: Platform | None, status: int, body: Any) -> PlatformError:
    """A refusal in owner-facing words: what failed, and what to do about it."""
    label = PLATFORM_LABEL.get(platform, "The platform") if platform else "The platform"
    code, detail = _message(body)
    detail = detail[:300]
    lowered = f"{code} {detail}".lower()
    said = f" {label} said: {detail}" if detail else ""
    if platform == Platform.X and (status == 402 or "client-not-enrolled" in lowered or "subset of x api" in lowered or "creditsdepleted" in lowered):
        return PlatformError(
            code="x_access_tier",
            message=f"X refused the post: the app's API access tier does not allow posting.{said}",
            fix="X's API posting needs a paid access tier for the project (Basic or higher, or pay-per-use credits). Upgrade it in the X Developer Portal, then try again.",
            status=status,
        )
    if platform == Platform.TIKTOK and "unaudited_client" in lowered:
        return PlatformError(
            code="tiktok_unaudited",
            message=f"TikTok only lets unaudited apps post privately.{said}",
            fix="Until TikTok's app audit approves Direct Post, keep ANUM_MARKETING_TIKTOK_DIRECT_POST off: ANUM then uploads the video to the creator's TikTok inbox as a draft.",
            status=status,
        )
    if platform == Platform.TIKTOK and "spam_risk" in lowered:
        return PlatformError(
            code="rate_limited",
            message=f"TikTok limited posting for this account for now.{said}",
            fix="TikTok caps posts per creator per day. ANUM tries again later; space posts out.",
            retryable=True,
            status=status,
        )
    if platform == Platform.YOUTUBE and ("quotaexceeded" in lowered or "quota" in lowered and status == 403):
        return PlatformError(
            code="youtube_quota",
            message="The YouTube Data API daily quota is used up (an upload costs 1,600 of the default 10,000 units).",
            fix="Try again after midnight Pacific time, or request a higher quota for the Google Cloud project.",
            status=status,
        )
    if platform == Platform.INSTAGRAM and ("2207052" in lowered or "9004" in lowered or "media download" in lowered or "could not be fetched" in lowered):
        return PlatformError(
            code="media_fetch",
            message=f"Instagram could not download the media.{said}",
            fix="Instagram fetches media from the API's public address: set ANUM_MARKETING_PUBLIC_API_URL to the API's public HTTPS origin, and use JPEG images.",
            status=status,
        )
    if status == 401 or code in {"190", "102", "access_token_invalid", "invalid_token", "65", "401"} or "expired" in lowered and "token" in lowered:
        return PlatformError(
            code="auth_expired",
            message=f"The {label} connection expired or was revoked.{said}",
            fix="Reconnect the account on the Marketing page (Accounts > Reconnect).",
            status=status,
        )
    if status == 403 or code in {"10", "200", "3", "permission_denied", "scope_not_authorized", "access_denied"}:
        return PlatformError(
            code="permission",
            message=f"{label} refused: the connection is missing a permission.{said}",
            fix="Reconnect and grant every permission ANUM asks for. Some permissions need the platform's app review first (see docs/marketing.md, Platform apps).",
            status=status,
        )
    if status == 429 or code in {"4", "17", "32", "613", "rate_limit_exceeded"}:
        return PlatformError(
            code="rate_limited",
            message=f"{label} is rate limiting requests for now.",
            fix="Nothing to do: ANUM tries again with a longer wait.",
            retryable=True,
            status=status,
        )
    if status >= 500:
        return PlatformError(
            code="platform_unavailable",
            message=f"{label} had a server error ({status}).",
            fix="Nothing to do: ANUM tries again shortly. If it keeps failing, check the platform's status page.",
            retryable=True,
            status=status,
        )
    return PlatformError(
        code="rejected",
        message=f"{label} rejected the request ({status}).{said}",
        fix="Check the post against the platform's rules (length, media type and size), edit it and schedule again.",
        status=status,
    )


async def _call(client: httpx.AsyncClient, platform: Platform | None, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise platform_error("timeout", f"{PLATFORM_LABEL.get(platform, 'The platform') if platform else 'The platform'} did not answer in time.", "Nothing to do: ANUM tries again shortly.", retryable=True) from exc
    except httpx.TransportError as exc:
        raise platform_error("network", "The platform could not be reached.", "Check the API server's outbound internet access; ANUM tries again shortly.", retryable=True) from exc
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = None
        raise PlatformCallError(map_error(platform, response.status_code, body))
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _bearer(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _poll(check, attempts: int | None = None):  # type: ignore[no-untyped-def]
    """Call ``check`` until it returns a value (not None), waiting ``POLL_SECONDS`` between tries."""
    for attempt in range(attempts or POLL_ATTEMPTS):
        result = await check()
        if result is not None:
            return result
        if attempt + 1 < (attempts or POLL_ATTEMPTS):
            await asyncio.sleep(POLL_SECONDS)
    return None


# -- connectors -----------------------------------------------------------------------------


class Connector:
    provider: Provider
    uses_pkce = False

    def missing_settings(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def authorize_url(self, state: str, code_challenge: str | None) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:  # pragma: no cover
        raise NotImplementedError

    async def refresh(self, refresh_token: str) -> TokenSet | None:
        return None

    async def revoke(self, account: SocialAccount, access_token: str) -> None:
        return None

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def publish(self, request: PublishRequest) -> PublishResult:  # pragma: no cover
        raise NotImplementedError

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def redirect_url() -> str:
        return marketing_settings.oauth_redirect_url.strip()


def _missing(pairs: list[tuple[str, str]]) -> list[str]:
    problems = [name for name, value in pairs if not value]
    redirect = marketing_settings.oauth_redirect_url.strip()
    if not redirect.startswith("https://") and not redirect.startswith("http://localhost") and not redirect.startswith("http://127.0.0.1"):
        problems.append("ANUM_MARKETING_OAUTH_REDIRECT_URL")
    return problems


class MetaConnector(Connector):
    """Facebook Pages and Instagram professional accounts through one Meta app (Graph API)."""

    provider = Provider.META
    scopes = (
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "read_insights",
        "instagram_basic",
        "instagram_content_publish",
        "instagram_manage_insights",
        "business_management",
    )

    @property
    def graph(self) -> str:
        return f"https://graph.facebook.com/{marketing_settings.meta_graph_version}"

    @property
    def video_graph(self) -> str:
        return f"https://graph-video.facebook.com/{marketing_settings.meta_graph_version}"

    def missing_settings(self) -> list[str]:
        return _missing([
            ("ANUM_MARKETING_META_APP_ID", marketing_settings.meta_app_id.strip()),
            ("ANUM_MARKETING_META_APP_SECRET", secret(marketing_settings.meta_app_secret)),
        ])

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        query = urlencode({
            "client_id": marketing_settings.meta_app_id.strip(),
            "redirect_uri": self.redirect_url(),
            "state": state,
            "response_type": "code",
            "scope": ",".join(self.scopes),
        })
        return f"https://www.facebook.com/{marketing_settings.meta_graph_version}/dialog/oauth?{query}"

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:
        app = {"client_id": marketing_settings.meta_app_id.strip(), "client_secret": secret(marketing_settings.meta_app_secret)}
        async with config.http_client() as client:
            short = _json(await _call(client, Platform.FACEBOOK, "POST", f"{self.graph}/oauth/access_token", data={**app, "redirect_uri": self.redirect_url(), "code": code}))
            user_token = str(short.get("access_token") or "")
            if not user_token:
                raise platform_error("oauth_failed", "Meta did not return an access token.", "Start the connection again from the Marketing page.")
            # A long-lived user token makes the Page tokens below non-expiring.
            long = _json(await _call(client, Platform.FACEBOOK, "POST", f"{self.graph}/oauth/access_token", data={**app, "grant_type": "fb_exchange_token", "fb_exchange_token": user_token}))
            user_token = str(long.get("access_token") or user_token)
            pages = _json(await _call(
                client, Platform.FACEBOOK, "GET", f"{self.graph}/me/accounts",
                params={"fields": "id,name,access_token,tasks,instagram_business_account{id,username,name}", "limit": "100"},
                headers=_bearer(user_token),
            ))
        found: list[DiscoveredAccount] = []
        for page in pages.get("data") or []:
            page_token = str(page.get("access_token") or "")
            if not page.get("id") or not page_token:
                continue
            found.append(DiscoveredAccount(
                platform=Platform.FACEBOOK, external_id=str(page["id"]), display_name=str(page.get("name") or page["id"]),
                handle=None, access_token=page_token, scopes=list(self.scopes), details={"tasks": page.get("tasks") or []},
            ))
            instagram = page.get("instagram_business_account") or {}
            if instagram.get("id"):
                found.append(DiscoveredAccount(
                    platform=Platform.INSTAGRAM, external_id=str(instagram["id"]),
                    display_name=str(instagram.get("name") or instagram.get("username") or instagram["id"]),
                    handle=instagram.get("username"), access_token=page_token, scopes=list(self.scopes),
                    details={"page_id": str(page["id"])},
                ))
        if not found:
            raise platform_error(
                "no_accounts",
                "Meta returned no Facebook Pages for this login.",
                "Sign in with a Facebook profile that manages the Page, choose the Page (and its linked Instagram professional account) in the Meta dialog, and try again.",
            )
        return found

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:
        fields = "id,username,name" if account.platform == Platform.INSTAGRAM else "id,name"
        async with config.http_client() as client:
            body = _json(await _call(client, account.platform, "GET", f"{self.graph}/{account.external_id}", params={"fields": fields}, headers=_bearer(access_token)))
        return {"name": body.get("name") or body.get("username")}

    async def publish(self, request: PublishRequest) -> PublishResult:
        if request.account.platform == Platform.INSTAGRAM:
            return await self._publish_instagram(request)
        return await self._publish_facebook(request)

    async def _publish_facebook(self, request: PublishRequest) -> PublishResult:
        page = request.account.external_id
        headers = _bearer(request.access_token)
        images = [item for item in request.media if item.ref.kind == MediaKind.IMAGE]
        videos = [item for item in request.media if item.ref.kind == MediaKind.VIDEO]
        async with config.http_client() as client:
            if videos:
                video = videos[0]
                body = _json(await _call(
                    client, Platform.FACEBOOK, "POST", f"{self.video_graph}/{page}/videos", headers=headers,
                    data={"description": request.text, **({"title": request.title} if request.title else {})},
                    files={"source": (video.ref.name or "video.mp4", video.content, video.ref.content_type or "video/mp4")},
                ))
                video_id = str(body.get("id") or "")
                return PublishResult(external_id=video_id, external_url=f"https://www.facebook.com/{page}/videos/{video_id}")
            if len(images) == 1:
                image = images[0]
                body = _json(await _call(
                    client, Platform.FACEBOOK, "POST", f"{self.graph}/{page}/photos", headers=headers,
                    data={"caption": request.text},
                    files={"source": (image.ref.name or "image.jpg", image.content, image.ref.content_type or "image/jpeg")},
                ))
                post_id = str(body.get("post_id") or body.get("id") or "")
                return PublishResult(external_id=post_id, external_url=f"https://www.facebook.com/{post_id}")
            data: dict[str, str] = {"message": request.text}
            for index, image in enumerate(images):
                uploaded = _json(await _call(
                    client, Platform.FACEBOOK, "POST", f"{self.graph}/{page}/photos", headers=headers,
                    data={"published": "false"},
                    files={"source": (image.ref.name or f"image{index}.jpg", image.content, image.ref.content_type or "image/jpeg")},
                ))
                data[f"attached_media[{index}]"] = '{"media_fbid":"' + str(uploaded.get("id") or "") + '"}'
            if request.link_url and not images:
                data["link"] = request.link_url
            body = _json(await _call(client, Platform.FACEBOOK, "POST", f"{self.graph}/{page}/feed", headers=headers, data=data))
        post_id = str(body.get("id") or "")
        return PublishResult(external_id=post_id, external_url=f"https://www.facebook.com/{post_id}")

    async def _publish_instagram(self, request: PublishRequest) -> PublishResult:
        user = request.account.external_id
        headers = _bearer(request.access_token)
        if any(item.public_url is None for item in request.media):
            raise platform_error(
                "media_url_missing",
                "Instagram needs a public link to the media, and the API's public address is not set.",
                "Set ANUM_MARKETING_PUBLIC_API_URL to the API's public HTTPS origin and try again.",
            )
        async with config.http_client() as client:
            async def container(data: dict[str, str]) -> str:
                body = _json(await _call(client, Platform.INSTAGRAM, "POST", f"{self.graph}/{user}/media", headers=headers, data=data))
                return str(body.get("id") or "")

            async def ready(container_id: str) -> None:
                async def status() -> str | None:
                    body = _json(await _call(client, Platform.INSTAGRAM, "GET", f"{self.graph}/{container_id}", params={"fields": "status_code,status"}, headers=headers))
                    code = str(body.get("status_code") or "")
                    if code == "ERROR":
                        raise platform_error("rejected", f"Instagram could not process the media: {body.get('status') or 'unknown error'}.", "Check the video's format (MP4, H.264, AAC), length and size, then try again.")
                    return code if code in {"FINISHED", "PUBLISHED"} else None

                if await _poll(status) is None:
                    raise platform_error("processing_timeout", "Instagram was still processing the video.", "Nothing to do: ANUM tries again shortly.", retryable=True)

            media = request.media
            if len(media) == 1 and media[0].ref.kind == MediaKind.VIDEO:
                creation = await container({"media_type": "REELS", "video_url": media[0].public_url or "", "caption": request.text})
                await ready(creation)
            elif len(media) == 1:
                creation = await container({"image_url": media[0].public_url or "", "caption": request.text})
            else:
                children = [await container({"image_url": item.public_url or "", "is_carousel_item": "true"}) for item in media]
                creation = await container({"media_type": "CAROUSEL", "children": ",".join(children), "caption": request.text})
                await ready(creation)
            published = _json(await _call(client, Platform.INSTAGRAM, "POST", f"{self.graph}/{user}/media_publish", headers=headers, data={"creation_id": creation}))
            media_id = str(published.get("id") or "")
            permalink = None
            try:
                permalink = _json(await _call(client, Platform.INSTAGRAM, "GET", f"{self.graph}/{media_id}", params={"fields": "permalink"}, headers=headers)).get("permalink")
            except PlatformCallError:
                permalink = None  # published; the link is a nicety
        return PublishResult(external_id=media_id, external_url=permalink)

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:
        result: dict[str, PostMetrics] = {}
        headers = _bearer(access_token)
        async with config.http_client() as client:
            for external_id in external_ids:
                if account.platform == Platform.INSTAGRAM:
                    body = _json(await _call(client, Platform.INSTAGRAM, "GET", f"{self.graph}/{external_id}/insights", params={"metric": "reach,views,likes,comments,shares,saved,total_interactions"}, headers=headers))
                    values = {item.get("name"): _int((item.get("values") or [{}])[0].get("value")) for item in body.get("data") or []}
                    result[external_id] = PostMetrics(
                        impressions=values.get("views"), reach=values.get("reach"), views=values.get("views"),
                        engagement=values.get("total_interactions"), likes=values.get("likes"), comments=values.get("comments"),
                        shares=values.get("shares"), saves=values.get("saved"),
                        note="Instagram does not report link clicks for feed posts.",
                    )
                    continue
                counts = _json(await _call(client, Platform.FACEBOOK, "GET", f"{self.graph}/{external_id}", params={"fields": "shares,reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)"}, headers=headers))
                likes = _int(((counts.get("reactions") or {}).get("summary") or {}).get("total_count"))
                comments = _int(((counts.get("comments") or {}).get("summary") or {}).get("total_count"))
                shares = _int((counts.get("shares") or {}).get("count")) or 0
                metrics = PostMetrics(likes=likes, comments=comments, shares=shares)
                try:
                    insights = _json(await _call(client, Platform.FACEBOOK, "GET", f"{self.graph}/{external_id}/insights", params={"metric": "post_impressions,post_impressions_unique,post_clicks"}, headers=headers))
                    values = {item.get("name"): _int((item.get("values") or [{}])[0].get("value")) for item in insights.get("data") or []}
                    metrics.impressions = values.get("post_impressions")
                    metrics.reach = values.get("post_impressions_unique")
                    metrics.clicks = values.get("post_clicks")
                except PlatformCallError:
                    metrics.note = "Facebook did not return insights for this post (the Page needs read_insights)."
                metrics.engagement = sum(value or 0 for value in (likes, comments, shares, metrics.clicks))
                result[external_id] = metrics
        return result


class LinkedInConnector(Connector):
    """Member and organization (Company Page) posts through the versioned Posts API."""

    provider = Provider.LINKEDIN
    scopes = ("openid", "profile", "w_member_social", "w_organization_social", "r_organization_social", "rw_organization_admin")
    api = "https://api.linkedin.com"

    def missing_settings(self) -> list[str]:
        return _missing([
            ("ANUM_MARKETING_LINKEDIN_CLIENT_ID", marketing_settings.linkedin_client_id.strip()),
            ("ANUM_MARKETING_LINKEDIN_CLIENT_SECRET", secret(marketing_settings.linkedin_client_secret)),
        ])

    def _headers(self, token: str) -> dict[str, str]:
        return _bearer(token, **{"LinkedIn-Version": marketing_settings.linkedin_version, "X-Restli-Protocol-Version": "2.0.0"})

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        query = urlencode({
            "response_type": "code",
            "client_id": marketing_settings.linkedin_client_id.strip(),
            "redirect_uri": self.redirect_url(),
            "state": state,
            "scope": " ".join(self.scopes),
        })
        return f"https://www.linkedin.com/oauth/v2/authorization?{query}"

    def _client_form(self) -> dict[str, str]:
        return {"client_id": marketing_settings.linkedin_client_id.strip(), "client_secret": secret(marketing_settings.linkedin_client_secret)}

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:
        async with config.http_client() as client:
            token = _json(await _call(client, Platform.LINKEDIN, "POST", "https://www.linkedin.com/oauth/v2/accessToken", data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_url(), **self._client_form(),
            }))
            access = str(token.get("access_token") or "")
            if not access:
                raise platform_error("oauth_failed", "LinkedIn did not return an access token.", "Start the connection again from the Marketing page.")
            refresh = token.get("refresh_token")
            expires = _expires(token.get("expires_in"))
            granted = str(token.get("scope") or " ".join(self.scopes)).replace(",", " ").split()
            me = _json(await _call(client, Platform.LINKEDIN, "GET", f"{self.api}/v2/userinfo", headers=_bearer(access)))
            accounts = [DiscoveredAccount(
                platform=Platform.LINKEDIN, external_id=f"urn:li:person:{me.get('sub')}", display_name=str(me.get("name") or "LinkedIn member"),
                handle=None, access_token=access, refresh_token=refresh, expires_at=expires, scopes=granted, details={"kind": "member"},
            )]
            if "w_organization_social" in granted:
                acls = _json(await _call(client, Platform.LINKEDIN, "GET", f"{self.api}/rest/organizationAcls", params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"}, headers=self._headers(access)))
                for element in acls.get("elements") or []:
                    urn = str(element.get("organization") or element.get("organizationTarget") or "")
                    if not urn.startswith("urn:li:organization:"):
                        continue
                    name = urn
                    try:
                        org = _json(await _call(client, Platform.LINKEDIN, "GET", f"{self.api}/rest/organizations/{urn.rsplit(':', 1)[1]}", headers=self._headers(access)))
                        name = str(org.get("localizedName") or urn)
                    except PlatformCallError:
                        name = urn  # the post still works with the URN
                    accounts.append(DiscoveredAccount(
                        platform=Platform.LINKEDIN, external_id=urn, display_name=name, handle=None, access_token=access,
                        refresh_token=refresh, expires_at=expires, scopes=granted, details={"kind": "organization"},
                    ))
        return accounts

    async def refresh(self, refresh_token: str) -> TokenSet | None:
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.LINKEDIN, "POST", "https://www.linkedin.com/oauth/v2/accessToken", data={
                "grant_type": "refresh_token", "refresh_token": refresh_token, **self._client_form(),
            }))
        return TokenSet(str(body.get("access_token") or ""), body.get("refresh_token") or refresh_token, _expires(body.get("expires_in")))

    async def revoke(self, account: SocialAccount, access_token: str) -> None:
        async with config.http_client() as client:
            await _call(client, Platform.LINKEDIN, "POST", "https://www.linkedin.com/oauth/v2/revoke", data={"token": access_token, **self._client_form()})

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:
        async with config.http_client() as client:
            me = _json(await _call(client, Platform.LINKEDIN, "GET", f"{self.api}/v2/userinfo", headers=_bearer(access_token)))
        return {"name": me.get("name")}

    async def publish(self, request: PublishRequest) -> PublishResult:
        owner = request.account.external_id
        headers = self._headers(request.access_token)
        from .platforms import linkedin_commentary

        post: dict[str, Any] = {
            "author": owner,
            "commentary": linkedin_commentary(request.text),
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        async with config.http_client() as client:
            urns: list[str] = []
            for item in request.media:
                if item.ref.kind == MediaKind.IMAGE:
                    init = _json(await _call(client, Platform.LINKEDIN, "POST", f"{self.api}/rest/images?action=initializeUpload", headers=headers, json={"initializeUploadRequest": {"owner": owner}}))
                    value = init.get("value") or {}
                    await _call(client, Platform.LINKEDIN, "PUT", str(value.get("uploadUrl")), content=item.content, headers=_bearer(request.access_token, **{"Content-Type": item.ref.content_type or "image/jpeg"}))
                    urns.append(str(value.get("image")))
                else:
                    init = _json(await _call(client, Platform.LINKEDIN, "POST", f"{self.api}/rest/videos?action=initializeUpload", headers=headers, json={"initializeUploadRequest": {
                        "owner": owner, "fileSizeBytes": len(item.content), "uploadCaptions": False, "uploadThumbnail": False,
                    }}))
                    value = init.get("value") or {}
                    etags: list[str] = []
                    for part in value.get("uploadInstructions") or []:
                        first, last = int(part.get("firstByte", 0)), int(part.get("lastByte", len(item.content) - 1))
                        uploaded = await _call(client, Platform.LINKEDIN, "PUT", str(part.get("uploadUrl")), content=item.content[first : last + 1], headers={"Content-Type": "application/octet-stream"})
                        etags.append(uploaded.headers.get("etag", "").strip('"'))
                    await _call(client, Platform.LINKEDIN, "POST", f"{self.api}/rest/videos?action=finalizeUpload", headers=headers, json={"finalizeUploadRequest": {
                        "video": value.get("video"), "uploadToken": value.get("uploadToken", ""), "uploadedPartIds": etags,
                    }})
                    urns.append(str(value.get("video")))
            if len(urns) == 1:
                post["content"] = {"media": {"id": urns[0], **({"title": request.title} if request.title else {})}}
            elif len(urns) > 1:
                post["content"] = {"multiImage": {"images": [{"id": urn} for urn in urns]}}
            elif request.link_url:
                post["content"] = {"article": {"source": request.link_url, "title": request.title or request.link_url}}
            response = await _call(client, Platform.LINKEDIN, "POST", f"{self.api}/rest/posts", headers=headers, json=post)
        urn = response.headers.get("x-restli-id") or str(_json(response).get("id") or "")
        return PublishResult(external_id=urn, external_url=f"https://www.linkedin.com/feed/update/{urn}")

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:
        if account.details.get("kind") != "organization":
            note = "LinkedIn reports post statistics for organization pages only."
            return {external_id: PostMetrics(note=note) for external_id in external_ids}
        shares = [urn for urn in external_ids if urn.startswith("urn:li:share:")]
        ugc = [urn for urn in external_ids if urn.startswith("urn:li:ugcPost:")]
        params = {"q": "organizationalEntity", "organizationalEntity": account.external_id}
        query = urlencode(params)
        if shares:
            query += "&shares=List(" + ",".join(quote(urn, safe="") for urn in shares) + ")"
        if ugc:
            query += "&ugcPosts=List(" + ",".join(quote(urn, safe="") for urn in ugc) + ")"
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.LINKEDIN, "GET", f"{self.api}/rest/organizationalEntityShareStatistics?{query}", headers=self._headers(access_token)))
        result: dict[str, PostMetrics] = {}
        for element in body.get("elements") or []:
            urn = str(element.get("share") or element.get("ugcPost") or "")
            stats = element.get("totalShareStatistics") or {}
            likes, comments, shares_count, clicks = (_int(stats.get(key)) for key in ("likeCount", "commentCount", "shareCount", "clickCount"))
            result[urn] = PostMetrics(
                impressions=_int(stats.get("impressionCount")), reach=_int(stats.get("uniqueImpressionsCount")),
                clicks=clicks, likes=likes, comments=comments, shares=shares_count,
                engagement=sum(value or 0 for value in (likes, comments, shares_count, clicks)),
            )
        return result


class XConnector(Connector):
    """X API v2 posting with OAuth 2.0 user context (PKCE). Posting needs a paid access tier."""

    provider = Provider.X
    uses_pkce = True
    scopes = ("tweet.read", "tweet.write", "users.read", "media.write", "offline.access")
    api = "https://api.x.com"

    def missing_settings(self) -> list[str]:
        return _missing([
            ("ANUM_MARKETING_X_CLIENT_ID", marketing_settings.x_client_id.strip()),
            ("ANUM_MARKETING_X_CLIENT_SECRET", secret(marketing_settings.x_client_secret)),
        ])

    def _basic(self) -> dict[str, str]:
        raw = f"{marketing_settings.x_client_id.strip()}:{secret(marketing_settings.x_client_secret)}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        query = urlencode({
            "response_type": "code",
            "client_id": marketing_settings.x_client_id.strip(),
            "redirect_uri": self.redirect_url(),
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge or "",
            "code_challenge_method": "S256",
        })
        return f"https://x.com/i/oauth2/authorize?{query}"

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:
        async with config.http_client() as client:
            token = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/oauth2/token", headers=self._basic(), data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_url(),
                "code_verifier": code_verifier or "", "client_id": marketing_settings.x_client_id.strip(),
            }))
            access = str(token.get("access_token") or "")
            if not access:
                raise platform_error("oauth_failed", "X did not return an access token.", "Start the connection again from the Marketing page.")
            me = (_json(await _call(client, Platform.X, "GET", f"{self.api}/2/users/me", headers=_bearer(access)))).get("data") or {}
        return [DiscoveredAccount(
            platform=Platform.X, external_id=str(me.get("id") or ""), display_name=str(me.get("name") or me.get("username") or "X account"),
            handle=me.get("username"), access_token=access, refresh_token=token.get("refresh_token"),
            expires_at=_expires(token.get("expires_in")), scopes=str(token.get("scope") or "").split(),
        )]

    async def refresh(self, refresh_token: str) -> TokenSet | None:
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/oauth2/token", headers=self._basic(), data={
                "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": marketing_settings.x_client_id.strip(),
            }))
        return TokenSet(str(body.get("access_token") or ""), body.get("refresh_token") or refresh_token, _expires(body.get("expires_in")))

    async def revoke(self, account: SocialAccount, access_token: str) -> None:
        async with config.http_client() as client:
            await _call(client, Platform.X, "POST", f"{self.api}/2/oauth2/revoke", headers=self._basic(), data={"token": access_token})

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:
        async with config.http_client() as client:
            me = (_json(await _call(client, Platform.X, "GET", f"{self.api}/2/users/me", headers=_bearer(access_token)))).get("data") or {}
        return {"name": me.get("name"), "username": me.get("username")}

    async def publish(self, request: PublishRequest) -> PublishResult:
        headers = _bearer(request.access_token)
        media_ids: list[str] = []
        async with config.http_client() as client:
            for item in request.media:
                if item.ref.kind == MediaKind.IMAGE:
                    body = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/media/upload", headers=headers,
                                             data={"media_category": "tweet_image"},
                                             files={"media": (item.ref.name or "image", item.content, item.ref.content_type or "image/jpeg")}))
                    media_ids.append(str((body.get("data") or {}).get("id") or ""))
                    continue
                init = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/media/upload/initialize", headers=headers, json={
                    "media_type": item.ref.content_type or "video/mp4", "total_bytes": len(item.content), "media_category": "tweet_video",
                }))
                media_id = str((init.get("data") or {}).get("id") or "")
                chunk = 4 * 1024 * 1024
                for index, start in enumerate(range(0, len(item.content), chunk)):
                    await _call(client, Platform.X, "POST", f"{self.api}/2/media/upload/{media_id}/append", headers=headers,
                                data={"segment_index": str(index)}, files={"media": ("chunk", item.content[start : start + chunk], "application/octet-stream")})
                final = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/media/upload/{media_id}/finalize", headers=headers))
                info = (final.get("data") or {}).get("processing_info")
                if info and info.get("state") not in (None, "succeeded"):
                    async def processed() -> bool | None:
                        status = _json(await _call(client, Platform.X, "GET", f"{self.api}/2/media/upload", params={"command": "STATUS", "media_id": media_id}, headers=headers))
                        state = ((status.get("data") or {}).get("processing_info") or {}).get("state")
                        if state == "failed":
                            raise platform_error("rejected", "X could not process the video.", "Use an MP4 (H.264, AAC) up to 140 seconds and 512 MB.")
                        return True if state in (None, "succeeded") else None

                    if await _poll(processed) is None:
                        raise platform_error("processing_timeout", "X was still processing the video.", "Nothing to do: ANUM tries again shortly.", retryable=True)
                media_ids.append(media_id)
            body: dict[str, Any] = {"text": request.text}
            if media_ids:
                body["media"] = {"media_ids": media_ids}
            created = _json(await _call(client, Platform.X, "POST", f"{self.api}/2/tweets", headers=headers, json=body))
        tweet_id = str((created.get("data") or {}).get("id") or "")
        handle = request.account.handle
        url = f"https://x.com/{handle}/status/{tweet_id}" if handle else f"https://x.com/i/web/status/{tweet_id}"
        return PublishResult(external_id=tweet_id, external_url=url)

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:
        result: dict[str, PostMetrics] = {}
        async with config.http_client() as client:
            for start in range(0, len(external_ids), 100):
                ids = external_ids[start : start + 100]
                body = _json(await _call(client, Platform.X, "GET", f"{self.api}/2/tweets", params={"ids": ",".join(ids), "tweet.fields": "public_metrics,non_public_metrics"}, headers=_bearer(access_token)))
                for tweet in body.get("data") or []:
                    public = tweet.get("public_metrics") or {}
                    private = tweet.get("non_public_metrics") or {}
                    likes, replies, reposts, quotes = (_int(public.get(key)) for key in ("like_count", "reply_count", "retweet_count", "quote_count"))
                    clicks = _int(private.get("url_link_clicks"))
                    result[str(tweet.get("id"))] = PostMetrics(
                        impressions=_int(private.get("impression_count") or public.get("impression_count")),
                        clicks=clicks, likes=likes, comments=replies, shares=sum(value or 0 for value in (reposts, quotes)),
                        saves=_int(public.get("bookmark_count")),
                        engagement=sum(value or 0 for value in (likes, replies, reposts, quotes, clicks)),
                        note=None if private else "X reports link clicks for the last 30 days only.",
                    )
        return result


class TikTokConnector(Connector):
    """TikTok Content Posting API. Until the app passes TikTok's audit, videos are uploaded
    to the creator's inbox as drafts (``ANUM_MARKETING_TIKTOK_DIRECT_POST`` off)."""

    provider = Provider.TIKTOK
    uses_pkce = True
    scopes = ("user.info.basic", "video.upload", "video.publish", "video.list")
    api = "https://open.tiktokapis.com"

    def missing_settings(self) -> list[str]:
        return _missing([
            ("ANUM_MARKETING_TIKTOK_CLIENT_KEY", marketing_settings.tiktok_client_key.strip()),
            ("ANUM_MARKETING_TIKTOK_CLIENT_SECRET", secret(marketing_settings.tiktok_client_secret)),
        ])

    def _client_form(self) -> dict[str, str]:
        return {"client_key": marketing_settings.tiktok_client_key.strip(), "client_secret": secret(marketing_settings.tiktok_client_secret)}

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        query = urlencode({
            "client_key": marketing_settings.tiktok_client_key.strip(),
            "scope": ",".join(self.scopes),
            "response_type": "code",
            "redirect_uri": self.redirect_url(),
            "state": state,
            "code_challenge": code_challenge or "",
            "code_challenge_method": "S256",
        })
        return f"https://www.tiktok.com/v2/auth/authorize/?{query}"

    @staticmethod
    def _check(body: dict[str, Any]) -> dict[str, Any]:
        error = body.get("error") or {}
        if isinstance(error, dict) and error.get("code") not in (None, "", "ok"):
            raise PlatformCallError(map_error(Platform.TIKTOK, 400, body))
        return body.get("data") or {}

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:
        async with config.http_client() as client:
            token = _json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/oauth/token/", data={
                **self._client_form(), "code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_url(), "code_verifier": code_verifier or "",
            }))
            access = str(token.get("access_token") or "")
            if not access:
                raise PlatformCallError(map_error(Platform.TIKTOK, 400, token))
            user = (self._check(_json(await _call(client, Platform.TIKTOK, "GET", f"{self.api}/v2/user/info/", params={"fields": "open_id,display_name,username"}, headers=_bearer(access)))).get("user")) or {}
        account = DiscoveredAccount(
            platform=Platform.TIKTOK, external_id=str(token.get("open_id") or user.get("open_id") or ""),
            display_name=str(user.get("display_name") or user.get("username") or "TikTok account"), handle=user.get("username"),
            access_token=access, refresh_token=token.get("refresh_token"), expires_at=_expires(token.get("expires_in")),
            scopes=str(token.get("scope") or "").split(","),
        )
        account.details = await self._creator_info(access)
        return [account]

    async def _creator_info(self, access_token: str) -> dict[str, Any]:
        async with config.http_client() as client:
            info = self._check(_json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/post/publish/creator_info/query/", headers=_bearer(access_token, **{"Content-Type": "application/json; charset=UTF-8"}), json={})))
        return {
            "privacy_level_options": info.get("privacy_level_options") or [],
            "max_video_post_duration_sec": info.get("max_video_post_duration_sec"),
            "direct_post": marketing_settings.tiktok_direct_post,
        }

    async def refresh(self, refresh_token: str) -> TokenSet | None:
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/oauth/token/", data={**self._client_form(), "grant_type": "refresh_token", "refresh_token": refresh_token}))
        if not body.get("access_token"):
            raise PlatformCallError(map_error(Platform.TIKTOK, 401, body))
        return TokenSet(str(body["access_token"]), body.get("refresh_token") or refresh_token, _expires(body.get("expires_in")))

    async def revoke(self, account: SocialAccount, access_token: str) -> None:
        async with config.http_client() as client:
            await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/oauth/revoke/", data={**self._client_form(), "token": access_token})

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:
        return await self._creator_info(access_token)

    async def publish(self, request: PublishRequest) -> PublishResult:
        video = request.media[0]
        size = len(video.content)
        chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
        count = max(1, size // chunk)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        headers = _bearer(request.access_token, **{"Content-Type": "application/json; charset=UTF-8"})
        direct = marketing_settings.tiktok_direct_post
        async with config.http_client() as client:
            if direct:
                options = request.account.details.get("privacy_level_options") or ["PUBLIC_TO_EVERYONE"]
                privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else options[0]
                init = self._check(_json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/post/publish/video/init/", headers=headers, json={
                    "post_info": {"title": request.text[:2200], "privacy_level": privacy, "disable_comment": False, "disable_duet": False, "disable_stitch": False},
                    "source_info": source,
                })))
            else:
                init = self._check(_json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/post/publish/inbox/video/init/", headers=headers, json={"source_info": source})))
            publish_id, upload_url = str(init.get("publish_id") or ""), str(init.get("upload_url") or "")
            for index in range(count):
                start = index * chunk
                end = size - 1 if index == count - 1 else start + chunk - 1
                await _call(client, Platform.TIKTOK, "PUT", upload_url, content=video.content[start : end + 1], headers={
                    "Content-Type": video.ref.content_type or "video/mp4", "Content-Range": f"bytes {start}-{end}/{size}",
                })

            async def status() -> dict[str, Any] | None:
                data = self._check(_json(await _call(client, Platform.TIKTOK, "POST", f"{self.api}/v2/post/publish/status/fetch/", headers=headers, json={"publish_id": publish_id})))
                state = data.get("status")
                if state == "FAILED":
                    raise platform_error("rejected", f"TikTok could not post the video ({data.get('fail_reason') or 'unknown reason'}).", "Check the video (MP4 or MOV, 3 seconds to 10 minutes, vertical) and try again.")
                return data if state in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"} else None

            final = await _poll(status, attempts=10)
        handle = request.account.handle
        if not direct:
            return PublishResult(
                external_id=publish_id, external_url=None, status=PostStatus.SENT_TO_INBOX,
                details={"note": "Sent to the TikTok inbox. Open TikTok to add sounds and post it."},
            )
        post_ids = (final or {}).get("publicaly_available_post_id") or []
        video_id = str(post_ids[0]) if post_ids else publish_id
        url = f"https://www.tiktok.com/@{handle}/video/{video_id}" if handle and post_ids else None
        return PublishResult(external_id=video_id, external_url=url, details={"publish_id": publish_id})

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:
        ids = [value for value in external_ids if value.isdigit()]
        if not ids:
            return {}
        async with config.http_client() as client:
            data = self._check(_json(await _call(
                client, Platform.TIKTOK, "POST", f"{self.api}/v2/video/query/?fields=id,view_count,like_count,comment_count,share_count",
                headers=_bearer(access_token, **{"Content-Type": "application/json; charset=UTF-8"}), json={"filters": {"video_ids": ids[:20]}},
            )))
        result: dict[str, PostMetrics] = {}
        for video in data.get("videos") or []:
            likes, comments, shares, views = (_int(video.get(key)) for key in ("like_count", "comment_count", "share_count", "view_count"))
            result[str(video.get("id"))] = PostMetrics(
                views=views, impressions=views, likes=likes, comments=comments, shares=shares,
                engagement=sum(value or 0 for value in (likes, comments, shares)), note="TikTok reports views, not reach or clicks.",
            )
        return result


class YouTubeConnector(Connector):
    """YouTube Data API v3 resumable uploads (videos and Shorts)."""

    provider = Provider.GOOGLE
    uses_pkce = True
    scopes = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
    api = "https://www.googleapis.com"

    def missing_settings(self) -> list[str]:
        return _missing([
            ("ANUM_MARKETING_GOOGLE_CLIENT_ID", marketing_settings.google_client_id.strip()),
            ("ANUM_MARKETING_GOOGLE_CLIENT_SECRET", secret(marketing_settings.google_client_secret)),
        ])

    def _client_form(self) -> dict[str, str]:
        return {"client_id": marketing_settings.google_client_id.strip(), "client_secret": secret(marketing_settings.google_client_secret)}

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        query = urlencode({
            "client_id": marketing_settings.google_client_id.strip(),
            "redirect_uri": self.redirect_url(),
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge or "",
            "code_challenge_method": "S256",
        })
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    async def exchange(self, code: str, code_verifier: str | None) -> list[DiscoveredAccount]:
        async with config.http_client() as client:
            token = _json(await _call(client, Platform.YOUTUBE, "POST", "https://oauth2.googleapis.com/token", data={
                **self._client_form(), "code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_url(), "code_verifier": code_verifier or "",
            }))
            access = str(token.get("access_token") or "")
            if not access:
                raise platform_error("oauth_failed", "Google did not return an access token.", "Start the connection again from the Marketing page.")
            channels = _json(await _call(client, Platform.YOUTUBE, "GET", f"{self.api}/youtube/v3/channels", params={"part": "snippet", "mine": "true"}, headers=_bearer(access)))
        found = []
        for channel in channels.get("items") or []:
            snippet = channel.get("snippet") or {}
            found.append(DiscoveredAccount(
                platform=Platform.YOUTUBE, external_id=str(channel.get("id")), display_name=str(snippet.get("title") or "YouTube channel"),
                handle=snippet.get("customUrl"), access_token=access, refresh_token=token.get("refresh_token"),
                expires_at=_expires(token.get("expires_in")), scopes=str(token.get("scope") or "").split(),
            ))
        if not found:
            raise platform_error("no_accounts", "This Google account has no YouTube channel.", "Create the channel in YouTube Studio, or sign in with the account that owns it.")
        return found

    async def refresh(self, refresh_token: str) -> TokenSet | None:
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.YOUTUBE, "POST", "https://oauth2.googleapis.com/token", data={**self._client_form(), "grant_type": "refresh_token", "refresh_token": refresh_token}))
        return TokenSet(str(body.get("access_token") or ""), body.get("refresh_token") or refresh_token, _expires(body.get("expires_in")))

    async def revoke(self, account: SocialAccount, access_token: str) -> None:
        async with config.http_client() as client:
            await _call(client, Platform.YOUTUBE, "POST", "https://oauth2.googleapis.com/revoke", data={"token": access_token})

    async def check(self, account: SocialAccount, access_token: str) -> dict[str, Any]:
        async with config.http_client() as client:
            body = _json(await _call(client, Platform.YOUTUBE, "GET", f"{self.api}/youtube/v3/channels", params={"part": "id", "mine": "true"}, headers=_bearer(access_token)))
        return {"channels": len(body.get("items") or [])}

    async def publish(self, request: PublishRequest) -> PublishResult:
        video = request.media[0]
        tags = request.hashtags[:30]
        metadata = {
            "snippet": {"title": request.title[:100], "description": request.text, "tags": tags, "categoryId": "22", "defaultLanguage": request.language},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        async with config.http_client() as client:
            started = await _call(
                client, Platform.YOUTUBE, "POST", f"{self.api}/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                headers=_bearer(request.access_token, **{
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(len(video.content)),
                    "X-Upload-Content-Type": video.ref.content_type or "video/mp4",
                }),
                json=metadata,
            )
            location = started.headers.get("location")
            if not location or not location.startswith("https://www.googleapis.com/"):
                raise platform_error("rejected", "YouTube did not start the upload.", "Try again; if it keeps failing, reconnect the channel.", retryable=True)
            uploaded = _json(await _call(client, Platform.YOUTUBE, "PUT", location, content=video.content, headers=_bearer(request.access_token, **{"Content-Type": video.ref.content_type or "video/mp4"})))
        video_id = str(uploaded.get("id") or "")
        short = (video.ref.duration_seconds or 999) <= 180 and video.ref.width is not None and video.ref.height is not None and video.ref.height >= video.ref.width
        url = f"https://www.youtube.com/shorts/{video_id}" if short else f"https://www.youtube.com/watch?v={video_id}"
        return PublishResult(external_id=video_id, external_url=url, details={"privacy": (uploaded.get("status") or {}).get("privacyStatus")})

    async def metrics(self, account: SocialAccount, access_token: str, external_ids: list[str]) -> dict[str, PostMetrics]:
        result: dict[str, PostMetrics] = {}
        async with config.http_client() as client:
            for start in range(0, len(external_ids), 50):
                body = _json(await _call(client, Platform.YOUTUBE, "GET", f"{self.api}/youtube/v3/videos", params={"part": "statistics", "id": ",".join(external_ids[start : start + 50])}, headers=_bearer(access_token)))
                for item in body.get("items") or []:
                    stats = item.get("statistics") or {}
                    views, likes, comments = (_int(stats.get(key)) for key in ("viewCount", "likeCount", "commentCount"))
                    result[str(item.get("id"))] = PostMetrics(
                        views=views, impressions=views, likes=likes, comments=comments,
                        engagement=sum(value or 0 for value in (likes, comments)),
                        note="YouTube Data API reports views; impressions and clicks are in YouTube Studio.",
                    )
        return result


CONNECTORS: dict[Provider, Connector] = {
    Provider.META: MetaConnector(),
    Provider.LINKEDIN: LinkedInConnector(),
    Provider.X: XConnector(),
    Provider.TIKTOK: TikTokConnector(),
    Provider.GOOGLE: YouTubeConnector(),
}


def connector(provider: Provider) -> Connector:
    return CONNECTORS[provider]
