/**
 * Record-backed work motion on the campus (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md, layer 2).
 *
 * A vehicle drives into a building's yard, or a tyre rolls out of the door, only because one stored record
 * or one live event says something happened there. Nothing in this file invents a record, a name or a
 * number: a motion is built from a `WorkRecord`, whose only free fields are a kind from a fixed list, an
 * instant, an alert kind from a fixed list and the key of a building the campus really has. Everything the
 * screen says about a motion (the "Because" line) is a message template filled with those fields and no
 * others, so a title, a person, a customer, a vehicle or a count cannot reach the screen through here.
 *
 * Pure: no DOM, no three.js, no clock of its own (the caller passes `nowMs`). Poses are plain functions of
 * time written into a caller owned object.
 */
import { rotateXZ } from './campusFacing.ts';
import { intlLocale, type Locale } from './i18n.ts';
import type { MessageKey } from './messages/en.ts';

export type WorkKind = 'agent_working' | 'task_started' | 'task_done' | 'tyre_inspection' | 'tyre_alert';
export type WorkSource = 'presence' | 'flow' | 'tyre_pulse';
export type WorkVehicle = 'van' | 'truck' | 'hauler' | 'tyre';
export type BeaconColour = 'amber' | 'green' | 'red' | 'none';

export const WORK_KINDS: readonly WorkKind[] = ['agent_working', 'task_started', 'task_done', 'tyre_inspection', 'tyre_alert'];
export const ALERT_KINDS = ['low_tread', 'under_inflation', 'over_inflation', 'overdue_inspection', 'abnormal_wear', 'cost_anomaly'] as const;
export type WorkAlertKind = (typeof ALERT_KINDS)[number];

/** Which feed each kind of record comes from, which representative vehicle shows it, and how long the animation lasts (seconds). */
export const KIND_SOURCE: Readonly<Record<WorkKind, WorkSource>> = { agent_working: 'presence', task_started: 'flow', task_done: 'flow', tyre_inspection: 'tyre_pulse', tyre_alert: 'tyre_pulse' };
export const KIND_VEHICLE: Readonly<Record<WorkKind, WorkVehicle>> = { agent_working: 'van', task_started: 'truck', task_done: 'truck', tyre_inspection: 'tyre', tyre_alert: 'hauler' };
export const KIND_BEACON: Readonly<Record<WorkKind, BeaconColour>> = { agent_working: 'amber', task_started: 'amber', task_done: 'green', tyre_inspection: 'none', tyre_alert: 'red' };
export const KIND_SECONDS: Readonly<Record<WorkKind, number>> = { agent_working: 18, task_started: 18, task_done: 18, tyre_inspection: 13, tyre_alert: 20 };

/** The most animated motions at once, and how long a marker stays after its animation (or, without animation, in total). */
export const MAX_ANIMATED = 4;
export const LINGER_MS = 6_000;
export const STATIC_MS = 60_000;
/** A flow or presence record older than this is history, not a live event: no motion. */
export const LIVE_WINDOW_MS = 30 * 60_000;
/** Tyre Pulse records count for a day (a day-only inspection: until its day is over and a day has passed). */
export const TYRE_WINDOW_MS = 24 * 3_600_000;

/** What a record may carry. `recordId` only de-duplicates; it is never shown. */
export type WorkRecord = Readonly<{
  kind: WorkKind;
  buildingKey: string;
  /** When the record says it happened (ISO instant, or a calendar day for a Tyre Pulse inspection whose source keeps dates only). */
  at: string;
  day: boolean;
  alertKind: WorkAlertKind | null;
  recordId: string;
}>;

const isoMs = (value: unknown): number => (typeof value === 'string' && value.length >= 10 && value.length <= 40 ? Date.parse(value) : NaN);
const DAY_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$/;

/** A server value when it is an ISO 8601 instant with a zone that parses (packages/contracts/src/tyreEventTimes.ts), else null. */
export function tyreInstant(value: unknown): string | null {
  return typeof value === 'string' && INSTANT.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
}

/** A server value as a calendar day (its first ten characters, YYYY-MM-DD), else null. */
export function tyreDay(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const day = value.slice(0, 10);
  return DAY_ONLY.test(day) && Number.isFinite(Date.parse(day)) ? day : null;
}

