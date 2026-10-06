"""SSRF guard for workspace model endpoints (threat model G1, anum_api/model_egress.py)."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from anum_api import model_egress, onboarding
from anum_api.model_egress import (
    ENDPOINT_REFUSED,
    EgressPolicy,
    PinnedEgressTransport,
    UnsafeModelEndpointError,
    check_endpoint,
    model_http_client,
    parse_allowed_hosts,
    validate_model_base_url,
)
from anum_api.model_gateway import OpenAICompatibleGateway, RetryPolicy, build_model_gateway
from anum_api.onboarding import GENERIC_CONNECTION_FAILURE, ModelConfigWrite, _model_configs
from anum_api.schemas import TenantContext
from anum_api.settings import settings

ENFORCED = EgressPolicy(enforce=True)
PUBLIC_IP = "93.184.215.14"
CONTEXT = TenantContext(tenant_id="tenant_egress", workspace_id="workspace_egress", user_id="owner_egress", roles=["owner"])


class FakeResolver:
    """Answers from a script, one answer list per call, and counts lookups."""

    def __init__(self, *answers: list[str] | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})


# --------------------------------------------------------------------------- syntax and literals

REFUSED_URLS = [
    "http://models.example/v1",  # plain HTTP
    "ftp://models.example/v1",
    "file:///etc/passwd",
    "https://user:pw@models.example/v1",  # credentials
    "https://:pw@models.example/v1",
    "https://models.example:8443/v1",  # non-default port
    "https://models.example:80/v1",
    "https://127.0.0.1/v1",  # loopback
    "https://127.1.2.3/v1",
    "https://[::1]/v1",
    "https://10.0.0.1/v1",  # RFC 1918
    "https://172.16.5.4/v1",
    "https://192.168.1.1/v1",
    "https://100.64.0.1/v1",  # shared address space (CGNAT)
    "https://169.254.169.254/latest/meta-data",  # cloud metadata
    "https://169.254.1.1/v1",  # link-local
    "https://100.100.100.200/v1",  # Alibaba metadata
    "https://0.0.0.0/v1",  # unspecified
    "https://224.0.0.1/v1",  # multicast
    "https://240.0.0.1/v1",  # reserved
    "https://255.255.255.255/v1",  # broadcast
    "https://192.0.2.10/v1",  # documentation
    "https://[::]/v1",
    "https://[fd00::1]/v1",  # unique local
    "https://[fd00:ec2::254]/v1",  # AWS metadata over IPv6
    "https://[fe80::1]/v1",  # IPv6 link-local
    "https://[ff02::1]/v1",  # IPv6 multicast
    "https://[::ffff:127.0.0.1]/v1",  # IPv4-mapped loopback
    "https://[::ffff:169.254.169.254]/v1",  # IPv4-mapped metadata
    "https://[64:ff9b::a9fe:a9fe]/v1",  # NAT64 of 169.254.169.254
    "https://[2002:7f00:1::]/v1",  # 6to4 of 127.0.0.1
    "https://2130706433/v1",  # decimal 127.0.0.1
    "https://017700000001/v1",  # octal 127.0.0.1
    "https://0177.0.0.1/v1",  # octal dotted
    "https://0x7f.1/v1",  # hex, short form
    "https://0xa9fea9fe/v1",  # hex 169.254.169.254
    "https://127.1/v1",  # short form
]


@pytest.mark.parametrize("url", REFUSED_URLS)
def test_unsafe_base_urls_are_refused_outside_local(url: str) -> None:
    with pytest.raises(UnsafeModelEndpointError):
        asyncio.run(validate_model_base_url(url, ENFORCED, FakeResolver([PUBLIC_IP])))


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "https://8.8.8.8/v1", "https://[2606:4700:4700::1111]/v1", "https://Models.Example./v1"],
)
def test_public_https_endpoints_are_allowed(url: str) -> None:
    asyncio.run(validate_model_base_url(url, ENFORCED, FakeResolver([PUBLIC_IP])))


@pytest.mark.parametrize(
    "answer",
    [
        ["127.0.0.1"],
        ["10.1.2.3"],
        ["169.254.169.254"],
        ["::1"],
        ["fd12:3456::1"],
        ["fe80::1%2"],
        ["::ffff:10.0.0.1"],
        [PUBLIC_IP, "127.0.0.1"],  # one bad address is enough
        [],
    ],
)
def test_hosts_resolving_to_internal_addresses_are_refused(answer: list[str]) -> None:
    with pytest.raises(UnsafeModelEndpointError) as raised:
        asyncio.run(validate_model_base_url("https://models.example/v1", ENFORCED, FakeResolver(answer)))
    # Generic: never reveals what the host resolved to.
    assert str(raised.value) == ENDPOINT_REFUSED
    for address in answer:
        assert address not in str(raised.value)


def test_unresolvable_hosts_get_the_same_generic_refusal() -> None:
    resolver = FakeResolver(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
    with pytest.raises(UnsafeModelEndpointError, match="public model endpoint"):
        asyncio.run(validate_model_base_url("https://nowhere.example/v1", ENFORCED, resolver))


def test_local_and_test_environments_keep_localhost_ollama_working() -> None:
    resolver = FakeResolver(["127.0.0.1"])
    for environment in ("local", "test"):
        policy = EgressPolicy.from_settings(settings.model_copy(update={"environment": environment}))
        assert policy.enforce is False
        asyncio.run(validate_model_base_url("http://localhost:11434/v1", policy, resolver))
    assert resolver.calls == []  # nothing is resolved locally
    # Credentials and non-HTTP schemes are refused everywhere.
    with pytest.raises(UnsafeModelEndpointError):
        check_endpoint("http://user:pw@localhost:11434/v1", EgressPolicy(enforce=False))
    with pytest.raises(UnsafeModelEndpointError):
        check_endpoint("gopher://localhost:11434/", EgressPolicy(enforce=False))


def test_production_policy_comes_from_settings() -> None:
    config = settings.model_copy(update={"environment": "production", "model_allowed_hosts": "ollama.internal:11434"})
    policy = EgressPolicy.from_settings(config)
    assert policy.enforce is True
    assert policy.allowed_hosts == frozenset({("ollama.internal", 11434)})


# --------------------------------------------------------------------------- allow-list


def test_allow_listed_self_hosted_model_may_use_http_private_addresses_and_its_port() -> None:
    policy = EgressPolicy(enforce=True, allowed_hosts=parse_allowed_hosts("ollama.internal:11434, models.corp"))

    asyncio.run(validate_model_base_url("http://ollama.internal:11434/v1", policy, FakeResolver(["10.0.0.7"])))
    asyncio.run(validate_model_base_url("https://models.corp/v1", policy, FakeResolver(["192.168.4.4"])))
    # Only the listed port; a bare host entry means the default port.
    with pytest.raises(UnsafeModelEndpointError):
        check_endpoint("http://ollama.internal:11435/v1", policy)
    with pytest.raises(UnsafeModelEndpointError):
        check_endpoint("https://models.corp:8443/v1", policy)
    # Metadata and link-local stay refused even for a listed host.
    with pytest.raises(UnsafeModelEndpointError):
        asyncio.run(validate_model_base_url("http://ollama.internal:11434/v1", policy, FakeResolver(["169.254.169.254"])))
    # Another host on the same private network is still refused.
    with pytest.raises(UnsafeModelEndpointError):
        asyncio.run(validate_model_base_url("https://other.corp/v1", policy, FakeResolver(["192.168.4.5"])))


def test_allow_list_entries_are_hosts_not_urls() -> None:
    assert parse_allowed_hosts(" A.example. , 10.0.0.5:11434,[fd00::5]:8080 ,") == frozenset(
        {("a.example", None), ("10.0.0.5", 11434), ("fd00::5", 8080)}
    )
    for bad in ("http://ollama:11434", "ollama/v1", "user@ollama", "ollama:notaport"):
        with pytest.raises(ValueError):
            parse_allowed_hosts(bad)


# --------------------------------------------------------------------------- request time


def _pinned_client(resolver: FakeResolver, handler, policy: EgressPolicy = ENFORCED) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = PinnedEgressTransport(policy, resolve=resolver, inner=httpx.MockTransport(recording))
    return httpx.AsyncClient(transport=transport, follow_redirects=False), seen


def _gateway(client: httpx.AsyncClient, base_url: str = "https://models.example/v1") -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        api_key="sk-test",
        model="gpt-4.1-mini",
        base_url=base_url,
        client=client,
        retry_policy=RetryPolicy(max_attempts=1),
    )


def test_requests_connect_to_the_resolved_address_with_the_original_host_and_sni() -> None:
    resolver = FakeResolver([PUBLIC_IP])
    client, seen = _pinned_client(resolver, _ok)

    asyncio.run(_gateway(client).generate_text("hello"))

    (request,) = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["host"] == "models.example"
    assert request.extensions["sni_hostname"] == "models.example"
    assert resolver.calls == [("models.example", 443)]


def test_dns_rebinding_after_the_save_time_check_is_refused_at_request_time() -> None:
    # First answer (save time) is public, the next one (request time) is loopback.
    resolver = FakeResolver([PUBLIC_IP], ["127.0.0.1"])
    asyncio.run(validate_model_base_url("https://rebind.example/v1", ENFORCED, resolver))
    client, seen = _pinned_client(resolver, _ok)

    with pytest.raises(UnsafeModelEndpointError):
        asyncio.run(_gateway(client, "https://rebind.example/v1").generate_text("hello"))

    assert seen == []  # never connected
    assert len(resolver.calls) == 2


def test_each_request_resolves_once_and_uses_that_answer() -> None:
    # A resolver that would flip to an internal address on a second lookup within the
    # same request never gets the chance: the transport looks up once and pins.
    resolver = FakeResolver([PUBLIC_IP], ["10.0.0.1"])
    client, seen = _pinned_client(resolver, _ok)

    asyncio.run(_gateway(client).generate_text("hello"))

    assert len(resolver.calls) == 1
    assert seen[0].url.host == PUBLIC_IP


def test_ipv6_answers_are_pinned_with_brackets() -> None:
    resolver = FakeResolver(["2606:4700:4700::1111"])
    client, seen = _pinned_client(resolver, _ok)

    asyncio.run(_gateway(client).generate_text("hello"))

    assert seen[0].url.host == "2606:4700:4700::1111"
    assert str(seen[0].url).startswith("https://[2606:4700:4700::1111]/v1/")
    assert seen[0].headers["host"] == "models.example"


def test_redirects_to_other_hosts_are_not_followed() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    client, seen = _pinned_client(FakeResolver([PUBLIC_IP]), redirect)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_gateway(client).generate_text("hello"))
    assert len(seen) == 1
    assert all(request.url.host == PUBLIC_IP for request in seen)


def test_guarded_clients_never_follow_redirects_or_use_env_proxies() -> None:
    enforced = model_http_client(5, ENFORCED)
    relaxed = model_http_client(5, EgressPolicy(enforce=False))
    try:
        assert enforced.follow_redirects is False and relaxed.follow_redirects is False
        assert isinstance(enforced._transport, PinnedEgressTransport)
        assert enforced._mounts == {}  # no HTTP(S)_PROXY mounts that would bypass the pin
    finally:
        asyncio.run(enforced.aclose())
        asyncio.run(relaxed.aclose())


def test_workspace_gateways_route_through_the_guard_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    resolver = FakeResolver(["10.0.0.9"])
    monkeypatch.setattr(model_egress, "resolver", resolver)
    connected: list[httpx.Request] = []

    def never(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be reached
        connected.append(request)
        return _ok(request)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(never))
    guarded = build_model_gateway("openai_compatible", api_key="sk-test", base_url="https://internal.example/v1", egress_guard=True)

    with pytest.raises(UnsafeModelEndpointError):
        asyncio.run(guarded.generate_text("hello"))
    assert connected == []
    assert resolver.calls == [("internal.example", 443)]


# --------------------------------------------------------------------------- API


def test_saving_an_internal_base_url_is_refused_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(ValidationError, match="HTTPS"):
        ModelConfigWrite(provider="ollama", model="llama3.2", base_url="http://localhost:11434/v1")
    with pytest.raises(ValidationError, match="credentials"):
        ModelConfigWrite(provider="ollama", model="llama3.2", base_url="https://u:p@models.example/v1")

    monkeypatch.setattr(model_egress, "resolver", FakeResolver(["169.254.169.254"]))
    payload = ModelConfigWrite(provider="openai_compatible", model="m", base_url="https://metadata.example/v1", api_key=SecretStr("sk-test"))
    with pytest.raises(HTTPException) as raised:
        asyncio.run(onboarding.set_model_config(payload, CONTEXT))
    assert raised.value.status_code == 422
    assert "169.254" not in raised.value.detail


def test_connection_test_returns_a_generic_error_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    _model_configs.clear()
    onboarding._workspace_gateways.clear()
    _model_configs.save(
        CONTEXT,
        provider="ollama",
        model="llama3.2",
        base_url="https://ollama.internal.example/v1",
        api_key=None,
        updated_at=onboarding.utc_now(),
    )

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused 10.0.0.9:443")

    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    monkeypatch.setattr(settings, "model_max_attempts", 1)

    # Locally the message is actionable.
    with pytest.raises(HTTPException) as local:
        asyncio.run(onboarding.test_model_config(CONTEXT))
    assert "Could not reach Ollama at https://ollama.internal.example/v1" in local.value.detail

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as remote:
        asyncio.run(onboarding.test_model_config(CONTEXT))
    assert remote.value.status_code == 502
    assert remote.value.detail == GENERIC_CONNECTION_FAILURE
    assert "ollama.internal" not in remote.value.detail and "10.0.0.9" not in remote.value.detail

    # A refused (internal) endpoint gets the very same answer as an unreachable one.
    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: None)
    monkeypatch.setattr(model_egress, "resolver", FakeResolver(["10.0.0.9"]))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(onboarding.test_model_config(CONTEXT))
    assert refused.value.detail == GENERIC_CONNECTION_FAILURE
    _model_configs.clear()
