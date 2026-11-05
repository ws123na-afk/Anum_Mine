/**
 * Record-backed agent walkers on the campus (docs/ANUM_MINE_HQ_AGENT_WALKERS.md).
 *
 * A figure of an agent walks from one building's door to another's only because a stored record says that agent moved:
 * a meeting with a room in another building started or ended with the agent as a participant, or the presence answer
 * puts the agent's desk in another building than the answer before. Nothing here invents a person, a crowd, a count or a
 * reason. A record carries only an agent id, two building keys, an instant and the id of the place (a room or a building);
 * the words on screen are a message template filled with the time and a place NAME read from the office registry, so a
 * meeting title, a transcript, a prompt or a task title cannot reach a string through this file.
 *
 * Pure: no DOM, no three.js, no clock of its own (callers pass `nowMs`).
 */
import type { Locale } from './i18n.ts';
import { whenText } from './hqWorkMotion.ts';
import type { MessageKey } from './messages/en.ts';

export type WalkerKind = 'meeting_started' | 'meeting_ended' | 'desk_moved';
export const WALKER_KINDS: readonly WalkerKind[] = ['meeting_started', 'meeting_ended', 'desk_moved'];

/** At most this many figures at once; the rest wait in a queue (and the screen says how many wait, only when some do). */
export const MAX_WALKERS = 12;
/** A record older than this is history, not an event. */
export const LIVE_WINDOW_MS = 30 * 60_000;
/** A walker that has waited longer than this for a free place is dropped (its event is no longer news). */
export const QUEUE_TTL_MS = 120_000;
export const QUEUE_CAP = 200;
/** How long the "Because" line stays after the walk, and how long the static marker of reduced motion stays. */
export const LINGER_MS = 6_000;
export const STATIC_MS = 60_000;
/** Seconds the walk takes (door to door, by path length) and the pause at the destination door. Total stays between 10 and 25 s. */
export const WALK_MIN_S = 9;
export const WALK_MAX_S = 23;
export const PAUSE_S = 1.6;
/** Metres a second the path length is divided by before the clamp: the campus is drawn small, so figures are drawn larger than life (see WALKER_SCALE). */
export const WALK_SPEED = 4.5;
/** Figures are drawn this much larger than life so they read from the campus camera. */
export const WALKER_SCALE = 3.5;
/** Metres of path for one full step cycle (two steps), at the drawn size. */
export const CYCLE_M = 5;

/** What a record may carry. `recordId` only de-duplicates and is never shown. */
export type WalkerRecord = Readonly<{
  kind: WalkerKind;
  agentId: string;
  /** Campus building keys (scene keys of registry buildings). */
  from: string;
  to: string;
  /** When the record says it happened (ISO instant). */
  at: string;
  /** The room (meetings) or the building (desk moved) the "Because" line names, looked up in the registry by id. */
  placeId: string;
  recordId: string;
}>;

const isoMs = (value: unknown): number => (typeof value === 'string' && value.length >= 10 && value.length <= 40 ? Date.parse(value) : NaN);

// ---------------------------------------------------------------- topology: which building holds which room, desk and agent

export type TopoBuilding = Readonly<{
  id: string;
  floors: ReadonlyArray<Readonly<{
    rooms: ReadonlyArray<Readonly<{ id: string; kind?: string | null }>>;
    desks: ReadonlyArray<Readonly<{ id: string; roomId: string; agentId: string | null }>>;
  }>>;
}>;

export type Topology = Readonly<{
  /** Scene keys of the registry buildings the campus draws. Empty when the workspace has fewer than two (nothing can walk between them). */
  known: ReadonlySet<string>;
  roomBuilding: ReadonlyMap<string, string>;
  deskBuilding: ReadonlyMap<string, string>;
  /** The building where an agent's desks are. An agent with desks in two buildings has none (the data cannot say where it sits). */
  home: ReadonlyMap<string, string>;
  /** The registry kind of the room of an agent's desk, for the figure's role family. */
  homeRoomKind: ReadonlyMap<string, string>;
}>;