/** Builds a record, or null when anything is outside what a record may carry (unknown kind or building, a time that does not parse or lies in the future). */
export function makeRecord(input: Readonly<{ kind: unknown; buildingKey: unknown; at: unknown; recordId: unknown; alertKind?: unknown }>, knownBuildings: ReadonlySet<string>, nowEpochMs: number): WorkRecord | null {
  const kind = WORK_KINDS.find((k) => k === input.kind);
  if (!kind) return null;
  if (typeof input.buildingKey !== 'string' || !knownBuildings.has(input.buildingKey)) return null;
  if (typeof input.recordId !== 'string' || input.recordId.length === 0 || input.recordId.length > 200) return null;
  const at = input.at as string;
  const when = isoMs(at);
  if (!Number.isFinite(when) || when > nowEpochMs + 5 * 60_000) return null;
  // Only a Tyre Pulse inspection may be a calendar day, and only when its source keeps no time; every other record is an instant.
  const day = kind === 'tyre_inspection' && DAY_ONLY.test(at);
  const age = nowEpochMs - when;
  // A day-only record is judged by its whole day: it is current until the day is over and a day has passed after it.
  if (kind === 'tyre_alert' || kind === 'tyre_inspection' ? age > TYRE_WINDOW_MS + (day ? 24 * 3_600_000 : 0) : age > LIVE_WINDOW_MS) return null;
  const alertKind = kind === 'tyre_alert' ? ALERT_KINDS.find((a) => a === input.alertKind) ?? null : null;
  return { kind, buildingKey: input.buildingKey, at, day, alertKind, recordId: `${kind}:${input.buildingKey}:${input.recordId}` };
}

// ---------------------------------------------------------------- the "Because" line

export type BecauseParts = Readonly<{ key: MessageKey; vars: Readonly<Record<string, string>> }>;

const BECAUSE: Readonly<Record<WorkKind, MessageKey>> = {
  agent_working: 'hqMotion.because.agent_working', task_started: 'hqMotion.because.task_started', task_done: 'hqMotion.because.task_done',
  tyre_inspection: 'hqMotion.because.tyre_inspection', tyre_alert: 'hqMotion.because.tyre_alert',
};
const ALERT_WORDS: Readonly<Record<WorkAlertKind, MessageKey>> = {
  low_tread: 'hqMotion.alert.low_tread', under_inflation: 'hqMotion.alert.under_inflation', over_inflation: 'hqMotion.alert.over_inflation',
  overdue_inspection: 'hqMotion.alert.overdue_inspection', abnormal_wear: 'hqMotion.alert.abnormal_wear', cost_anomaly: 'hqMotion.alert.cost_anomaly',
};

/** "14:05" for an instant, "26 Oct" for a calendar day, in the person's language (Western digits). Empty when the value does not parse. */
export function whenText(at: string, day: boolean, locale: Locale): string {
  const ms = isoMs(at);
  if (!Number.isFinite(ms)) return '';
  const lang = intlLocale(locale);
  return day
    ? new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(ms))
    : new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(ms));
}

/**
 * The only words a motion may put on screen: a template per kind, the time of the record and, for an alert, a word from a fixed list.
 * `translate` is the catalogue lookup, passed in so this file stays free of the catalogue.
 */
export function becauseLine(record: Pick<WorkRecord, 'kind' | 'at' | 'day' | 'alertKind'>, locale: Locale, translate: (key: MessageKey, vars?: Record<string, string>) => string): string {
  const vars: Record<string, string> = { time: whenText(record.at, record.day, locale) };
  if (record.kind === 'tyre_alert' && record.alertKind) vars.what = translate(ALERT_WORDS[record.alertKind]);
  return translate(record.kind === 'tyre_alert' && !record.alertKind ? 'hqMotion.because.tyre_alert_plain' : BECAUSE[record.kind], vars);
}

// ---------------------------------------------------------------- feeds to records

export type PresenceView = Readonly<{ key: string; working: number | null; verifiedAt: string | null }>;

/** A building whose presence says more agents are working than the last answer did. First answer: nothing (the past is not replayed). */
export function presenceRecords(previous: readonly PresenceView[] | null, next: readonly PresenceView[], known: ReadonlySet<string>, nowEpochMs: number): WorkRecord[] {
  if (!previous) return [];
  const before = new Map(previous.map((p) => [p.key, p.working]));
  const out: WorkRecord[] = [];
  for (const p of next) {
    const was = before.get(p.key);
    if (p.working === null || p.verifiedAt === null || was === undefined || was === null || p.working <= was) continue;
    const record = makeRecord({ kind: 'agent_working', buildingKey: p.key, at: p.verifiedAt, recordId: `${p.verifiedAt}` }, known, nowEpochMs);
    if (record) out.push(record);
  }
  return out;
}

