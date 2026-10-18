"""The load guard before heavy evaluations (docs/finance-trading-intel.md#load-guard).

Backtests and walk-forward runs execute inside the API process on a small shared VM (the
Oracle Always Free Ampere A1). Before one starts, :func:`check` reads the machine's state,
read only and without any extra dependency:

* the 1-minute load average per CPU this process may use (``os.getloadavg`` and the CPU
  affinity or the cgroup CPU quota, whichever is smaller);
* the memory still free: the cgroup v2 limit minus its usage when the container has one,
  otherwise ``MemAvailable`` from ``/proc/meminfo``;
* how many heavy evaluations this process already runs.

When any is past its limit (``ANUM_FINANCE_EVAL_MAX_LOAD_PER_CPU``,
``ANUM_FINANCE_EVAL_MIN_FREE_MEMORY_MB``, ``ANUM_FINANCE_EVAL_MAX_CONCURRENT``) the evaluation
does not start: a person's request is refused with 503 and ``Retry-After`` (nothing is
stored), and a scheduled revalidation is recorded as deferred and retried by the next
post-close cycle instead of failing the strategy. A reading that is not available on this
platform counts as fine, never as busy.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .. import config as _config

RETRY_AFTER_SECONDS = 60

_slots_lock = threading.Lock()
_running = 0


@dataclass(frozen=True)
class Reading:
    load_per_cpu: float | None
    free_memory_mb: int | None
    running: int


class ServerBusy(RuntimeError):
    """The machine is too busy for a heavy evaluation now."""

    def __init__(self, reason: str, reading: Reading) -> None:
        self.reason = reason
        self.reading = reading
        super().__init__(MESSAGES[reason])


MESSAGES = {
    "load": "The server is busy right now, so the evaluation did not start. Try again in a minute.",
    "memory": "The server is short of free memory right now, so the evaluation did not start. Try again in a minute.",
    "concurrent": "Other evaluations are running on the server, so this one did not start. Try again in a minute.",
}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _cpus() -> float:
    try:
        count = float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        count = float(os.cpu_count() or 1)
    quota = (_read("/sys/fs/cgroup/cpu.max") or "").split()
    if len(quota) == 2 and quota[0] != "max":
        try:
            count = min(count, max(float(quota[0]) / float(quota[1]), 0.1))
        except (ValueError, ZeroDivisionError):
            pass
    return max(count, 0.1)


def _load_per_cpu() -> float | None:
    try:
        one, _, _ = os.getloadavg()
    except (AttributeError, OSError):
        return None
    return one / _cpus()


def _free_memory_mb() -> int | None:
    limit, used = _read("/sys/fs/cgroup/memory.max"), _read("/sys/fs/cgroup/memory.current")
    if limit and used and limit != "max":
        try:
            return max(int(limit) - int(used), 0) // (1024 * 1024)
        except ValueError:
            pass
    meminfo = _read("/proc/meminfo") or ""
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            try:
                return int(parts[1]) // 1024
            except (IndexError, ValueError):
                return None
    return None


# Replaced in tests; never by request data.
read_load: Callable[[], float | None] = _load_per_cpu
read_free_memory: Callable[[], int | None] = _free_memory_mb


def _limits(running: int) -> Reading:
    cfg = _config.finance_config
    now = Reading(load_per_cpu=read_load(), free_memory_mb=read_free_memory(), running=running)
    if running >= cfg.eval_max_concurrent:
        raise ServerBusy("concurrent", now)
    if now.load_per_cpu is not None and now.load_per_cpu > cfg.eval_max_load_per_cpu:
        raise ServerBusy("load", now)
    if now.free_memory_mb is not None and now.free_memory_mb < cfg.eval_min_free_memory_mb:
        raise ServerBusy("memory", now)
    return now


def check() -> Reading:
    """The current reading, or :class:`ServerBusy` when an evaluation must not start now."""
    with _slots_lock:
        running = _running
    return _limits(running)


@contextmanager
def heavy() -> Iterator[Reading]:
    """Run one heavy evaluation: refused with :class:`ServerBusy` when the machine is busy,
    otherwise one slot is held until it ends."""
    global _running
    with _slots_lock:
        now = _limits(_running)
        _running += 1
    try:
        yield now
    finally:
        with _slots_lock:
            _running -= 1
