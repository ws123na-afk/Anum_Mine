# ANUM MINE headquarters: streaming and load testing

Tool: `scripts/hq-load/hq_load.py` (Python 3.11 or newer, asyncio and `httpx`, no paid tools).
It measures the two server paths the office depends on.

1. **SSE fan-out.** N clients keep `GET /api/v1/events/stream` open for one workspace. A producer creates
   tasks (`POST /api/v1/tasks`); each `task.created` event must reach every client. Reported: clients
   connected, deliveries expected and received, loss percent, delivery latency p50, p95, p99 and worst.
   Each client counts an event once, so duplicates cannot hide a loss.
2. **Snapshot.** M workers read what the office reads today to derive presence (`GET /api/v1/tasks?limit=100`
   and `GET /api/v1/agents?limit=100`, repeatable with `--snapshot-path`) for a fixed time. Reported:
   snapshots per second, errors, rate-limited (429) count, latency p50, p95, p99. When the presence
   snapshot endpoint lands (`claude/hq-data`), pass it with `--snapshot-path /api/v1/<its path>`.

The tool creates tasks, so it only runs against a test workspace. It never runs against production data.

## Run it locally (what the numbers below used)

```
cd services/api
ANUM_ENVIRONMENT=local ANUM_AUTH_MODE=headers \
  ANUM_RATE_LIMIT_REQUESTS_PER_MINUTE=6000000 ANUM_RATE_LIMIT_BURST=100000 \
  python3 -m uvicorn anum_api.main:app --port 8218
# in another shell, from the repository root:
python3 scripts/hq-load/hq_load.py --base-url http://127.0.0.1:8218 --workspace workspace_l21 \
  --seed-tasks 21 --sse-clients 21 --events 20 --snapshot-workers 5 --duration 6
python3 scripts/hq-load/hq_load.py --base-url http://127.0.0.1:8218 --workspace workspace_l200 \
  --seed-tasks 200 --sse-clients 200 --events 50 --settle 8 --snapshot-workers 20 --duration 10
```

The relaxed rate limit is only so the tool is not throttled. With the default limit (600 requests a
minute, burst 120 per client) the same snapshot test is answered with 429 for about 96 percent of
requests at 5 workers: that is the limiter working, and the tool reports it as `rate_limited` instead of
an error (`--fail-on-429` makes it a failure). A single browser tab refreshing the office is far below
that limit.

Exit code is 1 when a threshold is passed and broken: `--max-p95-ms`, `--max-loss-pct`,
`--max-snapshot-p95-ms`, `--fail-on-429`, or when not every client connects.

## Results

Local run, 22 and 23 October 2026 (rows below use the fixed tool), API memory backend (events are polled once a second), one machine for API,
tool and 200 SSE connections, so these are floor numbers for latency and an upper bound for contention.

| Scenario | Result |
| --- | --- |
| 21 SSE clients, 20 events | 21 of 21 connected, 420 of 420 deliveries, loss 0 percent, latency p50 518 ms, p95 963 ms, p99 970 ms |
| 21 agents snapshot, 5 workers, 6 s | 111 snapshots per second, 0 errors, p50 43 ms, p95 65 ms, p99 87 ms |
| 200 SSE clients, 50 events, no other load | 200 of 200 connected, 10,000 of 10,000 deliveries, loss 0 percent (3 runs), p50 about 540 ms, p95 about 1,000 ms, p99 2.7 to 3.0 s |
| 200 clients plus 20 snapshot workers, 200 seeded tasks | loss 0 percent, p95 1,004 ms, p99 2.7 s; snapshot 58 per second, p50 324 ms, p95 496 ms, p99 732 ms, 0 errors |

The median of about 0.5 s is the memory backend's one second poll.

### H-09 closed: the loss was a counting bug in the tool, not the server

Earlier versions of this document reported 0.28 to 0.82 percent loss at 200 clients. Root cause: the
tool counted a `task.created` frame only if the producer already knew that task id. The producer learns
the id from the `POST /api/v1/tasks` response, and under 200 open connections on one machine a client can
read the frame before the producer's task has processed that response. Those early frames were ignored and
reported as lost. The server delivered them.

How it was isolated (23 October 2026, memory backend, same machine, 200 clients, 50 events):

