/**
 * The headquarters' live presence link (docs/ANUM_MINE_HQ_LIVE_SCENE.md): one shared stream for the whole page.
 *
 *   snapshot, then deltas (GET /office/presence/stream)  ->  reconnect with backoff and Last-Event-ID  ->
 *   polling of GET /office/presence when streaming is blocked  ->  "Not connected" when nothing answers.
 *
 * The browser's EventSource cannot send the sign-in headers, so the stream is read with fetch and parsed here. All
 * time and network access is injected, so the rules are tested without a server or a clock. The server states
 * (never invented here) arrive as `OfficePresenceItem`s; this module only keeps them, orders them and ages them.
 */
import type { OfficePresenceDeltaFrame, OfficePresenceItem, OfficePresenceSnapshot, OfficePresenceSnapshotFrame } from '@anum/contracts';
import { PresenceApiError, classifyPresenceDelta, stateFromSnapshot, type PresenceState } from './officePresenceClient.ts';

export type PresenceLink = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'offline' | 'denied' | 'missing';

export const PRESENCE_TIMING = {
  /** The server's staleness window (office_presence.STALE_AFTER). */
  staleAfterMs: 5 * 60_000,
  /** The server sends a keep-alive about every 15 s; this much silence means the link is dead. */
  silenceMs: 45_000,
  /** A stream that sends no snapshot in this time is treated as blocked (a proxy that buffers it). */
  firstFrameMs: 8_000,
  backoffBaseMs: 1_000,
  backoffMaxMs: 30_000,
  /** Consecutive failures before the page falls back to polling. */
  pollAfterFailures: 3,
  pollMs: 10_000,
  /** Reads triggered by shell events are at least this far apart. */
  pollMinGapMs: 2_000,
  /** While polling, try the stream again this often. */
  probeMs: 60_000,
  /** After a planned end of the stream (the server bounds its lifetime), reconnect at once. */
  planned: 300,
  reconnectingAfterMs: 3_000,
  resyncLimit: 5,
  resyncWindowMs: 60_000,
} as const;

export interface StreamResponse {
  status: number;
  contentType: string | null;
  read(): Promise<{ done: boolean; value?: string }>;
  cancel(): void;
}

export interface PresenceHubDeps {
  openStream(lastEventId: string | null, signal: AbortSignal): Promise<StreamResponse>;
  loadSnapshot(signal: AbortSignal): Promise<OfficePresenceSnapshot>;
  scope(): { tenantId: string; workspaceId: string };
  now(): number;
  /** Runs `fn` after `ms`; returns the function that cancels it. */
  setTimer(fn: () => void, ms: number): () => void;
  random(): number;
}

export interface PresenceHubView {
  link: PresenceLink;
  state: PresenceState | null;
  /** Client time when `state` last changed. */
  receivedAt: number;
  /** Client time of the last sign of life from the server (frame, keep-alive or poll answer); 0 when none. */
  contactAt: number;
  /** Counts every change, so a subscriber can tell a new view from the same one. */
  version: number;
}

// ---- Server-sent events, incrementally --------------------------------------------------

export type SseMessage = { kind: 'comment' } | { kind: 'frame'; event: string; id: string | null; data: string };
const MAX_BUFFER = 2_000_000;

export function createSseParser() {
  let buffer = '';
  return {
    push(chunk: string): SseMessage[] {
      buffer += chunk.replace(/\r\n?/g, '\n');
      if (buffer.length > MAX_BUFFER) throw new PresenceApiError('The presence stream sent an oversized frame');
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() ?? '';
      const out: SseMessage[] = [];
      for (const block of blocks) {
        if (block === '') continue;
        let event = 'message', id: string | null = null, data: string[] = [], comment = false, fields = false;
        for (const line of block.split('\n')) {
          if (line.startsWith(':')) { comment = true; continue; }
          const at = line.indexOf(':');
          const name = at < 0 ? line : line.slice(0, at);
          let value = at < 0 ? '' : line.slice(at + 1);
          if (value.startsWith(' ')) value = value.slice(1);
          fields = true;
          if (name === 'event') event = value;
          else if (name === 'id') id = value;
          else if (name === 'data') data.push(value);
        }
        if (fields && data.length) out.push({ kind: 'frame', event, id, data: data.join('\n') });
        else if (comment && !fields) out.push({ kind: 'comment' });
      }
      return out;
    },
  };
}

// ---- Staleness ----------------------------------------------------------------------------

/**
 * Items as they should be drawn now. The server marks an item stale only when it builds a frame, and an unchanged item
 * is not sent again, so the browser ages items itself, on the server's clock (the frame's `as_of` plus the time since
 * it arrived: a wrong device clock changes nothing). An item the server exempts (`provenance.kind === 'none'`: idle,
 * and a desk whose agent is hidden) is stale only when the link itself went silent for longer than the window.
 */