export const EMPTY_TOPOLOGY: Topology = { known: new Set(), roomBuilding: new Map(), deskBuilding: new Map(), home: new Map(), homeRoomKind: new Map() };

/** Same format as `registryKey` in campusRegistry.ts (a test pins it). */
export const sceneKeyOf = (workspaceId: string, buildingId: string): string => `${workspaceId}~${buildingId}`;

export function topologyOf(workspaceId: string, registry: readonly TopoBuilding[]): Topology {
  if (registry.length < 2) return EMPTY_TOPOLOGY;
  const known = new Set<string>();
  const roomBuilding = new Map<string, string>();
  const deskBuilding = new Map<string, string>();
  const roomKinds = new Map<string, string>();
  const home = new Map<string, string>();
  const homeRoomKind = new Map<string, string>();
  const ambiguous = new Set<string>();
  for (const b of registry) {
    const key = sceneKeyOf(workspaceId, b.id);
    known.add(key);
    for (const f of b.floors) {
      for (const r of f.rooms) { roomBuilding.set(r.id, key); if (r.kind) roomKinds.set(r.id, r.kind); }
      for (const d of f.desks) {
        deskBuilding.set(d.id, key);
        if (!d.agentId) continue;
        const was = home.get(d.agentId);
        if (was !== undefined && was !== key) ambiguous.add(d.agentId);
        else { home.set(d.agentId, key); const kind = roomKinds.get(d.roomId); if (kind) homeRoomKind.set(d.agentId, kind); }
      }
    }
  }
  for (const id of ambiguous) { home.delete(id); homeRoomKind.delete(id); }
  return { known, roomBuilding, deskBuilding, home, homeRoomKind };
}

// ---------------------------------------------------------------- records

/** Builds a record, or null when anything is outside what a record may carry. */
export function makeRecord(
  input: Readonly<{ kind: unknown; agentId: unknown; from: unknown; to: unknown; at: unknown; placeId: unknown; recordKey: unknown }>,
  topo: Topology, nowMs: number,
): WalkerRecord | null {
  const kind = WALKER_KINDS.find((k) => k === input.kind);
  if (!kind) return null;
  const { agentId, from, to, placeId, recordKey } = input;
  if (typeof agentId !== 'string' || agentId.length === 0 || agentId.length > 200 || agentId.startsWith('desk:')) return null;
  if (typeof from !== 'string' || typeof to !== 'string' || from === to || !topo.known.has(from) || !topo.known.has(to)) return null;
  if (typeof placeId !== 'string' || typeof recordKey !== 'string' || recordKey.length === 0 || recordKey.length > 300) return null;
  // The named place must really be in the building the walk goes to (a room of the meeting, or the building itself).
  if (kind === 'meeting_started' && topo.roomBuilding.get(placeId) !== to) return null;
  if (kind === 'meeting_ended' && topo.roomBuilding.get(placeId) !== from) return null;
  if (kind === 'desk_moved' && placeId !== to) return null;
  const at = input.at as string;
  const when = isoMs(at);
  if (!Number.isFinite(when) || when > nowMs + 5 * 60_000 || nowMs - when > LIVE_WINDOW_MS) return null;
  return { kind, agentId, from, to, at, placeId, recordId: `${kind}:${agentId}:${recordKey}` };
}

export type MeetingEntry = Readonly<{ roomId: string; agentIds: readonly string[]; startedAt: string | null }>;
/** What the presence answer says about one agent: where its desk is and whether the state may be drawn at all. */
export type SeatEntry = Readonly<{ deskId: string | null; verifiedAt: string; eligible: boolean }>;
export type WalkSnapshot = Readonly<{
  /** Live meetings by meeting id, or null when the meeting feed was not readable. */
  meetings: ReadonlyMap<string, MeetingEntry> | null;
  /** Presence by agent id, or null when no presence was read. */
  seats: ReadonlyMap<string, SeatEntry> | null;
}>;