| Run | Result |
| --- | --- |
| Old tool, 3 runs | 10,000 expected; 9,961, 9,918 and 9,942 counted (loss 0.39, 0.82, 0.58 percent) |
| Independent diagnostic: record every frame with its client and id, reconcile after the run | 10,000 of 10,000 delivered, 0 missing for any task, 0 unknown ids |
| Fixed tool, 3 runs (`Deliveries` in `hq_load.py` matches arrivals with produced ids after the fact) | 10,000 of 10,000 each time, loss 0.00 percent; p50 about 540 ms, p95 about 1,000 ms, p99 2.7 to 3.0 s |
| Fixed tool, 200 clients plus 20 snapshot workers, 200 seeded tasks | 10,000 of 10,000, loss 0.00 percent, p95 1,004 ms; snapshot 58 per second, p95 496 ms, p99 732 ms, 0 errors |

The p99 is higher than in the old table because the previously discarded early frames, which are the
slowest ones, are now counted. The server code (`/api/v1/events/stream` polling path, the presence stream,
event ids, keep-alives) needed no change: nothing is dropped, there is no bounded per-client queue, and the
sequence gap rule and `Last-Event-ID` replay are unchanged. Regression test:
`services/api/tests/test_hq_load_deliveries.py` (fails against the old counting, passes now).

Not run: PostgreSQL mode and the Oracle VM. The local PostgreSQL cluster was not running and this
sandbox has no database credentials for it, so only the memory backend was measured. In PostgreSQL mode the
stream cursor is `(created_at, id)`; if the Oracle run shows a few missing deliveries, suspect that a
transaction committing after a later one (a lower `created_at` becoming visible after the cursor moved), not
the tool. That is a hypothesis, not a finding: run the steps below and report the numbers.

## Acceptable numbers on the Oracle VM

Proposed targets. The owner or the next agent should confirm them after the first real run.

| Measure | 21 agents, 21 tabs | 200 agents, 200 tabs |
| --- | --- | --- |
| SSE clients connected | all | all |
| Event loss | 0 percent | 0 percent (any loss is a finding: keep the JSON and the API log) |
| SSE delivery p95 | at most 2 s with the memory event bus, at most 1 s with NATS | at most 3 s memory, at most 1.5 s with NATS |
| Snapshot p95 (5 workers / 20 workers) | at most 250 ms | at most 600 ms |
| Snapshot errors (5xx) | 0 | 0 |
| API container memory while connected | under 70 percent of its limit | under 70 percent |
| PostgreSQL connections in use | no pool exhaustion, no `idle in transaction` from streams | same |

## How the owner runs it on the Oracle VM

Do this on staging with a test organisation and a test workspace, never with real customer data, and not
while people depend on the system.

1. Make sure staging uses the Oracle stack (`infra/oracle/compose.yaml`). Staging requires sign-in
   (OIDC), so the local development session of the tool is not available there. Create a test user in a
   test organisation, sign in as that user, and obtain an access token for it. Export it without writing it
   in a file or a chat: `read -rs HQ_LOAD_TOKEN; export HQ_LOAD_TOKEN`. The tool reads `HQ_LOAD_TOKEN`
   (arguments show up in `ps`, so avoid `--token`).
2. From the VM, run the tool in a throwaway container on the API's network so no port needs opening:
   `docker run --rm -e HQ_LOAD_TOKEN --network <the compose backend network> -v "$PWD/scripts/hq-load":/w python:3.13-slim sh -c "pip install -q httpx && python /w/hq_load.py --base-url http://api:8000 --tenant <test tenant id> --workspace <test workspace id> --seed-tasks 21 --sse-clients 21 --events 20 --snapshot-workers 5 --duration 10 --max-p95-ms 2000 --max-loss-pct 0 --max-snapshot-p95-ms 250"`.
   Find the network name with `docker network ls`; the API service is `api` in `infra/oracle/compose.yaml`.
3. Repeat with `--seed-tasks 200 --sse-clients 200 --events 50 --settle 8 --snapshot-workers 20
   --max-loss-pct 0 --max-snapshot-p95-ms 600`. Pass: exit code 0, `loss_pct` 0.0, `clients_connected` equal to `clients_requested`. Fail: `loss_pct` above 0 (re-run once; if it repeats, send `out.json` and the API log lines of the run, and compare with the memory-mode result above: that points at the PostgreSQL event cursor), or any 5xx.
4. While it runs, watch Beszel (CPU, memory) and `docker stats`; afterwards check
   `select count(*), state from pg_stat_activity group by state;` shows no growth of `idle in transaction`.
5. Paste the JSON output (`--json out.json`) into the Results table above with the date and the machine
   size, and delete the test tasks (the test workspace can be deleted from the organisation settings).

If staging's rate limit answers 429, lower the workers or ask for a test-only limit; do not disable the
limiter on a shared environment.
