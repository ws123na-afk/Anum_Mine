"""Security and contract tests without requiring a browser installation."""
import asyncio
import pytest

from anum_api.browser_agent import (
    BrowserAction, BrowserRejected, BrowserRequest, checked_url, validate_host,
)


@pytest.mark.parametrize("host", [
    "localhost", "localhost.localdomain", "internal.local", "private.internal",
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "bad host",
])
def test_denies_private_hostnames_and_ip_literals(host):
    with pytest.raises(BrowserRejected):
        validate_host(host)


def test_url_allowlist_enforced():
    hosts = frozenset({"example.com"})
    assert checked_url("https://example.com/articles?q=one", hosts).startswith("https://")
    for url in [
        "http://example.com", "https://other.com", "https://example.com:8443",
        "https://user:pass@example.com", "file:///etc/passwd",
        "https://example.com.evil.com",
    ]:
        with pytest.raises(BrowserRejected):
            checked_url(url, hosts)


def test_rejects_script_forms_and_unbounded_actions():
    for kind in ["evaluate", "submit", "fill", "download", "upload"]:
        with pytest.raises(BrowserRejected):
            BrowserAction(kind)
    with pytest.raises(BrowserRejected):
        BrowserRequest(frozenset({"example.com"}), tuple(BrowserAction("read") for _ in range(21)))


def test_request_validates_navigation_at_creation():
    with pytest.raises(BrowserRejected):
        BrowserRequest(frozenset({"example.com"}), (BrowserAction("navigate", "https://wrong.com"),))
    request = BrowserRequest(frozenset({"example.com"}), (
        BrowserAction("navigate", "https://example.com"),
        BrowserAction("read"),
        BrowserAction("screenshot"),
    ))
    assert len(request.actions) == 3


def test_denies_dns_to_private_network(monkeypatch):
    import anum_api.browser_agent as module
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("10.1.2.3", 443))
    ])
    with pytest.raises(BrowserRejected):
        asyncio.run(module.verify_public_dns("example.com"))