export type FlowView = Readonly<{ key: string; kind: string; stage: string; since: string; buildingId: string | null }>;

/** Tasks that just started running in an office, and tasks that just finished, between two flow answers. First answer: nothing. */
export function flowRecords(previous: ReadonlyMap<string, string> | null, items: readonly FlowView[], buildingOf: (buildingId: string) => string | null, known: ReadonlySet<string>, nowEpochMs: number): WorkRecord[] {
  if (!previous) return [];
  const out: WorkRecord[] = [];
  for (const item of items) {
    if (item.kind !== 'task' || !item.buildingId) continue;
    const was = previous.get(item.key) ?? null;
    const kind: WorkKind | null = item.stage === 'in_office' && was !== 'in_office' ? 'task_started' : item.stage === 'done' && was !== null && was !== 'done' ? 'task_done' : null;
    if (!kind) continue;
    const building = buildingOf(item.buildingId);
    if (!building) continue;
    const record = makeRecord({ kind, buildingKey: building, at: item.since, recordId: `${item.key}:${item.since}` }, known, nowEpochMs);
    if (record) out.push(record);
  }
  return out;
}

export const stagesOf = (items: readonly FlowView[]): Map<string, string> => new Map(items.map((i) => [i.key, i.stage]));

/** What the Tyre Pulse reader gave the browser: only these fields are ever kept. Times are ISO instants or calendar days, or null when unknown. */
export type TyreFeed =
  | Readonly<{ state: 'loading' | 'not_connected' | 'forbidden' | 'error' }>
  | Readonly<{
    state: 'connected';
    alerts: ReadonlyArray<Readonly<{ id: string; kind: string; firstSeenAt: string }>>;
    /** When ANUM first saw the newest alert that is not resolved (the dashboard's `latest_alert_at`). */
    latestAlertAt: string | null;
    /** The calendar day of the last inspection (`operations.last_inspection`). */
    lastInspection: string | null;
    /** Its stored time, when the source keeps one (`operations.last_inspection_at`); null for a date-only record. */
    lastInspectionAt: string | null;
  }>;

/** The inspection that is new in `next`: its instant, or its day when the source keeps dates only. Null when nothing changed. */
function newInspection(previous: Extract<TyreFeed, { state: 'connected' }>, next: Extract<TyreFeed, { state: 'connected' }>): string | null {
  const laterDay = next.lastInspection !== null && (previous.lastInspection === null || next.lastInspection > previous.lastInspection);
  if (next.lastInspectionAt) {
    // Two times: a later one is a new inspection. Only a day before: a later day is new; the same day may be the same inspection, so it is not.
    if (previous.lastInspectionAt) return isoMs(next.lastInspectionAt) > isoMs(previous.lastInspectionAt) ? next.lastInspectionAt : null;
    return laterDay ? next.lastInspectionAt : null;
  }
  return laterDay ? next.lastInspection : null;
}

/**
 * Tyre Pulse events: what changed between two connected answers, at most one alert and one inspection ("one animation per event type").
 * The first answer replays nothing. An alert is an event when the newest alert time moved forward; an inspection when a later one was recorded.
 * The record carries the stored time (an instant, or a day for a date-only source); one older than the 24 hour window is history, not an event.
 */
export function tyreRecords(previous: TyreFeed | null, next: TyreFeed, buildingKey: string | null, known: ReadonlySet<string>, nowEpochMs: number): WorkRecord[] {
  if (next.state !== 'connected' || !buildingKey || !previous || previous.state !== 'connected') return [];
  const out: WorkRecord[] = [];
  const alertAt = next.latestAlertAt;
  if (alertAt && (previous.latestAlertAt === null || isoMs(alertAt) > isoMs(previous.latestAlertAt))) {
    const match = next.alerts.find((a) => isoMs(a.firstSeenAt) === isoMs(alertAt)) ?? null;
    const record = makeRecord({ kind: 'tyre_alert', buildingKey, at: alertAt, recordId: match ? match.id : alertAt, alertKind: match?.kind }, known, nowEpochMs);
    if (record) out.push(record);
  }
  const inspection = newInspection(previous, next);
  if (inspection) {
    const record = makeRecord({ kind: 'tyre_inspection', buildingKey, at: inspection, recordId: inspection }, known, nowEpochMs);
    if (record) out.push(record);
  }
  return out;
}