/** An agent may be drawn only when a presence record exists and says something drawable (not paused, not unknown, not stale, not private). */
export function eligibleState(p: Readonly<{ agentId: string; status: string; stale?: boolean; reason?: string | null }>): boolean {
  return !p.agentId.startsWith('desk:') && p.status !== 'paused' && p.status !== 'unknown' && p.stale !== true && p.reason !== 'agent_not_visible';
}

/** Meetings that appear in `next` and not in `prev` (and were never seen live before, `seen`): each participant whose desk is in another building walks to the meeting room. */
export function startedRecords(prev: WalkSnapshot | null, next: WalkSnapshot, topo: Topology, nowMs: number, seen?: ReadonlySet<string>): WalkerRecord[] {
  if (!prev?.meetings || !next.meetings || !next.seats) return [];
  const out: WalkerRecord[] = [];
  for (const [id, m] of next.meetings) {
    // A meeting that was live earlier (it was paused and is live again) is not a new start.
    if (prev.meetings.has(id) || seen?.has(id)) continue;
    const to = topo.roomBuilding.get(m.roomId);
    if (!to || !m.startedAt) continue;
    for (const agentId of m.agentIds) {
      const seat = next.seats.get(agentId);
      const from = topo.home.get(agentId);
      if (!seat?.eligible || !from) continue;
      const r = makeRecord({ kind: 'meeting_started', agentId, from, to, at: m.startedAt, placeId: m.roomId, recordKey: id }, topo, nowMs);
      if (r) out.push(r);
    }
  }
  return out;
}

/** Ids of meetings that were live in `prev` and are not in `next`: the caller asks the meeting record why (ended, or only paused). */
export function vanishedMeetings(prev: WalkSnapshot | null, next: WalkSnapshot): string[] {
  if (!prev?.meetings || !next.meetings) return [];
  return [...prev.meetings.keys()].filter((id) => !next.meetings!.has(id));
}

const TERMINAL = new Set(['finished', 'cancelled', 'failed']);

/** A meeting that is over (the record says finished, cancelled or failed, with the time): its participants walk back to their desks' building. */
export function endedRecords(
  meetingId: string, entry: MeetingEntry, detail: Readonly<{ status?: unknown; ended_at?: unknown }> | null,
  seats: ReadonlyMap<string, SeatEntry> | null, topo: Topology, nowMs: number,
): WalkerRecord[] {
  if (!detail || typeof detail.status !== 'string' || !TERMINAL.has(detail.status) || !seats) return [];
  const from = topo.roomBuilding.get(entry.roomId);
  if (!from) return [];
  const out: WalkerRecord[] = [];
  for (const agentId of entry.agentIds) {
    const to = topo.home.get(agentId);
    if (!seats.get(agentId)?.eligible || !to) continue;
    const r = makeRecord({ kind: 'meeting_ended', agentId, from, to, at: detail.ended_at, placeId: entry.roomId, recordKey: meetingId }, topo, nowMs);
    if (r) out.push(r);
  }
  return out;
}

/** An agent whose desk (as the presence answer reports it) is in another building than in the answer before. Never for an agent whose desks are in two buildings. */
export function movedRecords(prev: WalkSnapshot | null, next: WalkSnapshot, topo: Topology, nowMs: number): WalkerRecord[] {
  if (!prev?.seats || !next.seats) return [];
  const out: WalkerRecord[] = [];
  for (const [agentId, seat] of next.seats) {
    const before = prev.seats.get(agentId);
    if (!seat.eligible || !seat.deskId || !before?.deskId || before.deskId === seat.deskId) continue;
    // An agent seated in two buildings (a day and a night desk) is reported at either one: the data cannot say where it is, so nothing is drawn.
    if (!topo.home.has(agentId)) continue;
    const from = topo.deskBuilding.get(before.deskId), to = topo.deskBuilding.get(seat.deskId);
    if (!from || !to) continue;
    const r = makeRecord({ kind: 'desk_moved', agentId, from, to, at: seat.verifiedAt, placeId: to, recordKey: `${before.deskId}>${seat.deskId}@${seat.verifiedAt}` }, topo, nowMs);
    if (r) out.push(r);
  }
  return out;
}