export function presenceWithStaleness(view: Pick<PresenceHubView, 'state' | 'receivedAt' | 'contactAt'>, nowMs: number): OfficePresenceItem[] | null {
  const state = view.state;
  if (!state) return null;
  const asOf = Date.parse(state.asOf);
  const serverNow = Number.isFinite(asOf) ? asOf + Math.max(0, nowMs - view.receivedAt) : NaN;
  const silent = view.contactAt > 0 && nowMs - view.contactAt > PRESENCE_TIMING.staleAfterMs;
  return [...state.items.values()].map((item) => {
    const verified = Date.parse(item.last_verified_at);
    const aged = Number.isFinite(serverNow) && Number.isFinite(verified) && item.provenance.kind !== 'none' && serverNow - verified > PRESENCE_TIMING.staleAfterMs;
    const stale = item.stale || aged || silent;
    return stale === item.stale ? item : { ...item, stale };
  });
}

function sameItems(a: PresenceState, b: PresenceState): boolean {
  if (a.items.size !== b.items.size) return false;
  for (const [key, item] of a.items) {
    const other = b.items.get(key);
    // An item with no record behind it (idle, a hidden desk) is "verified" at every read; that alone is not a change.
    const flat = (i: typeof item) => JSON.stringify(i.provenance.kind === 'none' ? { ...i, last_verified_at: '' } : i);
    if (!other || flat(other) !== flat(item)) return false;
  }
  return true;
}

const statusOf = (error: unknown): number | null => {
  const s = (error as { status?: unknown } | null)?.status;
  return typeof s === 'number' ? s : null;
};
const ID = /^[A-Za-z0-9._:-]{1,120}$/;

// ---- The hub ------------------------------------------------------------------------------

export class PresenceHub {
  private view: PresenceHubView = { link: 'connecting', state: null, receivedAt: 0, contactAt: 0, version: 0 };
  private readonly listeners = new Set<(view: PresenceHubView) => void>();
  private stopped = true;
  private gen = 0;
  private ctrl: AbortController | null = null;
  private lastEventId: string | null = null;
  private failures = 0;
  private mode: 'stream' | 'poll' = 'stream';
  private timers = new Set<() => void>();
  private watchdog: (() => void) | null = null;
  private firstFrame: (() => void) | null = null;
  private resyncs: number[] = [];
  private startScope: { tenantId: string; workspaceId: string } | null = null;

  private readonly deps: PresenceHubDeps;
  constructor(deps: PresenceHubDeps) { this.deps = deps; }

  getView(): PresenceHubView { return this.view; }
  subscribe(fn: (view: PresenceHubView) => void): () => void { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.startScope = this.deps.scope();
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.gen += 1;
    this.abort();
    this.clearTimers();
  }

  /** Reconnect now (the tab came back, or the network did): skips any waiting backoff. */
  kick(): void {
    if (this.stopped || this.view.link === 'denied' || this.view.link === 'missing') return;
    if (this.view.link === 'live') return;
    this.clearTimers();
    this.failures = 0;
    this.mode = 'stream';
    void this.connect();
  }

  /** Read the snapshot once more, for example after a shell event while the stream is down. */
  pollNow(): void {
    if (this.stopped || this.mode !== 'poll') return;
    // A burst of shell events must not become a burst of reads.
    if (this.deps.now() - this.lastPollAt < PRESENCE_TIMING.pollMinGapMs) return;
    void this.pollOnce(this.gen);
  }
  private lastPollAt = 0;

  // ---- internals ----

  private emit(patch: Partial<PresenceHubView>): void {
    const next = { ...this.view, ...patch, version: this.view.version + 1 };
    if (patch.link === this.view.link && Object.keys(patch).length === 1) return;
    this.view = next;
    for (const fn of [...this.listeners]) fn(next);
  }
  private setLink(link: PresenceLink): void { if (this.view.link !== link) this.emit({ link }); }
  private timer(ms: number, fn: () => void): () => void {
    const cancel = this.deps.setTimer(() => { this.timers.delete(cancel); fn(); }, ms);
    this.timers.add(cancel);
    return cancel;
  }
  private clearTimers(): void {
    for (const cancel of [...this.timers]) cancel();
    this.timers.clear();
    this.watchdog = null;
    this.firstFrame = null;
  }
  private abort(): void { try { this.ctrl?.abort(); } catch { /* already closed */ } this.ctrl = null; }
  private sameScope(): boolean {
    const now = this.deps.scope(), was = this.startScope;
    return !!was && now.tenantId === was.tenantId && now.workspaceId === was.workspaceId;
  }