// ---------------------------------------------------------------- planner

export type WorkMotion = Readonly<{
  id: string;
  record: WorkRecord;
  startedMs: number;
  /** Whether a vehicle or tyre is drawn (false under reduced motion, past the cap or without a route). The marker is shown either way. */
  animate: boolean;
  seconds: number;
}>;

/**
 * Turns records into motions. One per record id, ever (a record that was shown is not shown again while this page is open), one live
 * motion per building and kind at a time, and at most `MAX_ANIMATED` animated at once. Plain state, no timers: `prune` drops what is over.
 */
export class WorkPlanner {
  private readonly seen = new Set<string>();
  private list: WorkMotion[] = [];

  get motions(): readonly WorkMotion[] { return this.list; }

  accept(records: readonly WorkRecord[], nowMs: number, options: Readonly<{ reducedMotion: boolean; routed: (buildingKey: string, kind: WorkKind) => boolean }>): boolean {
    let changed = false;
    for (const record of records) {
      if (this.seen.has(record.recordId)) continue;
      this.seen.add(record.recordId);
      if (this.list.some((m) => m.record.buildingKey === record.buildingKey && m.record.kind === record.kind && this.alive(m, nowMs))) continue;
      const animatedNow = this.list.filter((m) => m.animate && this.animating(m, nowMs)).length;
      const animate = !options.reducedMotion && animatedNow < MAX_ANIMATED && options.routed(record.buildingKey, record.kind);
      this.list = [...this.list.filter((m) => !(m.record.buildingKey === record.buildingKey && m.record.kind === record.kind)), { id: record.recordId, record, startedMs: nowMs, animate, seconds: KIND_SECONDS[record.kind] }];
      changed = true;
    }
    return changed;
  }

  animating(m: WorkMotion, nowMs: number): boolean { return m.animate && nowMs - m.startedMs < m.seconds * 1000; }
  alive(m: WorkMotion, nowMs: number): boolean { return nowMs - m.startedMs < (m.animate ? m.seconds * 1000 + LINGER_MS : STATIC_MS); }

  /** Drops finished motions; true when something went. */
  prune(nowMs: number): boolean {
    const keep = this.list.filter((m) => this.alive(m, nowMs));
    if (keep.length === this.list.length) return false;
    this.list = keep;
    return true;
  }

  /** The next instant something ends (an animation or a marker), or null when idle. */
  nextEnd(nowMs: number): number | null {
    let next: number | null = null;
    for (const m of this.list) {
      const ends = [m.startedMs + m.seconds * 1000, m.startedMs + (m.animate ? m.seconds * 1000 + LINGER_MS : STATIC_MS)].filter((e) => e > nowMs);
      for (const e of ends) if (next === null || e < next) next = e;
    }
    return next;
  }

  reset(): void { this.seen.clear(); this.list = []; }
}

// ---------------------------------------------------------------- sites and routes

/**
 * A building's own rectangle when it is leaned (`Placement.yaw`, up to 45 degrees): its middle, its half sizes in world axes before the
 * lean (already grown like the box) and the lean. The axis aligned box round a leaned building is much bigger than the building, so its
 * door, and the yard stop in front of it, sit inside that box: the yard side and the obstacle test then use this rectangle instead.
 */
export type Lean2 = Readonly<{ cx: number; cz: number; hw: number; hd: number; yaw: number }>;
export type Box2 = Readonly<{ minX: number; minZ: number; maxX: number; maxZ: number; lean?: Lean2 }>;
export type SiteInput = Readonly<{ key: string; door: Readonly<{ x: number; z: number }>; box: Box2 }>;
/** Where things happen at one building: the yard stop for a vehicle, the tyre's start at the door and the rack. */
export type WorkSite = Readonly<{
  key: string;
  /** Unit vector from the building outwards through its door, and its left-hand sideways. */
  nx: number; nz: number; tx: number; tz: number;
  stop: Readonly<{ x: number; z: number }>;
  door: Readonly<{ x: number; z: number }>;
  rack: Readonly<{ x: number; z: number }>;
}>;

