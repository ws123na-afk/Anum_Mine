"""Record database units of work opened on the event loop (test helper).

The API serves every user from one event loop per process: a store or repository opened on
it blocks every request while it waits for a connection, a lock or a slow query
(docs/realtime.md#event-loop). ``store_opens_on_loop`` wraps every store opener and the
request unit of work and lists the ones called from a thread that is running an event loop.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from anum_api import dependencies, scoped_store


@contextmanager
def store_opens_on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []

    def guard(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapped(*args: Any, **kwargs: Any) -> Iterator[Any]:
            try:
                asyncio.get_running_loop()
                calls.append(name)
            except RuntimeError:
                pass  # a worker thread: allowed
            with original(*args, **kwargs) as opened:
                yield opened

        return wrapped

    original_scoped = scoped_store.open_scoped_store
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("anum_api") and getattr(module, "open_scoped_store", None) is original_scoped:
            monkeypatch.setattr(module, "open_scoped_store", guard("open_scoped_store", original_scoped))
    monkeypatch.setattr(dependencies, "_unit_of_work", guard("_unit_of_work", dependencies._unit_of_work))
    yield calls
