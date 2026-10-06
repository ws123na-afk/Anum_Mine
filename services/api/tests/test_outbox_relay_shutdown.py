"""Stopping the outbox relay must never abandon a transaction mid-pass.

Regression: cancelling a pass while its commit ran in a worker thread let the
rollback race the same session, leaving the connection idle in transaction with
the claimed rows locked (the PostgreSQL CI job then hung on the next migration).
"""

from __future__ import annotations

import asyncio
import threading
import time

from anum_api.event_bus import InMemoryEventBus
from anum_api.outbox_relay import PostgresOutboxRelay, RelayPass, _in_thread


def test_a_cancelled_session_call_waits_for_its_thread_before_returning() -> None:
    finished = threading.Event()

    def slow_commit() -> None:
        time.sleep(0.3)
        finished.set()

    async def scenario() -> bool:
        task = asyncio.create_task(_in_thread(slow_commit))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return finished.is_set()

    assert asyncio.run(scenario()) is True


def test_stop_lets_the_current_pass_commit_instead_of_cancelling_it() -> None:
    bus = InMemoryEventBus()
    # Backlog metrics are not under test: keep the relay from reading them.
    relay = PostgresOutboxRelay(bus, session_factory=lambda: None, poll_interval=0.01, metrics_interval=1e12)
    relay._snapshot_at = time.monotonic()
    committed: list[str] = []

    async def scenario() -> list[str]:
        in_pass = asyncio.Event()

        async def slow_pass() -> RelayPass:
            in_pass.set()
            await asyncio.sleep(0.3)  # publishing and committing
            committed.append("pass")
            return RelayPass()

        relay.relay_once = slow_pass  # type: ignore[method-assign]
        await bus.connect()
        relay.start()
        await asyncio.wait_for(in_pass.wait(), timeout=2)
        await relay.stop(timeout=5)
        return committed

    assert asyncio.run(scenario()) == ["pass"]
