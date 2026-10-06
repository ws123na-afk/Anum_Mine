"""Outbound guard for workspace-configured model endpoints (threat model G1, SSRF).

A workspace owner chooses the ``base_url`` the API server calls for that workspace's
model. Outside ``ANUM_ENVIRONMENT=local``/``test`` that URL must name a public HTTPS
endpoint on the default port. The guard refuses, at save time and again for every
request:

* any scheme other than ``https`` and any credentials in the URL;
* a non-default port;
* IP literals in non-canonical forms (``2130706433``, ``0177.0.0.1``, ``0x7f.1``);
* hosts that resolve to loopback, private (RFC 1918), shared (CGNAT), unique-local,
  link-local (including ``169.254.169.254`` and other cloud metadata addresses),
  multicast, unspecified, reserved or documentation addresses, including IPv4 embedded
  in IPv6 (``::ffff:127.0.0.1``, NAT64, 6to4). A host is refused if *any* of its
  addresses is refused.

At request time :class:`PinnedEgressTransport` resolves the host once, checks every
address, then connects to the first address while keeping the original ``Host`` header
and TLS server name (SNI and certificate verification), so a DNS answer that changes
between the check and the connection (DNS rebinding) cannot redirect the call. Clients
built here never follow redirects.

``ANUM_MODEL_ALLOWED_HOSTS`` is the operator's explicit escape hatch for a self-hosted
model on a private network (for example Ollama): comma-separated ``host`` or
``host:port`` entries. A listed host may use plain HTTP, resolve to private or loopback
addresses and, when the entry names a port, use that port. Link-local, metadata,
multicast, unspecified and reserved addresses stay refused even for listed hosts.

Messages raised for resolution or address failures are deliberately generic: they never
say which address a host resolved to, so the guard is not an internal DNS oracle.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .settings import Settings

LOCAL_ENVIRONMENTS = frozenset({"local", "test"})
DEFAULT_PORTS = {"https": 443, "http": 80}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

ENDPOINT_REFUSED = (
    "base_url must point to a public model endpoint. Private, loopback, link-local and "
    "metadata addresses are refused; an operator can allow a self-hosted model host with "
    "ANUM_MODEL_ALLOWED_HOSTS."
)

# Refused even for allow-listed hosts: nothing legitimate serves a model there.
_ALWAYS_REFUSED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",  # "this network", includes the unspecified address
        "169.254.0.0/16",  # IPv4 link-local, includes 169.254.169.254 (cloud metadata)
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "192.0.0.192/32",  # Oracle Cloud metadata
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, includes the broadcast address
        "::/128",
        "fe80::/10",  # IPv6 link-local
        "fd00:ec2::254/128",  # AWS metadata over IPv6
        "ff00::/8",  # IPv6 multicast
    )
)
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class UnsafeModelEndpointError(ValueError):
    """The URL may not be called. The message is safe to show to the workspace owner."""


@dataclass(frozen=True)
class EgressPolicy:
    """Whether the guard applies, and the operator's allow-list of ``(host, port)``.

    A ``None`` port in an entry means the scheme's default port.
    """

    enforce: bool
    allowed_hosts: frozenset[tuple[str, int | None]] = frozenset()

    @classmethod
    def from_settings(cls, config: Settings | None = None) -> EgressPolicy:
        if config is None:
            from .settings import settings as config
        return cls(
            enforce=config.environment.strip().lower() not in LOCAL_ENVIRONMENTS,
            allowed_hosts=parse_allowed_hosts(config.model_allowed_hosts),
        )

    def allows_host(self, host: str, port: int, default_port: int) -> bool:
        if (host, port) in self.allowed_hosts:
            return True
        return port == default_port and (host, None) in self.allowed_hosts


def parse_allowed_hosts(value: str | Iterable[str] | None) -> frozenset[tuple[str, int | None]]:
    """``"ollama.internal:11434, 10.0.0.5:11434, models.corp"`` -> ``{(host, port), ...}``."""
    if not value:
        return frozenset()
    items = value.split(",") if isinstance(value, str) else list(value)
    entries: set[tuple[str, int | None]] = set()
    for raw in items:
        item = raw.strip()
        if not item:
            continue
        if "://" in item or "/" in item or "@" in item:
            raise ValueError("ANUM_MODEL_ALLOWED_HOSTS entries are host or host:port, not URLs")
        parsed = urlsplit(f"//{item}")
        if not parsed.hostname:
            raise ValueError("ANUM_MODEL_ALLOWED_HOSTS has an empty host")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("ANUM_MODEL_ALLOWED_HOSTS has an invalid port") from None
        entries.add((normalize_host(parsed.hostname), port))
    return frozenset(entries)


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".").strip("[]")


@dataclass(frozen=True)
class ModelEndpoint:
    scheme: str
    host: str
    port: int
    allow_listed: bool


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_noncanonical_ipv4(host: str) -> bool:
    """Forms such as ``2130706433``, ``0177.0.0.1`` or ``0x7f.1`` that resolvers accept."""
    try:
        socket.inet_aton(host)
    except (OSError, ValueError):
        return False
    return True


def check_endpoint(url: str, policy: EgressPolicy | None = None) -> ModelEndpoint:
    """Syntax checks that need no DNS. Raises :class:`UnsafeModelEndpointError`."""
    policy = policy or EgressPolicy.from_settings()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise UnsafeModelEndpointError("base_url is not a valid URL") from None
    scheme = parsed.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parsed.hostname:
        raise UnsafeModelEndpointError("base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeModelEndpointError("base_url must not contain credentials")
    host = normalize_host(parsed.hostname)
    default_port = DEFAULT_PORTS[scheme]
    port = default_port if port is None else port
    allow_listed = policy.allows_host(host, port, default_port)
    endpoint = ModelEndpoint(scheme=scheme, host=host, port=port, allow_listed=allow_listed)
    if not policy.enforce:
        return endpoint
    if _ip_literal(host) is None and _is_noncanonical_ipv4(host):
        raise UnsafeModelEndpointError("base_url must write IP addresses in standard form")
    if scheme != "https" and not allow_listed:
        raise UnsafeModelEndpointError("base_url must use HTTPS outside local development")
    if port != default_port and not allow_listed:
        raise UnsafeModelEndpointError(
            "base_url must use the default HTTPS port unless the host:port is in "
            "ANUM_MODEL_ALLOWED_HOSTS"
        )
    literal = _ip_literal(host)
    if literal is not None:
        check_address(literal, allow_listed=allow_listed)
    return endpoint


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def address_allowed(address: IPAddress, *, allow_listed: bool = False) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id:
            return False
        embedded = _embedded_ipv4(address)
        if embedded is not None and not address_allowed(embedded, allow_listed=allow_listed):
            return False
    if any(address in network for network in _ALWAYS_REFUSED_NETWORKS if network.version == address.version):
        return False
    if address.is_multicast or address.is_unspecified or address.is_link_local or address.is_reserved:
        return False
    if allow_listed:
        return True
    return address.is_global and not address.is_private and not address.is_loopback


def check_address(address: IPAddress, *, allow_listed: bool = False) -> None:
    if not address_allowed(address, allow_listed=allow_listed):
        raise UnsafeModelEndpointError(ENDPOINT_REFUSED)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


# Replaced by tests with a fake; looked up at call time.
resolver: Resolver = system_resolver


async def resolve_pinned(endpoint: ModelEndpoint, resolve: Resolver | None = None) -> str:
    """Resolve once, refuse if any address is refused, and return the address to dial."""
    literal = _ip_literal(endpoint.host)
    if literal is not None:
        check_address(literal, allow_listed=endpoint.allow_listed)
        return str(literal)
    try:
        answers = await (resolve or resolver)(endpoint.host, endpoint.port)
    except (OSError, UnicodeError):
        raise UnsafeModelEndpointError(ENDPOINT_REFUSED) from None
    addresses = [_ip_literal(answer.split("%", 1)[0]) for answer in answers]
    if not addresses or any(address is None for address in addresses):
        raise UnsafeModelEndpointError(ENDPOINT_REFUSED)
    for address in addresses:
        check_address(address, allow_listed=endpoint.allow_listed)  # type: ignore[arg-type]
    return str(addresses[0])


async def validate_model_base_url(
    url: str, policy: EgressPolicy | None = None, resolve: Resolver | None = None
) -> None:
    """Save-time check: syntax and, when enforced, DNS resolution of the host."""
    policy = policy or EgressPolicy.from_settings()
    endpoint = check_endpoint(url, policy)
    if policy.enforce:
        await resolve_pinned(endpoint, resolve)


class PinnedEgressTransport(httpx.AsyncBaseTransport):
    """Checks every request and connects to the address it resolved (no rebinding)."""

    def __init__(
        self,
        policy: EgressPolicy,
        *,
        resolve: Resolver | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.policy = policy
        self._resolve = resolve
        self._inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        endpoint = check_endpoint(str(original), self.policy)
        address = await resolve_pinned(endpoint, self._resolve)
        if "host" not in request.headers:
            request.headers["host"] = original.netloc.decode("ascii")
        request.url = original.copy_with(host=address)
        if endpoint.scheme == "https" and _ip_literal(endpoint.host) is None:
            # TLS server name and certificate verification use the original host name.
            request.extensions = {**request.extensions, "sni_hostname": original.host}
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def model_http_client(timeout_seconds: float, policy: EgressPolicy | None = None) -> httpx.AsyncClient:
    """HTTP client for a workspace-configured model endpoint. Never follows redirects."""
    policy = policy or EgressPolicy.from_settings()
    if not policy.enforce:
        return httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    return httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        transport=PinnedEgressTransport(policy),
    )
