"""Turn rate for live agent meetings (finding S-05).

``execute-turn`` and ``run`` each start model calls. On top of the global request limiter and the
per-meeting and monthly caps, a turn is admitted only while two sliding windows have room:

* one actor (tenant, workspace and user) may start ``meeting_turns_per_minute_actor`` turns a minute, and
* one workspace (tenant and workspace) may start ``meeting_turns_per_minute_workspace`` turns a minute.

``run`` asks for as many turns as it will execute (at most three), all or nothing, so a refused call
consumes nothing. A refusal names the window that is full and how many seconds to wait. State is kept
in memory per API process, like the webhook failure bucket in ``automation.py``; with several replicas
the effective ceiling is the limit times the replicas, and the monthly budget (S-02) remains the hard
bound on cost. The state holds only counts and timestamps keyed by ids: never text.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from .schemas import TenantContext

WINDOW_SECONDS = 60
MAX_KEYS = 10_000


@dataclass(frozen=True)
class TurnRateDecision:
    allowed: bool
    scope: str = ""          # "actor" or "workspace" when refused
    limit: int = 0
    retry_after_seconds: int = 0


class TurnRateLimiter:
    """Sliding window per key, bounded in keys so unique ids cannot exhaust memory."""

    def __init__(self, *, window_seconds: int = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = MAX_KEYS) -> None:
        self.window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _live(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def _wait(self, hits: deque[float], need: int, limit: int, now: float) -> int:
        # Room for ``need`` more appears once the (len + need - limit)th oldest hit leaves the window.
        index = min(len(hits) - 1, len(hits) + need - limit - 1)
        if index < 0:
            return self.window
        return max(1, math.ceil(hits[index] + self.window - now))

    def acquire(self, context: TenantContext, turns: int, *, actor_limit: int, workspace_limit: int) -> TurnRateDecision:
        if turns < 1:
            raise ValueError("turns must be at least 1")
        actor_key = f"a:{context.tenant_id}:{context.workspace_id}:{context.user_id}"
        space_key = f"w:{context.tenant_id}:{context.workspace_id}"
        now = self._clock()
        with self._lock:
            actor = self._live(actor_key, now)
            space = self._live(space_key, now)
            # The actor window is checked first: it is the one a person can act on.
            if len(actor) + turns > actor_limit:
                return TurnRateDecision(False, "actor", actor_limit, self._wait(actor, turns, actor_limit, now))
            if len(space) + turns > workspace_limit:
                return TurnRateDecision(False, "workspace", workspace_limit, self._wait(space, turns, workspace_limit, now))
            for key, hits in ((actor_key, actor), (space_key, space)):
                hits.extend([now] * turns)
                self._hits[key] = hits
                self._hits.move_to_end(key)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return TurnRateDecision(True)


limiter = TurnRateLimiter()


def refusal_message(decision: TurnRateDecision) -> str:
    who = "You have" if decision.scope == "actor" else "This workspace has"
    return (f"{who} started the most agent meeting turns allowed in one minute ({decision.limit}). "
            f"Wait {decision.retry_after_seconds} seconds and try again.")


__all__ = ["TurnRateDecision", "TurnRateLimiter", "limiter", "refusal_message"]
