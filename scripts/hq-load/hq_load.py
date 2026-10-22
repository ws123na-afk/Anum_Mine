#!/usr/bin/env python3
"""ANUM MINE headquarters load test: SSE fan-out and the presence snapshot (F14).

Asyncio + httpx only (already a dependency of services/api). No paid tools, no browser.
It measures the two server paths the headquarters depends on:

  1. SSE fan-out: N clients hold GET /api/v1/events/stream open for one workspace while a producer
     creates tasks; every task.created event must reach every client. Reported: connected clients,
     events delivered, loss, delivery latency p50/p95/p99/max.
  2. Snapshot: M concurrent loops read the presence evidence the office uses today (open tasks and
     agents, paged) for a fixed time. Reported: requests per second, errors, latency p50/p95/p99.

Everything runs in ONE tenant and workspace created by a local development session, so it needs
ANUM_ENVIRONMENT=local (or test) with ANUM_AUTH_MODE=headers. It never touches another tenant.
For a deployed API pass --token with a token of a test workspace and never run it against production
data: it creates tasks.

Examples
  python scripts/hq-load/hq_load.py --base-url http://127.0.0.1:8000 --sse-clients 21 --events 20
  python scripts/hq-load/hq_load.py --sse-clients 200 --events 50 --snapshot-workers 20 --duration 15 \
      --seed-tasks 200 --max-p95-ms 1500 --max-loss-pct 0 --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

import httpx


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


@dataclass
class Stats:
    count: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float]:
        lat = self.latencies_ms
        return {
            "count": self.count,
            "errors": self.errors,
            "p50_ms": round(pct(lat, 0.50), 1),
            "p95_ms": round(pct(lat, 0.95), 1),
            "p99_ms": round(pct(lat, 0.99), 1),
            "max_ms": round(max(lat), 1) if lat else 0.0,
            "mean_ms": round(statistics.fmean(lat), 1) if lat else 0.0,
        }


async def local_token(client: httpx.AsyncClient, base: str, tenant: str, workspace: str) -> str:
    response = await client.post(
        f"{base}/api/v1/auth/local/session",
        json={"tenant_id": tenant, "workspace_id": workspace, "user_id": "user_hq_load"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def create_task(client: httpx.AsyncClient, base: str, headers: dict[str, str], title: str) -> tuple[str | None, float]:
    sent = time.perf_counter()
    response = await client.post(
        f"{base}/api/v1/tasks", headers=headers, json={"title": title, "prompt": "hq load test, no real work"}
    )
    if response.status_code != 201:
        return None, sent
    return response.json()["id"], sent


async def seed(client: httpx.AsyncClient, base: str, headers: dict[str, str], count: int, prefix: str) -> int:
    sem = asyncio.Semaphore(10)
    made = 0

    async def one(i: int) -> None:
        nonlocal made
        async with sem:
            task_id, _ = await create_task(client, base, headers, f"{prefix} seed {i}")
            if task_id:
                made += 1

    await asyncio.gather(*(one(i) for i in range(count)))
    return made


async def sse_client(
    base: str, headers: dict[str, str], ready: asyncio.Event, stop: asyncio.Event,
    sent_at: dict[str, float], received: dict[str, int], latencies: list[float], connected: list[int], failed: list[int],
) -> None:
    # A client of its own so every SSE connection is a separate TCP connection, like separate browser tabs.
    timeout = httpx.Timeout(10.0, read=None)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            async with client.stream("GET", f"{base}/api/v1/events/stream", headers=headers) as response:
                if response.status_code != 200:
                    failed.append(1)
                    return
                connected.append(1)
                ready.set()
                mine: set[str] = set()  # one count per event per client, so duplicates cannot hide a loss
                buffer = ""
                async for chunk in response.aiter_text():
                    if stop.is_set():
                        return
                    buffer += chunk
                    frames = buffer.split("\n\n")
                    buffer = frames.pop()
                    for frame in frames:
                        for line in frame.split("\n"):
                            if not line.startswith("data: "):
                                continue
                            try:
                                event = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            if event.get("type") != "task.created":
                                continue
                            task_id = event.get("subject") or (event.get("payload") or {}).get("task_id")
                            if task_id in sent_at and task_id not in mine:
                                mine.add(task_id)
                                received[task_id] = received.get(task_id, 0) + 1
                                latencies.append((time.perf_counter() - sent_at[task_id]) * 1000)
        except asyncio.CancelledError:
            raise  # the run is over: closing a client is not a failure
        except httpx.HTTPError:
            if not stop.is_set():
                failed.append(1)


async def run_fanout(args: argparse.Namespace, base: str, headers: dict[str, str]) -> dict[str, object]:
    sent_at: dict[str, float] = {}
    received: dict[str, int] = {}
    latencies: list[float] = []
    connected: list[int] = []
    failed: list[int] = []
    stop = asyncio.Event()
    ready = asyncio.Event()
    tasks = [
        asyncio.create_task(sse_client(base, headers, ready, stop, sent_at, received, latencies, connected, failed))
        for _ in range(args.sse_clients)
    ]
    await asyncio.sleep(min(5.0, 0.5 + args.sse_clients * 0.02))  # let the clients connect
    limits = httpx.Limits(max_connections=20)
    async with httpx.AsyncClient(timeout=15.0, limits=limits, trust_env=False) as producer:
        for i in range(args.events):
            task_id, sent = await create_task(producer, base, headers, f"hq load event {i}")
            if task_id:
                sent_at[task_id] = sent
            await asyncio.sleep(args.event_gap)
    await asyncio.sleep(args.settle)
    stop.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    expected = len(sent_at) * max(1, len(connected))
    delivered = sum(received.values())
    stat = Stats(count=delivered, errors=len(failed), latencies_ms=latencies)
    return {
        "clients_requested": args.sse_clients,
        "clients_connected": len(connected),
        "clients_failed": len(failed),
        "events_sent": len(sent_at),
        "deliveries_expected": expected,
        "deliveries_received": delivered,
        "loss_pct": round(100 * (1 - delivered / expected), 2) if expected else 0.0,
        **{k: v for k, v in stat.summary().items() if k.endswith("_ms")},
    }


async def run_snapshot(args: argparse.Namespace, base: str, headers: dict[str, str]) -> dict[str, object]:
    stats = Stats()
    statuses: dict[int, int] = {}
    deadline = time.perf_counter() + args.duration
    paths = args.snapshot_path or ["/api/v1/tasks?limit=100", "/api/v1/agents?limit=100"]

    async def worker() -> None:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                bad = False
                for path in paths:  # one "snapshot" = every path, like the office's refresh
                    try:
                        response = await client.get(f"{base}{path}", headers=headers)
                        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
                        # 429 is the API's rate limit working as designed: counted, not an error
                        bad = bad or (response.status_code >= 400 and response.status_code != 429)
                    except httpx.HTTPError:
                        bad = True
                stats.count += 1
                stats.errors += int(bad)
                stats.latencies_ms.append((time.perf_counter() - started) * 1000)

    await asyncio.gather(*(worker() for _ in range(args.snapshot_workers)))
    out = stats.summary()
    out["snapshots_per_second"] = round(stats.count / args.duration, 1)
    out["workers"] = args.snapshot_workers
    out["rate_limited"] = statuses.get(429, 0)
    out["status_codes"] = {str(k): v for k, v in sorted(statuses.items())}
    out["paths"] = paths
    return out


async def main_async(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
        token = args.token or os.environ.get('HQ_LOAD_TOKEN') or await local_token(client, base, args.tenant, args.workspace)
        headers = {"authorization": f"Bearer {token}", "x-workspace-id": args.workspace}
        if (args.token or os.environ.get('HQ_LOAD_TOKEN')) and args.tenant:
            headers["x-tenant-id"] = args.tenant
        report: dict[str, object] = {"base_url": base, "tenant": args.tenant, "workspace": args.workspace}
        if args.seed_tasks:
            report["seeded_tasks"] = await seed(client, base, headers, args.seed_tasks, "hq-load")
    if args.sse_clients:
        report["fanout"] = await run_fanout(args, base, headers)
    if args.snapshot_workers:
        report["snapshot"] = await run_snapshot(args, base, headers)

    print(json.dumps(report, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

    failures: list[str] = []
    fan = report.get("fanout")
    if isinstance(fan, dict):
        if fan["clients_connected"] < fan["clients_requested"]:
            failures.append(f"only {fan['clients_connected']} of {fan['clients_requested']} SSE clients connected")
        if args.max_loss_pct is not None and fan["loss_pct"] > args.max_loss_pct:
            failures.append(f"event loss {fan['loss_pct']}% > {args.max_loss_pct}%")
        if args.max_p95_ms is not None and fan["p95_ms"] > args.max_p95_ms:
            failures.append(f"SSE delivery p95 {fan['p95_ms']} ms > {args.max_p95_ms} ms")
    snap = report.get("snapshot")
    if isinstance(snap, dict):
        if snap["errors"]:
            failures.append(f"{snap['errors']} snapshot errors")
        if args.fail_on_429 and snap["rate_limited"]:
            failures.append(f"{snap['rate_limited']} snapshot requests were rate limited (429)")
        if args.max_snapshot_p95_ms is not None and snap["p95_ms"] > args.max_snapshot_p95_ms:
            failures.append(f"snapshot p95 {snap['p95_ms']} ms > {args.max_snapshot_p95_ms} ms")
    for message in failures:
        print("FAIL:", message, file=sys.stderr)
    return 1 if failures else 0


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--tenant", default="tenant_hq_load")
    p.add_argument("--workspace", default="workspace_hq_load")
    p.add_argument("--token", help="bearer token of a TEST workspace (prefer the HQ_LOAD_TOKEN environment variable: arguments show in `ps`); default: a local development session")
    p.add_argument("--seed-tasks", type=int, default=0, help="create this many tasks first (the open tasks the snapshot reads)")
    p.add_argument("--sse-clients", type=int, default=0)
    p.add_argument("--events", type=int, default=20)
    p.add_argument("--event-gap", type=float, default=0.1, help="seconds between created tasks")
    p.add_argument("--settle", type=float, default=3.0, help="seconds to wait for the last events")
    p.add_argument("--snapshot-workers", type=int, default=0)
    p.add_argument("--snapshot-path", action="append", help="repeatable; default tasks and agents lists")
    p.add_argument("--duration", type=float, default=10.0, help="seconds for the snapshot phase")
    p.add_argument("--max-p95-ms", type=float, help="fail when SSE delivery p95 is above this")
    p.add_argument("--max-loss-pct", type=float, help="fail when more deliveries than this percent are missing")
    p.add_argument("--max-snapshot-p95-ms", type=float, help="fail when snapshot p95 is above this")
    p.add_argument("--fail-on-429", action="store_true", help="treat the API's rate limit as a failure (default: report it)")
    p.add_argument("--json", help="also write the report here")
    return p.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async(parse())))
    except httpx.ConnectError as exc:
        raise SystemExit(f"Cannot reach the API ({exc}). Start it first and check --base-url.")
