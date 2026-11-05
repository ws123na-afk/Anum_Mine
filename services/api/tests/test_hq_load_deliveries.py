"""H-09: the load tool must not count a delivery as lost because it arrived early.

The SSE frame for a produced task can reach a client before the producer has read the
POST response that tells it the task id. The tool used to ignore any frame whose id it did
not know yet, which showed up as 0.3 to 0.8 percent "loss" at 200 clients while the server
had delivered every event once (verified by reconciling after the run).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "hq-load" / "hq_load.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("hq_load_tool", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_arrival_before_the_producer_knows_the_id_is_still_delivered() -> None:
    d = load_tool().Deliveries()
    d.arrived(0, "task_a", 10.050)  # the frame wins the race
    d.sent("task_a", 10.000)        # the producer registers the id afterwards
    d.arrived(1, "task_a", 10.200)
    delivered, latencies = d.result()
    assert delivered == 2
    assert [round(x) for x in sorted(latencies)] == [50, 200]


def test_duplicates_count_once_per_client_and_unsent_ids_are_ignored() -> None:
    d = load_tool().Deliveries()
    d.sent("task_a", 1.0)
    d.arrived(0, "task_a", 1.1)
    d.arrived(0, "task_a", 1.5)       # a repeat on the same client
    d.arrived(0, "task_other", 1.2)   # not produced by this run
    delivered, latencies = d.result()
    assert delivered == 1 and round(latencies[0]) == 100


def test_a_missing_delivery_is_still_reported_as_missing() -> None:
    d = load_tool().Deliveries()
    d.sent("task_a", 1.0)
    d.sent("task_b", 1.0)
    d.arrived(0, "task_a", 1.1)
    delivered, _ = d.result()
    assert delivered == 1  # task_b never arrived: the caller computes loss from expected - delivered
