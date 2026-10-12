"""A fake of every social platform API the marketing module calls (httpx.MockTransport).

Only plainly fake, lowercase credentials are used (gitleaks). Each handler checks the
bearer token it expects, so a test fails if a token is sent to the wrong platform.
"""

from __future__ import annotations

import json
import struct
from typing import Any
from urllib.parse import parse_qs

import httpx

PAGE_TOKEN = "page-access-example"
LI_TOKEN = "linkedin-access-example"
X_TOKEN = "x-access-example"
X_TOKEN_2 = "x-access-renewed"
TT_TOKEN = "tiktok-access-example"
YT_TOKEN = "youtube-access-example"


def jpeg(width: int, height: int) -> bytes:
    """A tiny JPEG header the API can read the size from."""
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9 + b"\xff\xd9"


def png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


class FakePlatforms:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bodies: list[tuple[str, Any]] = []
        self.fail: dict[str, httpx.Response] = {}
        self.fail_times: dict[str, int] = {}
        self.revoked: list[str] = []
        self.tweets: list[dict[str, Any]] = []
        self.li_posts: list[dict[str, Any]] = []
        self.ig_containers: list[dict[str, str]] = []
        self.tiktok_direct_inits = 0
        self.uploads: dict[str, int] = {}

    def failing(self, key: str, response: httpx.Response, times: int = 1) -> None:
        self.fail[key] = response
        self.fail_times[key] = times

    @staticmethod
    def _form(request: httpx.Request) -> dict[str, str]:
        if "multipart" in request.headers.get("content-type", ""):
            return {}
        try:
            return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        except UnicodeDecodeError:
            return {}

    def _bearer(self, request: httpx.Request) -> str:
        value = request.headers.get("authorization", "")
        return value.removeprefix("Bearer ")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path, method = request.url.host, request.url.path, request.method
        key = f"{method} {host}{path}"
        self.calls.append(key)
        if key in self.fail and self.fail_times.get(key, 0) > 0:
            self.fail_times[key] -= 1
            return self.fail[key]
        if host in {"graph.facebook.com", "graph-video.facebook.com"}:
            return self._meta(request, path, method)
        if host in {"www.linkedin.com", "api.linkedin.com"}:
            return self._linkedin(request, host, path, method)
        if host == "api.x.com":
            return self._x(request, path, method)
        if host in {"open.tiktokapis.com", "open-upload.tiktokapis.com"}:
            return self._tiktok(request, host, path, method)
        if host in {"oauth2.googleapis.com", "www.googleapis.com"}:
            return self._google(request, host, path, method)
        return httpx.Response(404, json={"error": {"message": f"unexpected {key}"}})

    # Meta ------------------------------------------------------------------------------
    def _meta(self, request: httpx.Request, path: str, method: str) -> httpx.Response:
        tail = path.split("/", 2)[2] if path.count("/") >= 2 else ""
        if tail == "oauth/access_token":
            form = self._form(request)
            assert form["client_secret"] == "meta-secret-example"
            if form.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "user-long-example", "expires_in": 5184000})
            assert form["code"] == "meta-code"
            return httpx.Response(200, json={"access_token": "user-short-example"})
        if tail == "me/accounts":
            assert self._bearer(request) == "user-long-example"
            return httpx.Response(200, json={"data": [{
                "id": "page1", "name": "Tyre Shop", "access_token": PAGE_TOKEN, "tasks": ["CREATE_CONTENT"],
                "instagram_business_account": {"id": "ig1", "username": "tyreshop", "name": "Tyre Shop"},
            }]})
        assert self._bearer(request) == PAGE_TOKEN, path
        params = dict(request.url.params)
        if method == "POST" and tail == "page1/photos":
            body = request.content
            self.bodies.append(("fb_photo", body))
            if b'name="published"' in body:
                return httpx.Response(200, json={"id": f"photo{len(self.bodies)}"})
            return httpx.Response(200, json={"id": "photo1", "post_id": "page1_100"})
        if method == "POST" and tail == "page1/feed":
            self.bodies.append(("fb_feed", self._form(request)))
            return httpx.Response(200, json={"id": "page1_200"})
        if method == "POST" and tail == "page1/videos":
            return httpx.Response(200, json={"id": "video1"})
        if method == "POST" and tail == "ig1/media":
            form = self._form(request)
            self.ig_containers.append(form)
            return httpx.Response(200, json={"id": f"container{len(self.ig_containers)}"})
        if method == "POST" and tail == "ig1/media_publish":
            return httpx.Response(200, json={"id": "igmedia1"})
        if method == "GET" and tail.endswith("/insights"):
            if tail.startswith("igmedia"):
                return httpx.Response(200, json={"data": [
                    {"name": name, "values": [{"value": value}]}
                    for name, value in (("reach", 900), ("views", 1200), ("likes", 80), ("comments", 9), ("shares", 4), ("saved", 7), ("total_interactions", 100))
                ]})
            return httpx.Response(200, json={"data": [
                {"name": "post_impressions", "values": [{"value": 2000}]},
                {"name": "post_impressions_unique", "values": [{"value": 1500}]},
                {"name": "post_clicks", "values": [{"value": 30}]},
            ]})
        if method == "GET":
            fields = params.get("fields", "")
            if fields.startswith("status_code"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if fields == "permalink":
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/example/"})
            if fields.startswith("shares"):
                return httpx.Response(200, json={"shares": {"count": 3}, "reactions": {"summary": {"total_count": 40}}, "comments": {"summary": {"total_count": 5}}})
            return httpx.Response(200, json={"id": tail, "name": "Tyre Shop", "username": "tyreshop"})
        return httpx.Response(404, json={"error": {"message": "unknown meta path", "code": 100}})

    # LinkedIn --------------------------------------------------------------------------
    def _linkedin(self, request: httpx.Request, host: str, path: str, method: str) -> httpx.Response:
        if path == "/oauth/v2/accessToken":
            form = self._form(request)
            assert form["client_secret"] == "linkedin-secret-example"
            if form["grant_type"] == "refresh_token":
                return httpx.Response(200, json={"access_token": LI_TOKEN, "expires_in": 5184000})
            return httpx.Response(200, json={
                "access_token": LI_TOKEN, "expires_in": 5184000, "refresh_token": "linkedin-refresh-example",
                "scope": "openid,profile,w_member_social,w_organization_social,r_organization_social",
            })
        if path == "/oauth/v2/revoke":
            self.revoked.append("linkedin")
            return httpx.Response(200)
        if host == "www.linkedin.com" and path.startswith("/dms-uploads/"):
            self.uploads[path] = len(request.content)
            return httpx.Response(201)
        assert self._bearer(request) == LI_TOKEN
        if path == "/v2/userinfo":
            return httpx.Response(200, json={"sub": "abc123", "name": "Owner Name"})
        assert request.headers.get("linkedin-version"), "versioned API needs LinkedIn-Version"
        if path == "/rest/organizationAcls":
            return httpx.Response(200, json={"elements": [{"organization": "urn:li:organization:42", "role": "ADMINISTRATOR"}]})
        if path == "/rest/organizations/42":
            return httpx.Response(200, json={"localizedName": "TyrePulse"})
        if path == "/rest/images":
            return httpx.Response(200, json={"value": {"uploadUrl": "https://www.linkedin.com/dms-uploads/img1", "image": "urn:li:image:1"}})
        if path == "/rest/posts":
            self.li_posts.append(json.loads(request.content))
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:99"})
        if path == "/rest/organizationalEntityShareStatistics":
            return httpx.Response(200, json={"elements": [{"share": "urn:li:share:99", "totalShareStatistics": {
                "impressionCount": 500, "uniqueImpressionsCount": 420, "clickCount": 12, "likeCount": 20, "commentCount": 2, "shareCount": 1}}]})
        return httpx.Response(404, json={"message": "unknown linkedin path"})

    # X ---------------------------------------------------------------------------------
    def _x(self, request: httpx.Request, path: str, method: str) -> httpx.Response:
        if path == "/2/oauth2/token":
            assert request.headers["authorization"].startswith("Basic ")
            form = self._form(request)
            if form["grant_type"] == "refresh_token":
                assert form["refresh_token"] == "x-refresh-example"
                return httpx.Response(200, json={"access_token": X_TOKEN_2, "refresh_token": "x-refresh-renewed", "expires_in": 7200})
            assert form["code_verifier"], "PKCE verifier is required"
            return httpx.Response(200, json={"access_token": X_TOKEN, "refresh_token": "x-refresh-example", "expires_in": 7200, "scope": "tweet.read tweet.write users.read media.write offline.access"})
        if path == "/2/oauth2/revoke":
            self.revoked.append("x")
            return httpx.Response(200, json={"revoked": True})
        assert self._bearer(request) in {X_TOKEN, X_TOKEN_2}
        if path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "777", "name": "Tyre Shop", "username": "tyreshop"}})
        if path == "/2/media/upload":
            return httpx.Response(200, json={"data": {"id": "m1"}})
        if path == "/2/tweets" and method == "POST":
            self.tweets.append({**json.loads(request.content), "token": self._bearer(request)})
            return httpx.Response(201, json={"data": {"id": "t1", "text": "ok"}})
        if path == "/2/tweets" and method == "GET":
            return httpx.Response(200, json={"data": [{"id": "t1", "public_metrics": {"like_count": 10, "reply_count": 2, "retweet_count": 3, "quote_count": 1, "impression_count": 700}, "non_public_metrics": {"impression_count": 710, "url_link_clicks": 6}}]})
        return httpx.Response(404, json={"title": "Not Found"})

    # TikTok ----------------------------------------------------------------------------
    def _tiktok(self, request: httpx.Request, host: str, path: str, method: str) -> httpx.Response:
        ok = {"code": "ok", "message": ""}
        if host == "open-upload.tiktokapis.com":
            assert request.headers["content-range"].startswith("bytes 0-")
            self.uploads[path] = len(request.content)
            return httpx.Response(201)
        if path == "/v2/oauth/token/":
            return httpx.Response(200, json={"access_token": TT_TOKEN, "expires_in": 86400, "refresh_token": "tiktok-refresh-example", "open_id": "tt1", "scope": "user.info.basic,video.upload,video.publish,video.list"})
        if path == "/v2/oauth/revoke/":
            self.revoked.append("tiktok")
            return httpx.Response(200, json={})
        assert self._bearer(request) == TT_TOKEN
        if path == "/v2/user/info/":
            return httpx.Response(200, json={"data": {"user": {"open_id": "tt1", "display_name": "Tyre Shop", "username": "tyreshop"}}, "error": ok})
        if path == "/v2/post/publish/creator_info/query/":
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], "max_video_post_duration_sec": 600}, "error": ok})
        if path == "/v2/post/publish/inbox/video/init/":
            return httpx.Response(200, json={"data": {"publish_id": "pub1", "upload_url": "https://open-upload.tiktokapis.com/upload/1"}, "error": ok})
        if path == "/v2/post/publish/video/init/":
            self.tiktok_direct_inits += 1
            return httpx.Response(200, json={"data": {"publish_id": "pub2", "upload_url": "https://open-upload.tiktokapis.com/upload/2"}, "error": ok})
        if path == "/v2/post/publish/status/fetch/":
            publish_id = json.loads(request.content)["publish_id"]
            if publish_id == "pub1":
                return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}, "error": ok})
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000000000000001]}, "error": ok})
        if path == "/v2/video/query/":
            return httpx.Response(200, json={"data": {"videos": [{"id": "7300000000000000001", "view_count": 5000, "like_count": 300, "comment_count": 20, "share_count": 15}]}, "error": ok})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "unknown"}})

    # Google ----------------------------------------------------------------------------
    def _google(self, request: httpx.Request, host: str, path: str, method: str) -> httpx.Response:
        if host == "oauth2.googleapis.com" and path == "/token":
            return httpx.Response(200, json={"access_token": YT_TOKEN, "expires_in": 3599, "refresh_token": "youtube-refresh-example", "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"})
        if host == "oauth2.googleapis.com" and path == "/revoke":
            self.revoked.append("google")
            return httpx.Response(200)
        assert self._bearer(request) == YT_TOKEN
        if path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Tyre Shop", "customUrl": "@tyreshop"}}]})
        if path == "/upload/youtube/v3/videos" and method == "POST":
            metadata = json.loads(request.content)
            self.bodies.append(("youtube", metadata))
            return httpx.Response(200, headers={"location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"})
        if path == "/upload/youtube/v3/videos" and method == "PUT":
            self.uploads[path] = len(request.content)
            return httpx.Response(200, json={"id": "vid1", "status": {"privacyStatus": "public"}})
        if path == "/youtube/v3/videos":
            return httpx.Response(200, json={"items": [{"id": "vid1", "statistics": {"viewCount": "4000", "likeCount": "250", "commentCount": "12"}}]})
        return httpx.Response(404, json={"error": {"code": 404, "message": "not found"}})