export function siteOf(input: SiteInput): WorkSite {
  const lean = input.box.lean;
  const cx = lean ? lean.cx : (input.box.minX + input.box.maxX) / 2, cz = lean ? lean.cz : (input.box.minZ + input.box.maxZ) / 2;
  // A leaned building is read in its own frame (the lean undone), so the side found is the building's real side, not a side of the box round it.
  const [dx, dz] = lean ? rotateXZ(input.door.x - cx, input.door.z - cz, -lean.yaw) : [input.door.x - cx, input.door.z - cz];
  const w = lean ? lean.hw * 2 : input.box.maxX - input.box.minX, d = lean ? lean.hd * 2 : input.box.maxZ - input.box.minZ;
  // Snap to the side of the building the door is on.
  const alongX = Math.abs(dx) / Math.max(1e-6, w) > Math.abs(dz) / Math.max(1e-6, d);
  const sx = alongX ? Math.sign(dx) || 1 : 0, sz = alongX ? 0 : Math.sign(dz) || 1;
  const [nx, nz] = lean ? rotateXZ(sx, sz, lean.yaw) : [sx, sz];
  const tx = -nz, tz = nx;
  const at = (n: number, t: number) => ({ x: input.door.x + nx * n + tx * t, z: input.door.z + nz * n + tz * t });
  return { key: input.key, nx, nz, tx, tz, stop: at(8.5, 6), door: at(1.4, 0), rack: at(6.5, -4.5) };
}

/** Does the segment (a to b) pass through a box grown by `pad`? A leaned building's box is tested by its own rectangle (`Lean2`). */
export function segmentHitsBox(ax: number, az: number, bx: number, bz: number, box: Box2, pad: number): boolean {
  const lean = box.lean;
  if (lean) {
    const [lax, laz] = rotateXZ(ax - lean.cx, az - lean.cz, -lean.yaw), [lbx, lbz] = rotateXZ(bx - lean.cx, bz - lean.cz, -lean.yaw);
    return segmentHitsBox(lax, laz, lbx, lbz, { minX: -lean.hw, minZ: -lean.hd, maxX: lean.hw, maxZ: lean.hd }, pad);
  }
  let t0 = 0, t1 = 1;
  const clip = (p: number, q: number): boolean => {
    if (Math.abs(p) < 1e-9) return q >= 0;
    const r = q / p;
    if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; } else { if (r < t0) return false; if (r < t1) t1 = r; }
    return true;
  };
  const dx = bx - ax, dz = bz - az;
  return clip(-dx, ax - (box.minX - pad)) && clip(dx, box.maxX + pad - ax) && clip(-dz, az - (box.minZ - pad)) && clip(dz, box.maxZ + pad - az);
}

export type WorkRoute = Readonly<{ ax: number; az: number; bx: number; bz: number; length: number; heading: number }>;

/** Points sampled along road lanes (x, z pairs) the route may start from. */
export type RoadPoints = Float32Array;

/** The nearest road point from which a straight run to the yard stop crosses no building; null when there is none (then only the marker shows). */
export function routeTo(site: WorkSite, roads: RoadPoints, boxes: readonly Box2[]): WorkRoute | null {
  const bx = site.stop.x, bz = site.stop.z;
  let best: WorkRoute | null = null;
  for (let i = 0; i + 1 < roads.length; i += 2) {
    const ax = roads[i], az = roads[i + 1];
    const length = Math.hypot(bx - ax, bz - az);
    if (length < 6 || (best && length >= best.length)) continue;
    if (boxes.some((box) => segmentHitsBox(ax, az, bx, bz, box, 1.6))) continue;
    best = { ax, az, bx, bz, length, heading: Math.atan2(bx - ax, bz - az) };
  }
  return best;
}

/** Samples a lane polyline (flat x, z pairs, closed) every `step` metres. */
export function sampleRoad(pts: Float32Array, step: number, out: number[]): void {
  const n = pts.length / 2;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const x0 = pts[i * 2], z0 = pts[i * 2 + 1], x1 = pts[j * 2], z1 = pts[j * 2 + 1];
    const len = Math.hypot(x1 - x0, z1 - z0), k = Math.max(1, Math.round(len / step));
    for (let s = 0; s < k; s++) out.push(x0 + ((x1 - x0) * s) / k, z0 + ((z1 - z0) * s) / k);
  }
}

// ---------------------------------------------------------------- poses

export type WorkPose = { visible: boolean; x: number; z: number; heading: number; /** Wheel rotation in radians, growing with the distance driven. */ spin: number; /** 0 to 1 pulse for the beacon, 0 when not parked. */ beacon: number; /** 0 to 1, grows from 0 when the thing appears. */ grow: number };
export const newPose = (): WorkPose => ({ visible: false, x: 0, z: 0, heading: 0, spin: 0, beacon: 0, grow: 1 });

