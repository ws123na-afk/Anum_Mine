"""The action browser's only route out (docs/action-browser.md#the-browser-service-in-action-mode).

A real site needs cookies, form posts, its own scripts and frames, so Chromium makes its own
requests in action mode, but it still cannot reach anything by itself: every host name maps
to "not found" and its only route is this CONNECT proxy on 127.0.0.1 inside the browser
service. The proxy:

* accepts ``CONNECT host:443`` only (no plain http, no other port, no other method);
* checks the host name (``model_egress.check_endpoint``, always enforced);
* resolves it **once** and refuses the tunnel if any answer is loopback, private, shared,
  unique-local, link-local, a cloud metadata address, multicast, reserved or documentation
  space (``model_egress.resolve_pinned``);
* dials **that checked address**, so a second DNS answer is never used (no rebinding);
* keeps TLS end to end between Chromium and the site, and counts tunnels and bytes against
  the session's limits.

No ANUM settings and no secrets here. Tests pass ``resolve`` and ``connect``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..model_egress import EgressPolicy, UnsafeModelEndpointError, check_endpoint, resolve_pinned

POLICY = EgressPolicy(enforce=True)
MAX_HEADER_BYTES = 8192
Resolver = Callable[[str, int], Awaitable[list[str]]]
Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def _open(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port)


@dataclass
class ProxyStats:
    tunnels: int = 0
    refused: int = 0
    bytes: int = 0
    reasons: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {"tunnels": self.tunnels, "refused": self.refused, "bytes": self.bytes, "refused_reasons": dict(self.reasons)}


class EgressProxy:
    """One per browser session; ``start`` returns the port Chromium is pointed at."""

    def __init__(
        self,
        *,
        resolve: Resolver | None = None,
        connect: Connector | None = None,
        max_bytes: int = 25_000_000,
        max_tunnels: int = 200,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._resolve = resolve
        self._connect = connect or _open
        self.max_bytes = max_bytes
        self.max_tunnels = max_tunnels
        self.timeout_seconds = timeout_seconds
        self.stats = ProxyStats()
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.port = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def _refuse(self, code: str) -> None:
        self.stats.refused += 1
        self.stats.reasons[code] += 1

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.ensure_future(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=self.timeout_seconds)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                self._refuse("bad_request")
                return
            if len(head) > MAX_HEADER_BYTES:
                await self._answer(writer, 431, "bad_request")
                return
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = line.split(" ")
            if len(parts) != 3 or parts[0] != "CONNECT":
                await self._answer(writer, 405, "method")
                return
            host, port = _split_target(parts[1])
            if host is None or port != 443:
                await self._answer(writer, 403, "port")
                return
            if self.stats.tunnels >= self.max_tunnels:
                await self._answer(writer, 429, "too_many_tunnels")
                return
            if self.stats.bytes >= self.max_bytes:
                await self._answer(writer, 429, "too_many_bytes")
                return
            try:
                endpoint = check_endpoint(f"https://[{host}]/" if ":" in host else f"https://{host}/", POLICY)
                address = await resolve_pinned(endpoint, self._resolve)
            except UnsafeModelEndpointError:
                await self._answer(writer, 403, "refused_address")
                return
            try:
                up_reader, upstream = await asyncio.wait_for(self._connect(address, 443), timeout=self.timeout_seconds)
            except (OSError, TimeoutError):
                await self._answer(writer, 502, "unreachable")
                return
            self.stats.tunnels += 1
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(self._pipe(reader, upstream), self._pipe(up_reader, writer))
        except (ConnectionError, OSError):
            pass
        finally:
            for stream in (upstream, writer):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()

    async def _pipe(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await source.read(65536)
                if not chunk:
                    break
                self.stats.bytes += len(chunk)
                if self.stats.bytes > self.max_bytes:
                    self._refuse("too_many_bytes")
                    break
                sink.write(chunk)
                await sink.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                sink.close()

    async def _answer(self, writer: asyncio.StreamWriter, status: int, code: str) -> None:
        self._refuse(code)
        with contextlib.suppress(Exception):
            writer.write(f"HTTP/1.1 {status} Refused\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()


def _split_target(target: str) -> tuple[str | None, int]:
    """``host:port`` or ``[v6]:port`` of a CONNECT line."""
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port_text = target.rpartition(":")
    if not host or not port_text.isdigit():
        return None, 0
    return host.lower(), int(port_text)


__all__ = ["EgressProxy", "ProxyStats"]