  private touch(gen: number): void {
    if (gen !== this.gen) return;
    this.view = { ...this.view, contactAt: this.deps.now() };
    this.watchdog?.();
    this.watchdog = this.timer(PRESENCE_TIMING.silenceMs, () => this.fail(gen, 'silent'));
  }

  private apply(next: PresenceState): void {
    const current = this.view.state;
    if (current && Date.parse(next.asOf) < Date.parse(current.asOf)) return; // an older answer arriving late
    const now = this.deps.now();
    if (current && sameItems(current, next)) {
      // Nothing changed (a reconnect's snapshot usually repeats the last one): refresh the clocks and keep the screen as it is.
      this.view = { ...this.view, state: next, receivedAt: now, contactAt: now };
      return;
    }
    this.emit({ state: next, receivedAt: now, contactAt: now });
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    if (!this.sameScope()) { this.stop(); return; }
    const gen = ++this.gen;
    this.clearTimers();
    this.abort();
    const ctrl = new AbortController();
    this.ctrl = ctrl;
    const probing = this.mode === 'poll';
    let sawSnapshot = false;
    if (!probing) {
      const wasLive = this.view.link === 'live';
      this.setLink(wasLive ? 'live' : this.view.state || this.failures > 0 ? 'reconnecting' : 'connecting');
      // A planned end shows "Live" for a moment; if the new stream is slow to come, say so.
      if (wasLive) this.timer(PRESENCE_TIMING.reconnectingAfterMs, () => { if (gen === this.gen && !sawSnapshot) this.setLink('reconnecting'); });
    }
    const parser = createSseParser();
    this.firstFrame = this.timer(PRESENCE_TIMING.firstFrameMs, () => { if (!sawSnapshot) this.fail(gen, 'blocked'); });
    try {
      const res = await this.deps.openStream(this.lastEventId && ID.test(this.lastEventId) ? this.lastEventId : null, ctrl.signal);
      if (gen !== this.gen) { res.cancel(); return; }
      if (res.status === 401 || res.status === 403) { res.cancel(); this.finish('denied'); return; }
      // A server without the stream (or without its database) will not get one by retrying: read the snapshot instead.
      if (res.status === 404 || res.status === 405 || res.status === 501 || res.status === 503) { res.cancel(); this.failures = PRESENCE_TIMING.pollAfterFailures - 1; this.fail(gen, 'unsupported'); return; }
      if (res.status < 200 || res.status >= 300) { res.cancel(); this.fail(gen, 'status'); return; }
      if (res.contentType !== null && !/text\/event-stream/i.test(res.contentType)) { res.cancel(); this.fail(gen, 'blocked'); return; }
      this.touch(gen);
      for (;;) {
        const { done, value } = await res.read();
        if (gen !== this.gen) { res.cancel(); return; }
        if (done) break;
        this.touch(gen);
        for (const message of parser.push(value ?? '')) {
          if (message.kind === 'comment') continue;
          const outcome = this.handle(gen, message, sawSnapshot);
          if (outcome === 'snapshot') {
            sawSnapshot = true;
            this.firstFrame?.(); this.firstFrame = null;
            this.failures = 0;
            this.mode = 'stream';
            this.setLink('live');
          } else if (outcome === 'resync') {
            res.cancel();
            this.resync(gen);
            return;
          }
        }
      }
      // The stream ended. The server bounds its lifetime on purpose: after a snapshot this is not a failure.
      if (gen !== this.gen) return;
      res.cancel();
      if (sawSnapshot) {
        this.clearTimers();
        this.timer(PRESENCE_TIMING.planned, () => { void this.connect(); });
      } else this.fail(gen, 'ended');
    } catch (error) {
      if (gen !== this.gen) return;
      const status = statusOf(error);
      if (status === 401 || status === 403) { this.finish('denied'); return; }
      this.fail(gen, 'error');
    }
  }

  /** Applies one frame. Returns 'snapshot' for a snapshot, 'resync' when the state cannot be trusted. */
  private handle(gen: number, message: Extract<SseMessage, { kind: 'frame' }>, sawSnapshot: boolean): 'ok' | 'snapshot' | 'resync' {
    if (gen !== this.gen) return 'ok';
    let body: unknown;
    try { body = JSON.parse(message.data); } catch { return 'ok'; } // a broken frame is skipped; a gap will show if it mattered
    if (message.event === 'snapshot') {
      try {
        const frame = body as OfficePresenceSnapshotFrame;
        const state = stateFromSnapshot(frame, 0);
        if (state.workspaceId !== this.startScope?.workspaceId) return 'resync';
        if (message.id && ID.test(message.id)) this.lastEventId = message.id;
        else if (state.cursor && ID.test(state.cursor)) this.lastEventId = state.cursor;
        this.apply(state);
        return 'snapshot';
      } catch { return 'resync'; }
    }
    if (message.event === 'delta') {
      const state = this.view.state;
      if (!sawSnapshot || !state) return 'resync';
      const outcome = classifyPresenceDelta(state, body as OfficePresenceDeltaFrame);
      if (outcome.kind === 'duplicate') return 'ok';
      if (outcome.kind === 'gap') return 'resync';
      if (message.id && ID.test(message.id)) this.lastEventId = message.id;
      this.apply(outcome.state);
    }
    return 'ok';
  }