const smooth = (u: number) => { const c = Math.min(1, Math.max(0, u)); return c * c * (3 - 2 * c); };
const easeOut = (u: number) => 1 - (1 - Math.min(1, Math.max(0, u))) ** 3;
const easeIn = (u: number) => Math.min(1, Math.max(0, u)) ** 3;

/** Timeline of a vehicle (seconds): drive in, wait with the beacon on, turn round, drive out. The sum is the motion's length. */
export const VEHICLE_TIMELINE = { approach: 6, hold: 6.5, turn: 1, depart: 4.5 } as const;
const HAULER_EXTRA = 2;

/** A vehicle on its route at `t` seconds since the start. Out of the timeline it is not visible. */
export function vehiclePoseAt(route: WorkRoute, t: number, wheelRadius: number, out: WorkPose, extraHold = 0): WorkPose {
  const { approach, hold, turn, depart } = VEHICLE_TIMELINE;
  const holdFor = hold + extraHold;
  const L = route.length;
  const end = approach + holdFor + turn + depart;
  out.visible = t >= 0 && t < end; out.grow = 1; out.beacon = 0; out.heading = route.heading;
  let d = 0, travelled = 0;
  if (t < approach) { d = L * easeOut(t / approach); travelled = d; }
  else if (t < approach + holdFor) {
    d = L; travelled = L;
    out.beacon = 0.5 + 0.5 * Math.sin((t - approach) * 6.2);
  } else if (t < approach + holdFor + turn) {
    d = L; travelled = L;
    out.heading = route.heading + Math.PI * smooth((t - approach - holdFor) / turn);
    out.beacon = 0.5 + 0.5 * Math.sin((t - approach) * 6.2) * (1 - smooth((t - approach - holdFor) / turn));
  } else {
    const u = (t - approach - holdFor - turn) / depart;
    d = L * (1 - easeIn(u)); travelled = L + (L - d);
    out.heading = route.heading + Math.PI;
  }
  const f = L > 0 ? d / L : 0;
  out.x = route.ax + (route.bx - route.ax) * f; out.z = route.az + (route.bz - route.az) * f;
  out.spin = travelled / Math.max(0.1, wheelRadius);
  return out;
}

export const vehicleSeconds = (kind: WorkKind): number => VEHICLE_TIMELINE.approach + VEHICLE_TIMELINE.hold + (kind === 'tyre_alert' ? HAULER_EXTRA : 0) + VEHICLE_TIMELINE.turn + VEHICLE_TIMELINE.depart;
export const extraHoldOf = (kind: WorkKind): number => (kind === 'tyre_alert' ? HAULER_EXTRA : 0);

/** A tyre rolling from the door to the rack, resting there, rolling back: 4 s, 5 s, 4 s. */
export const TYRE_TIMELINE = { out: 4, rest: 5, back: 4 } as const;
export const TYRE_RADIUS = 0.55;

export function tyrePoseAt(site: WorkSite, t: number, out: WorkPose): WorkPose {
  const { out: tOut, rest, back } = TYRE_TIMELINE;
  const a = site.door, b = site.rack;
  const L = Math.hypot(b.x - a.x, b.z - a.z);
  out.visible = t >= 0 && t < tOut + rest + back; out.beacon = 0; out.heading = Math.atan2(b.x - a.x, b.z - a.z);
  let f = 0, travelled = 0;
  if (t < tOut) { f = smooth(t / tOut); travelled = f * L; }
  else if (t < tOut + rest) { f = 1; travelled = L; }
  else { const u = (t - tOut - rest) / back; f = 1 - smooth(u); travelled = L + (1 - f) * L; out.heading += Math.PI; }
  out.x = a.x + (b.x - a.x) * f; out.z = a.z + (b.z - a.z) * f;
  out.spin = travelled / TYRE_RADIUS;
  out.grow = smooth(t / 0.6) * (1 - smooth((t - (tOut + rest + back - 0.6)) / 0.6));
  return out;
}

// ---------------------------------------------------------------- the card line

/** What a building card shows: the Because line of its live motion, or why a feed is off. Strings only come from `becauseLine` and fixed messages. */
export type CardWork = Readonly<{ kind: WorkKind; at: string; because: string }>;
export type FeedNote = Readonly<{ reason: 'not_connected' | 'forbidden' | 'error' | 'elsewhere' }>;
