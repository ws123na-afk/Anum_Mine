import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { OfficePresenceItem, OfficePresenceSnapshot } from '@anum/contracts';
import {
  PRESENCE_TIMING, PresenceHub, acquirePresenceHub, createSseParser, presenceHubCount, presenceWithStaleness,
  type PresenceHubDeps, type StreamResponse,
} from '../src/lib/presenceStream.ts';

const WS = 'workspace_a';
const T0 = Date.parse('2026-10-26T10:00:00Z');
const iso = (ms: number) => new Date(ms).toISOString();

function item(key: string, state: OfficePresenceItem['state'], verified = T0, kind: OfficePresenceItem['provenance']['kind'] = 'task'): OfficePresenceItem {
  return { key, agent_id: key, agent_name: key, desk_id: null, state, provenance: { kind, run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: null }, task: null, last_verified_at: iso(verified), stale: false };
}
function snapshot(items: OfficePresenceItem[], asOf = T0, cursor: string | null = 'evt_1'): OfficePresenceSnapshot {
  const totals = { working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0 };
  for (const i of items) totals[i.state] += 1;
  return { version: 1, workspace_id: WS, as_of: iso(asOf), cursor, totals, items };
}
const frame = (event: string, data: unknown, id?: string) => `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const snapFrame = (items: OfficePresenceItem[], id = 'evt_1', asOf = T0) => frame('snapshot', { ...snapshot(items, asOf, id), sequence: 0 }, id);
const deltaFrame = (sequence: number, changed: OfficePresenceItem[], id: string, removed: string[] = [], asOf = T0 + sequence * 1000) =>
  frame('delta', { version: 1, sequence, as_of: iso(asOf), totals: {}, changed, removed }, id);

const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };

class FakeStream {
  private queue: Array<{ done: boolean; value?: string } | Error> = [];
  private waiting: Array<{ resolve: (v: { done: boolean; value?: string }) => void; reject: (e: Error) => void }> = [];
  cancelled = false;
  status: number;
  contentType: string | null;
  constructor(status = 200, contentType: string | null = 'text/event-stream') { this.status = status; this.contentType = contentType; }
  push(text: string) { this.deliver({ done: false, value: text }); }
  end() { this.deliver({ done: true }); }
  fail(error: Error) { this.deliver(error); }
  private deliver(entry: { done: boolean; value?: string } | Error) {
    const waiter = this.waiting.shift();
    if (!waiter) { this.queue.push(entry); return; }
    if (entry instanceof Error) waiter.reject(entry); else waiter.resolve(entry);
  }
  response(): StreamResponse {
    return {
      status: this.status, contentType: this.contentType,
      read: () => {
        const next = this.queue.shift();
        if (next) return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
        return new Promise((resolve, reject) => { this.waiting.push({ resolve, reject }); });
      },
      cancel: () => { this.cancelled = true; },
    };
  }
}

function harness(options: { workspace?: () => string } = {}) {
  let now = T0;
  let nextId = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  const opens: Array<{ lastEventId: string | null; stream: FakeStream | Error }> = [];
  const script: Array<FakeStream | Error> = [];
  const polls: Array<OfficePresenceSnapshot | (Error & { status?: number })> = [];
  let pollCalls = 0;
  const deps: PresenceHubDeps = {
    openStream: async (lastEventId) => {
      const next = script.shift();
      opens.push({ lastEventId, stream: next ?? new Error('no script') });
      if (!next || next instanceof Error) throw next ?? new Error('no script');
      return next.response();
    },
    loadSnapshot: async () => {
      pollCalls += 1;
      const next = polls.shift();
      if (!next) throw Object.assign(new Error('offline'), { status: 500 });
      if (next instanceof Error) throw next;
      return next;
    },
    scope: () => ({ tenantId: 't', workspaceId: options.workspace ? options.workspace() : WS }),
    now: () => now,
    setTimer: (fn, ms) => { const id = nextId++; timers.set(id, { at: now + ms, fn }); return () => { timers.delete(id); }; },
    random: () => 0.5,
  };
  const hub = new PresenceHub(deps);
  const advance = async (ms: number) => {
    const target = now + ms;
    for (;;) {
      await flush();
      const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = Math.max(now, due[1].at);
      timers.delete(due[0]);
      due[1].fn();
    }
    now = target;
    await flush();
  };
  return { hub, deps, script, opens, polls, advance, timers, get pollCalls() { return pollCalls; }, setNow: (v: number) => { now = v; } };
}

describe('server-sent events, incrementally', () => {
  test('frames split across chunks, comments and CRLF', () => {
    const p = createSseParser();
    assert.deepEqual(p.push('id: 7\nevent: delta\nda'), []);
    const out = p.push('ta: {"a":1}\r\n\r\n: keep-alive\n\n');
    assert.deepEqual(out, [{ kind: 'frame', event: 'delta', id: '7', data: '{"a":1}' }, { kind: 'comment' }]);
  });
  test('an oversized frame is refused', () => {
    assert.throws(() => createSseParser().push('x'.repeat(2_100_000)));
  });
});

describe('the presence hub', () => {
  test('snapshot, then deltas: states change without any reload', async () => {
    const h = harness();
    const s = new FakeStream();
    h.script.push(s);
    h.hub.start();
    await flush();
    assert.equal(h.hub.getView().link, 'connecting');
    s.push(snapFrame([item('a', 'idle'), item('b', 'working')]));
    await flush();
    assert.equal(h.hub.getView().link, 'live');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'idle');
    s.push(deltaFrame(1, [item('a', 'blocked')], 'evt_2'));
    await flush();
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'blocked');
    assert.equal(h.hub.getView().state?.totals.blocked, 1);
    s.push(deltaFrame(2, [], 'evt_3', ['b']));
    await flush();
    assert.equal(h.hub.getView().state?.items.has('b'), false);
    h.hub.stop();
  });

  test('a duplicate or older delta changes nothing and does not reconnect', async () => {
    const h = harness();
    const s = new FakeStream();
    h.script.push(s);
    h.hub.start();
    s.push(snapFrame([item('a', 'idle')]));
    s.push(deltaFrame(1, [item('a', 'working')], 'evt_2'));
    await flush();
    const version = h.hub.getView().version;
    s.push(deltaFrame(1, [item('a', 'blocked')], 'evt_2'));
    s.push(deltaFrame(0, [item('a', 'paused')], 'evt_0'));
    await flush();
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'working');
    assert.equal(h.hub.getView().version, version);
    assert.equal(h.opens.length, 1);
    h.hub.stop();
  });

  test('a gap in the sequence reconnects at once with Last-Event-ID and takes the new snapshot', async () => {
    const h = harness();
    const first = new FakeStream(), second = new FakeStream();
    h.script.push(first, second);
    h.hub.start();
    first.push(snapFrame([item('a', 'idle')], 'evt_1'));
    first.push(deltaFrame(1, [item('a', 'working')], 'evt_2'));
    first.push(deltaFrame(3, [item('a', 'blocked')], 'evt_4'));
    await flush();
    assert.equal(first.cancelled, true);
    assert.equal(h.opens.length, 2);
    assert.equal(h.opens[1].lastEventId, 'evt_2');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'working');
    second.push(snapFrame([item('a', 'blocked')], 'evt_4', T0 + 5000));
    await flush();
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'blocked');
    assert.equal(h.hub.getView().link, 'live');
    h.hub.stop();
  });

  test('the server ending the stream is a planned break: reconnect in a moment, link stays Live', async () => {
    const h = harness();
    const first = new FakeStream(), second = new FakeStream();
    h.script.push(first, second);
    h.hub.start();
    first.push(snapFrame([item('a', 'idle')]));
    await flush();
    first.end();
    await flush();
    assert.equal(h.hub.getView().link, 'live');
    await h.advance(PRESENCE_TIMING.planned + 1);
    assert.equal(h.opens.length, 2);
    assert.equal(h.opens[1].lastEventId, 'evt_1');
    h.hub.stop();
  });

  test('failures back off 1 s, 2 s, 4 s with jitter, show Reconnecting, then fall back to polling', async () => {
    const h = harness();
    h.script.push(new Error('x'), new Error('x'), new Error('x'));
    h.polls.push(snapshot([item('a', 'idle')]));
    h.hub.start();
    await flush();
    assert.equal(h.opens.length, 1);
    assert.equal(h.hub.getView().link, 'reconnecting');
    await h.advance(999);
    assert.equal(h.opens.length, 1);
    await h.advance(2);
    assert.equal(h.opens.length, 2);
    await h.advance(1_990);
    assert.equal(h.opens.length, 2);
    await h.advance(20);
    assert.equal(h.opens.length, 3);
    await flush();
    assert.equal(h.hub.getView().link, 'polling');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'idle');
    h.hub.stop();
  });

  test('polling repeats every 10 s, shows Not connected when it fails, and probes the stream again', async () => {
    const h = harness();
    h.script.push(new Error('x'), new Error('x'), new Error('x'));
    h.polls.push(snapshot([item('a', 'idle')]));
    h.hub.start();
    await h.advance(13_100);
    assert.equal(h.hub.getView().link, 'offline');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'idle');
    h.polls.push(snapshot([item('a', 'working')], T0 + 20_000));
    const recovered = new FakeStream();
    h.script.push(recovered);
    recovered.push(snapFrame([item('a', 'meeting')], 'evt_9', T0 + 100_000));
    await h.advance(PRESENCE_TIMING.probeMs);
    assert.equal(h.hub.getView().link, 'live');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'meeting');
    h.hub.stop();
  });

  test('a stream that answers 200 but sends nothing is treated as blocked', async () => {
    const h = harness();
    h.script.push(new FakeStream());
    h.hub.start();
    await h.advance(PRESENCE_TIMING.firstFrameMs + 1);
    assert.equal(h.hub.getView().link, 'reconnecting');
    h.hub.stop();
  });

  test('a response that is not an event stream is blocked', async () => {
    const h = harness();
    h.script.push(new FakeStream(200, 'text/html'));
    h.hub.start();
    await flush();
    assert.equal(h.hub.getView().link, 'reconnecting');
    h.hub.stop();
  });

  test('silence for longer than the keep-alive window drops the link', async () => {
    const h = harness();
    const s = new FakeStream(), s2 = new FakeStream();
    h.script.push(s, s2);
    h.hub.start();
    s.push(snapFrame([item('a', 'idle')]));
    await flush();
    await h.advance(PRESENCE_TIMING.silenceMs - 1_000);
    s.push(': keep-alive\n\n');
    await h.advance(PRESENCE_TIMING.silenceMs - 1_000);
    assert.equal(h.hub.getView().link, 'live', 'a keep-alive resets the watchdog');
    await h.advance(2_000);
    assert.equal(h.hub.getView().link, 'reconnecting');
    h.hub.stop();
  });

  test('401 and 403 stop without retrying; polling 404 means the server has no presence', async () => {
    const denied = harness();
    denied.script.push(new FakeStream(403));
    denied.hub.start();
    await flush();
    assert.equal(denied.hub.getView().link, 'denied');
    await denied.advance(120_000);
    assert.equal(denied.opens.length, 1);

    const missing = harness();
    missing.script.push(new Error('x'), new Error('x'), new Error('x'));
    missing.polls.push(Object.assign(new Error('nf'), { status: 404 }));
    missing.hub.start();
    await missing.advance(3_100);
    assert.equal(missing.hub.getView().link, 'missing');
    assert.equal(missing.hub.getView().state, null);
  });

  test('a snapshot of another workspace is never shown', async () => {
    const h = harness();
    const s = new FakeStream(), s2 = new FakeStream();
    h.script.push(s, s2);
    h.hub.start();
    s.push(frame('snapshot', { ...snapshot([item('x', 'working')]), workspace_id: 'workspace_other', sequence: 0 }, 'evt_1'));
    await flush();
    assert.equal(h.hub.getView().state, null);
    h.hub.stop();
  });

  test('an answer older than the one on screen is ignored', async () => {
    const h = harness();
    const s = new FakeStream(), s2 = new FakeStream();
    h.script.push(s, s2);
    h.hub.start();
    s.push(snapFrame([item('a', 'working')], 'evt_5', T0 + 60_000));
    await flush();
    s.push(deltaFrame(3, [], 'evt_6')); // gap: reconnect
    await flush();
    s2.push(snapFrame([item('a', 'idle')], 'evt_4', T0));
    await flush();
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'working');
    h.hub.stop();
  });

  test('kick reconnects at once and a scope change stops the hub', async () => {
    let ws = WS;
    const h = harness({ workspace: () => ws });
    h.script.push(new Error('x'), new FakeStream());
    h.hub.start();
    await flush();
    h.hub.kick();
    await flush();
    assert.equal(h.opens.length, 2);
    ws = 'workspace_b';
    h.script.push(new FakeStream());
    h.hub.kick();
    await flush();
    assert.equal(h.opens.length, 2, 'no stream is opened for another workspace');
  });
});

describe('a server without the stream', () => {
  test('404 goes straight to polling, and a poll 404 then says the server has no presence', async () => {
    const h = harness();
    h.script.push(new FakeStream(404));
    h.polls.push(snapshot([item('a', 'idle')]));
    h.hub.start();
    await flush();
    assert.equal(h.hub.getView().link, 'polling');
    assert.equal(h.hub.getView().state?.items.get('a')?.state, 'idle');
    assert.equal(h.opens.length, 1);
    h.hub.stop();
  });
});

describe('reads triggered by the shell', () => {
  test('a burst of events while polling is one read, and a live stream is never reconnected by them', async () => {
    const h = harness();
    h.script.push(new FakeStream(404));
    h.polls.push(snapshot([item('a', 'idle')]), snapshot([item('a', 'working')], T0 + 3_000), snapshot([item('a', 'queued')], T0 + 6_000));
    h.hub.start();
    await flush();
    assert.equal(h.pollCalls, 1);
    h.hub.pollNow(); h.hub.pollNow(); h.hub.pollNow();
    await flush();
    assert.equal(h.pollCalls, 1, 'inside the minimum gap');
    await h.advance(PRESENCE_TIMING.pollMinGapMs + 10);
    h.hub.pollNow(); h.hub.pollNow();
    await flush();
    assert.equal(h.pollCalls, 2);
    h.hub.stop();
    const live = harness();
    const s = new FakeStream();
    live.script.push(s);
    live.hub.start();
    s.push(snapFrame([item('a', 'idle')]));
    await flush();
    live.hub.pollNow();
    live.hub.kick();
    await flush();
    assert.equal(live.opens.length, 1);
    assert.equal(live.pollCalls, 0);
    live.hub.stop();
  });
});

describe('staleness', () => {
  const view = (state: ReturnType<PresenceHub['getView']>['state'], receivedAt: number, contactAt = receivedAt) => ({ state, receivedAt, contactAt });
  const hubWith = async (items: OfficePresenceItem[]) => {
    const h = harness();
    const s = new FakeStream();
    h.script.push(s);
    h.hub.start();
    s.push(snapFrame(items));
    await flush();
    const v = h.hub.getView();
    h.hub.stop();
    return v;
  };
  test('a record not confirmed within the server window turns stale on the server clock, with no new frame', async () => {
    const v = await hubWith([item('a', 'working', T0 - 1000), item('b', 'idle', T0, 'none')]);
    const early = presenceWithStaleness(view(v.state, v.receivedAt), v.receivedAt + 60_000)!;
    assert.equal(early.find((i) => i.key === 'a')!.stale, false);
    const lateAt = v.receivedAt + PRESENCE_TIMING.staleAfterMs + 5_000;
    const late = presenceWithStaleness(view(v.state, v.receivedAt, lateAt - 1_000), lateAt)!;
    assert.equal(late.find((i) => i.key === 'a')!.stale, true);
    assert.equal(late.find((i) => i.key === 'b')!.stale, false, 'idle with no record is exempt while the link is in touch');
  });
  test('when the link has been silent for the whole window, everything is stale', async () => {
    const v = await hubWith([item('b', 'idle', T0, 'none')]);
    const out = presenceWithStaleness(view(v.state, v.receivedAt, v.contactAt), v.contactAt + PRESENCE_TIMING.staleAfterMs + 1_000)!;
    assert.equal(out[0].stale, true);
  });
  test('the server flag is kept, and nothing is invented before the first answer', () => {
    assert.equal(presenceWithStaleness({ state: null, receivedAt: 0, contactAt: 0 }, 1), null);
  });
});

describe('one hub for the page', () => {
  test('every part gets the same hub; the last release stops it', async () => {
    const h = harness();
    let made = 0;
    const a = acquirePresenceHub('t/w', () => { made += 1; return h.hub; });
    const b = acquirePresenceHub('t/w', () => { made += 1; return h.hub; });
    assert.equal(made, 1);
    assert.equal(a.hub, b.hub);
    assert.equal(presenceHubCount(), 2);
    a.release();
    a.release();
    assert.equal(presenceHubCount(), 1);
    b.release();
    assert.equal(presenceHubCount(), 0);
  });
});