// ---------------------------------------------------------------- the "Because" line

const BECAUSE: Readonly<Record<WalkerKind, MessageKey>> = {
  meeting_started: 'hqWalkers.because.meeting_started', meeting_ended: 'hqWalkers.because.meeting_ended', desk_moved: 'hqWalkers.because.desk_moved',
};

/** "14:05" in the person's language (Western digits), the same text the campus work motion prints. Empty when the value does not parse. */
export const timeText = (at: string, locale: Locale): string => whenText(at, false, locale);

export type PlaceNames = Readonly<{ room: (id: string) => string; building: (key: string) => string }>;

/** The only words a walker may put on screen: a template per kind, the time and the name of a registry place. */
export function becauseLine(record: Pick<WalkerRecord, 'kind' | 'at' | 'placeId'>, names: PlaceNames, locale: Locale, translate: (key: MessageKey, vars?: Record<string, string>) => string): string {
  const raw = record.kind === 'desk_moved' ? names.building(record.placeId) : names.room(record.placeId);
  const place = raw.trim() || translate(record.kind === 'desk_moved' ? 'hqWalkers.place.building' : 'hqWalkers.place.room');
  return translate(BECAUSE[record.kind], { time: timeText(record.at, locale), place });
}

// ---------------------------------------------------------------- routes along the walkways

export type SiteGeo = Readonly<{ key: string; door: Readonly<{ x: number; z: number }>; box: Readonly<{ minX: number; minZ: number; maxX: number; maxZ: number }> }>;
export type SpokeLike = Readonly<{ id: string; points: ReadonlyArray<readonly [number, number]> }>;

/** A polyline on the ground (x, z pairs) with the data the pose function needs. `pts[0]` is the facade A, `doorAt` and `arriveAt` index the doors. */
export type WalkRoute = Readonly<{
  pts: Float32Array;
  cum: Float32Array;
  length: number;
  /** Distance along the route at door A and at door B (where the figure pauses). */
  doorA: number;
  arriveAt: number;
  walkSec: number;
  totalSec: number;
  speed: number;
}>;

const facadeOf = (s: SiteGeo): [number, number] => [Math.min(s.box.maxX, Math.max(s.box.minX, s.door.x)), Math.min(s.box.maxZ, Math.max(s.box.minZ, s.door.z))];

/**
 * From the facade of building A out of its door, along A's walkway to the roundabout's pavement, round the pavement the short way,
 * along B's walkway to B's door and in to B's facade. The walkways are the campus's own (`campusSpokes.routeSpokes`, which end exactly
 * on the doors), so the route leaves no existing path. Null when either building has no walkway.
 */