  private resync(gen: number): void {
    if (gen !== this.gen) return;
    const now = this.deps.now();
    this.resyncs = this.resyncs.filter((at) => now - at < PRESENCE_TIMING.resyncWindowMs);
    this.resyncs.push(now);
    if (this.resyncs.length > PRESENCE_TIMING.resyncLimit) { this.fail(gen, 'resync'); return; }
    void this.connect();
  }

  private fail(gen: number, _why: string): void {
    if (gen !== this.gen || this.stopped) return;
    this.gen += 1; // later callbacks of this attempt are ignored
    this.abort();
    this.clearTimers();
    if (this.mode === 'poll') { this.schedulePoll(); this.scheduleProbe(); return; } // a failed probe: stay polling
    this.failures += 1;
    if (this.failures >= PRESENCE_TIMING.pollAfterFailures) { this.enterPolling(); return; }
    this.setLink('reconnecting');
    const exp = Math.min(PRESENCE_TIMING.backoffMaxMs, PRESENCE_TIMING.backoffBaseMs * 2 ** (this.failures - 1));
    const delay = Math.round(exp * (0.8 + this.deps.random() * 0.4));
    this.timer(delay, () => { void this.connect(); });
  }

  private finish(link: 'denied' | 'missing'): void {
    this.gen += 1;
    this.abort();
    this.clearTimers();
    this.stopped = true;
    this.setLink(link);
  }

  private enterPolling(): void {
    this.mode = 'poll';
    this.setLink(this.view.state ? 'polling' : 'connecting');
    void this.pollOnce(this.gen);
    this.scheduleProbe();
  }

  private pollCancel: (() => void) | null = null;
  private schedulePoll(): void {
    this.pollCancel?.();
    this.pollCancel = this.timer(PRESENCE_TIMING.pollMs, () => { void this.pollOnce(this.gen); });
  }
  private scheduleProbe(): void {
    this.timer(PRESENCE_TIMING.probeMs, () => { if (this.mode === 'poll') void this.connect(); });
  }

  private async pollOnce(gen: number): Promise<void> {
    if (this.stopped || gen !== this.gen || this.mode !== 'poll') return;
    const ctrl = new AbortController();
    this.lastPollAt = this.deps.now();
    try {
      const snapshot = await this.deps.loadSnapshot(ctrl.signal);
      if (this.stopped || gen !== this.gen || this.mode !== 'poll') return;
      const state = stateFromSnapshot(snapshot, 0);
      if (state.workspaceId !== this.startScope?.workspaceId || !this.sameScope()) throw new PresenceApiError('Presence belongs to another workspace');
      this.apply(state);
      this.setLink('polling');
    } catch (error) {
      if (this.stopped || gen !== this.gen) return;
      const status = statusOf(error);
      if (status === 401 || status === 403) { this.finish('denied'); return; }
      if (status === 404 || status === 405 || status === 501 || status === 503) { this.finish('missing'); return; }
      this.setLink('offline');
    }
    if (this.stopped || gen !== this.gen) return;
    // One poll chain only: replace a waiting timer.
    this.schedulePoll();
  }
}

// ---- One hub for the whole page -----------------------------------------------------------

let shared: { key: string; hub: PresenceHub; refs: number } | null = null;

/**
 * Every part of the headquarters page asks for the same hub, so there is one stream, however many parts use presence
 * (earlier work showed several streams broke long sessions). The last release stops it.
 */
export function acquirePresenceHub(key: string, make: () => PresenceHub): { hub: PresenceHub; release: () => void } {
  if (!shared || shared.key !== key) {
    shared?.hub.stop();
    shared = { key, hub: make(), refs: 0 };
  }
  const entry = shared;
  entry.refs += 1;
  entry.hub.start();
  let released = false;
  return {
    hub: entry.hub,
    release: () => {
      if (released) return;
      released = true;
      entry.refs -= 1;
      if (entry.refs <= 0) { entry.hub.stop(); if (shared === entry) shared = null; }
    },
  };
}

export function presenceHubCount(): number { return shared ? shared.refs : 0; }