export function buildRoute(from: SiteGeo, to: SiteGeo, spokes: ReadonlyMap<string, SpokeLike>): WalkRoute | null {
  if (from.key === to.key) return null;
  const sa = spokes.get(from.key), sb = spokes.get(to.key);
  if (!sa || !sb || sa.points.length < 2 || sb.points.length < 2) return null;
  const pts: Array<readonly [number, number]> = [];
  const push = (p: readonly [number, number]) => { const q = pts[pts.length - 1]; if (!q || Math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-6) pts.push(p); };
  push(facadeOf(from));
  const a = [...sa.points].reverse();           // door A first, the pavement circle last
  a.forEach(push);
  const doorAIndex = pts.length - a.length;     // index of door A in pts
  const ca = a[a.length - 1], cb = sb.points[0];
  const ra = Math.hypot(ca[0], ca[1]), rb = Math.hypot(cb[0], cb[1]);
  const a0 = Math.atan2(ca[1], ca[0]);
  let d = Math.atan2(cb[1], cb[0]) - a0;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  const steps = Math.max(0, Math.ceil(Math.abs(d) / (Math.PI / 36)));
  for (let i = 1; i < steps; i++) { const f = i / steps, ang = a0 + d * f, r = ra + (rb - ra) * f; push([Math.cos(ang) * r, Math.sin(ang) * r]); }
  sb.points.forEach(push);
  const doorBIndex = pts.length - 1;
  push(facadeOf(to));
  if (pts.length < 2 || doorBIndex <= doorAIndex) return null;
  const flat = new Float32Array(pts.length * 2);
  const cum = new Float32Array(pts.length);
  for (let i = 0; i < pts.length; i++) {
    flat[i * 2] = pts[i][0]; flat[i * 2 + 1] = pts[i][1];
    if (i) cum[i] = cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  const length = cum[cum.length - 1];
  if (!(length > 1)) return null;
  const walkSec = Math.min(WALK_MAX_S, Math.max(WALK_MIN_S, length / WALK_SPEED));
  return { pts: flat, cum, length, doorA: cum[doorAIndex], arriveAt: cum[doorBIndex], walkSec, totalSec: walkSec + PAUSE_S, speed: length / walkSec };
}

export type WalkPose = { x: number; z: number; heading: number; phase: number; moving: boolean; visible: boolean; grow: number };
export const newWalkPose = (): WalkPose => ({ x: 0, z: 0, heading: 0, phase: 0, moving: false, visible: false, grow: 1 });

function at(route: WalkRoute, s: number, out: { x: number; z: number }): void {
  const { cum, pts } = route;
  const c = Math.min(route.length, Math.max(0, s));
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= c) lo = mid; else hi = mid; }
  const seg = cum[hi] - cum[lo] || 1, f = (c - cum[lo]) / seg;
  out.x = pts[lo * 2] + (pts[hi * 2] - pts[lo * 2]) * f;
  out.z = pts[lo * 2 + 1] + (pts[hi * 2 + 1] - pts[lo * 2 + 1]) * f;
}
const p0 = { x: 0, z: 0 }, p1 = { x: 0, z: 0 };

/** Where the figure is `t` seconds after it started: walks at constant speed, pauses at door B, walks in and is gone at the facade. */
export function walkPoseAt(route: WalkRoute, t: number, out: WalkPose): WalkPose {
  const tArrive = route.arriveAt / route.speed;
  let s: number;
  out.moving = true;
  if (t < tArrive) s = route.speed * t;
  else if (t < tArrive + PAUSE_S) { s = route.arriveAt; out.moving = false; }
  else s = route.arriveAt + route.speed * (t - tArrive - PAUSE_S);
  out.visible = t >= 0 && t < route.totalSec && s < route.length - 1e-6;
  at(route, s, p0);
  out.x = p0.x; out.z = p0.z;
  // Heading: along the path (a short look-ahead, and look-behind at the very end); at the pause it faces the building.
  const sa = Math.min(route.length, s + 0.8), sb = Math.max(0, s - 0.8);
  at(route, out.moving ? sa : Math.min(route.length, route.arriveAt + 0.8), p0);
  at(route, out.moving ? sb : Math.max(0, route.arriveAt - 0.1), p1);
  const dx = p0.x - p1.x, dz = p0.z - p1.z;
  if (dx * dx + dz * dz > 1e-8) out.heading = Math.atan2(dx, dz);
  out.phase = (s / CYCLE_M) * Math.PI * 2;
  out.grow = Math.min(1, 0.3 + t / 0.5);
  return out;
}

/** Where a reduced-motion marker stands: at the destination door, facing the building, not moving. */
export function restPose(route: WalkRoute, out: WalkPose): WalkPose {
  walkPoseAt(route, route.arriveAt / route.speed + PAUSE_S / 2, out);
  out.visible = true; out.moving = false; out.grow = 1;
  return out;
}

// ---------------------------------------------------------------- the planner

export type Walker = Readonly<{ id: string; record: WalkerRecord; startedMs: number; animate: boolean; route: WalkRoute; endMs: number; lineUntilMs: number }>;
type Waiting = { record: WalkerRecord; sinceMs: number };

export type AcceptOptions = Readonly<{ reducedMotion: boolean; route: (from: string, to: string) => WalkRoute | null }>;

/**
 * Keeps the walkers alive. At most MAX_WALKERS at once and one per agent: a record for an agent that is already walking waits (the newest
 * waiting one replaces an older waiting one). A record is shown once per page load (`seen`).
 */
export class WalkerPlanner {
  walkers: Walker[] = [];
  private queue: Waiting[] = [];
  private readonly seen = new Set<string>();
  private serial = 0;
  private opts: AcceptOptions | null = null;

  /** Real number of agents waiting for a place (0 when nobody waits). */
  get waiting(): number { return this.queue.length; }

  accept(records: readonly WalkerRecord[], nowMs: number, opts: AcceptOptions): boolean {
    this.opts = opts;
    let changed = false;
    for (const record of records) {
      if (this.seen.has(record.recordId)) continue;
      if (!opts.route(record.from, record.to)) continue;
      this.seen.add(record.recordId);
      const waitingIndex = this.queue.findIndex((w) => w.record.agentId === record.agentId);
      if (waitingIndex >= 0) this.queue.splice(waitingIndex, 1);
      if (this.queue.length >= QUEUE_CAP) this.queue.shift();
      this.queue.push({ record, sinceMs: nowMs });
      changed = true;
    }
    return this.promote(nowMs) || changed;
  }

  private promote(nowMs: number): boolean {
    const opts = this.opts;
    if (!opts) return false;
    let changed = false;
    for (let i = 0; i < this.queue.length && this.walkers.length < MAX_WALKERS;) {
      const w = this.queue[i];
      if (nowMs - w.sinceMs > QUEUE_TTL_MS) { this.queue.splice(i, 1); changed = true; continue; }
      if (this.walkers.some((x) => x.record.agentId === w.record.agentId)) { i += 1; continue; }
      const route = opts.route(w.record.from, w.record.to);
      this.queue.splice(i, 1);
      changed = true;
      if (!route) continue;
      const animate = !opts.reducedMotion;
      const endMs = nowMs + (animate ? route.totalSec * 1000 : STATIC_MS);
      this.serial += 1;
      this.walkers.push({ id: `${w.record.recordId}#${this.serial}`, record: w.record, startedMs: nowMs, animate, route, endMs, lineUntilMs: animate ? endMs + LINGER_MS : endMs });
    }
    return changed;
  }

  /** Drops walkers whose time is over, then lets waiting ones in. True when anything changed. */
  prune(nowMs: number): boolean {
    const before = this.walkers.length;
    this.walkers = this.walkers.filter((w) => nowMs < w.lineUntilMs);
    // The drawn figure ends with the walk; the line lingers, so a walker past its walk stays listed for the line only.
    return this.promote(nowMs) || this.walkers.length !== before;
  }

  /** Walkers whose figure is still drawn. */
  drawn(nowMs: number): Walker[] { return this.walkers.filter((w) => nowMs < w.endMs); }

  /** The next instant something changes (a walk ends, a line ends, a queued entry expires), or null. */
  nextEnd(nowMs: number): number | null {
    let next: number | null = null;
    const take = (v: number) => { if (v > nowMs && (next === null || v < next)) next = v; };
    for (const w of this.walkers) { take(w.endMs); take(w.lineUntilMs); }
    for (const w of this.queue) take(w.sinceMs + QUEUE_TTL_MS);
    return next;
  }
}
