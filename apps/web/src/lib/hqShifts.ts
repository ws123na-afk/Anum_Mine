/**
 * Working hours of an office (docs/ANUM_MINE_GREEN_CONCRETE.md). Pure: no DOM, no network.
 *
 * Each seat may carry the working hours of the person at it (`workStart`, `workEnd`, "HH:MM" in the building's own time zone;
 * an end before the start runs over midnight). A person is on shift only inside their own hours; outside them they are
 * "Off shift" (never "absent"): no figure sits at the desk, the screen stays dark and any work assigned to them is reported as
 * waiting, not as being done. The hours are the owner's, so they are facts, not defaults.
 *
 * Older seats (before each person had hours) belong to a day or night shift and read the building's day and night starts;
 * when the building never stored any, those are defaults (07:00 and 19:00) and every screen that shows them says so. A desk
 * with neither (an ordinary desk, a manager seat without hours) is always on.
 */
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from './headquartersProjection.ts';

export type Shift = 'day' | 'night';
export const DEFAULT_DAY_START = '07:00';
export const DEFAULT_NIGHT_START = '19:00';
export const HOUR_PATTERN = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

/** The day and night starts of a building, read by older Day or Night seats only. */
export type ShiftHours = Readonly<{ dayStart: string; nightStart: string; isDefault: boolean }>;
/** The clock of a building with seats that have hours: its zone, the local time of day and the building's day and night hours. */
export type ShiftSnapshot = Readonly<{ timezone: string; minutes: number; hours: ShiftHours }>;
export type DeskShiftInfo = Readonly<{
  /** The older day or night shift of the seat; null for a seat with its own hours or none. */
  shift: Shift | null; manager: boolean;
  /** True when the person is inside their hours now (a seat with no hours is always on). */
  onShift: boolean;
  /** The hours in force for this seat, or null for a seat with none. */
  start: string | null; end: string | null;
  /** True when the hours are the seat's own; false when they come from the building's day and night hours. */
  own: boolean;
  /** True when an older Day or Night seat reads the building's default hours, which nobody saved. */
  hoursAreDefault: boolean;
}>;

export function hoursOf(dayStart: string | null | undefined, nightStart: string | null | undefined): ShiftHours {
  const d = dayStart && HOUR_PATTERN.test(dayStart) ? dayStart : null;
  const n = nightStart && HOUR_PATTERN.test(nightStart) ? nightStart : null;
  return { dayStart: d ?? DEFAULT_DAY_START, nightStart: n ?? DEFAULT_NIGHT_START, isDefault: d === null && n === null };
}

const minutesOf = (hhmm: string): number => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

/** Minutes after local midnight at `ms` in the zone (UTC when the zone is unknown). */
export function minutesInZone(ms: number, timezone: string): number {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms));
    const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0);
    const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
    return (h % 24) * 60 + m;
  } catch {
    const d = new Date(ms);
    return d.getUTCHours() * 60 + d.getUTCMinutes();
  }
}

/** "HH:MM" of a local time of day. */
export const clockOf = (minutes: number): string => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

/** True when a local time of day is inside the hours. An end before the start runs over midnight (22:00 to 06:00). */
export function inWindow(minutes: number, start: string, end: string): boolean {
  const s = minutesOf(start), e = minutesOf(end);
  return s < e ? minutes >= s && minutes < e : minutes >= s || minutes < e;
}

/** The day or night shift in force at a local time of day, for older Day or Night seats. The night wraps over midnight. */
export function shiftAtMinutes(minutes: number, hours: Pick<ShiftHours, 'dayStart' | 'nightStart'>): Shift {
  const d = minutesOf(hours.dayStart), n = minutesOf(hours.nightStart);
  if (d <= n) return minutes >= d && minutes < n ? 'day' : 'night';
  return minutes >= n && minutes < d ? 'night' : 'day';
}

export const shiftAt = (ms: number, timezone: string, hours: ShiftHours): Shift => shiftAtMinutes(minutesInZone(ms, timezone), hours);

/** Start and end ("HH:MM") of an older day or night shift. */
export function windowOf(shift: Shift, hours: Pick<ShiftHours, 'dayStart' | 'nightStart'>): Readonly<{ start: string; end: string }> {
  return shift === 'day' ? { start: hours.dayStart, end: hours.nightStart } : { start: hours.nightStart, end: hours.dayStart };
}

const ownHours = (d: Pick<SceneDesk, 'workStart' | 'workEnd'>): boolean =>
  Boolean(d.workStart && d.workEnd && HOUR_PATTERN.test(d.workStart) && HOUR_PATTERN.test(d.workEnd));
/** True when the desk has working hours: its own, or an older day or night shift. */
export const deskHasHours = (d: Pick<SceneDesk, 'shift' | 'workStart' | 'workEnd'>): boolean => d.shift != null || ownHours(d);
export const hasShiftDesks = (building: SceneBuilding): boolean => building.floors.some((f) => f.desks.some(deskHasHours));
export const roomHasShiftDesks = (desks: readonly SceneDesk[]): boolean => desks.some(deskHasHours);

export function snapshotOf(building: SceneBuilding, nowMs: number): ShiftSnapshot {
  return { timezone: building.timezone, minutes: minutesInZone(nowMs, building.timezone), hours: hoursOf(building.shiftDayStart, building.shiftNightStart) };
}

/** What a desk says about its hours at the snapshot's time. */
export function deskShiftInfo(d: Pick<SceneDesk, 'shift' | 'workStart' | 'workEnd' | 'seatKind'>, snap: ShiftSnapshot): DeskShiftInfo {
  const manager = d.seatKind === 'manager';
  if (ownHours(d)) {
    const start = d.workStart as string, end = d.workEnd as string;
    return { shift: null, manager, onShift: inWindow(snap.minutes, start, end), start, end, own: true, hoursAreDefault: false };
  }
  if (d.shift != null) {
    const w = windowOf(d.shift, snap.hours);
    return { shift: d.shift, manager, onShift: inWindow(snap.minutes, w.start, w.end), start: w.start, end: w.end, own: false, hoursAreDefault: snap.hours.isDefault };
  }
  return { shift: null, manager, onShift: true, start: null, end: null, own: false, hoursAreDefault: false };
}

const seatKey = (d: Pick<SceneDesk, 'roomId' | 'position'>): string => `${d.roomId}:${d.position.x.toFixed(2)}:${d.position.z.toFixed(2)}`;

function viewFloor(floor: SceneFloor, snap: ShiftSnapshot): SceneFloor {
  if (!floor.desks.some(deskHasHours)) return floor;
  const withInfo = floor.desks.map((d) => (deskHasHours(d) || d.seatKind === 'manager' ? { ...d, shiftInfo: deskShiftInfo(d, snap) } : d));
  // Older builds put two people (a day and a night seat) at one place. The room never draws two people at one place: the
  // person on shift now keeps it; when nobody there is on shift, the first desk keeps it as an off shift seat.
  const keep = new Map<string, SceneDesk>();
  for (const d of withInfo) {
    const key = seatKey(d), held = keep.get(key);
    if (!held || (held.shiftInfo && !held.shiftInfo.onShift && (!d.shiftInfo || d.shiftInfo.onShift))) keep.set(key, d);
  }
  const shown = new Set([...keep.values()].map((d) => d.id));
  const desks = withInfo.filter((d) => shown.has(d.id));
  if (desks.length === floor.desks.length) return { ...floor, desks };
  const rooms: RoomGeometry[] = floor.rooms.map((r) => ({ ...r, deskIds: r.deskIds.filter((id) => shown.has(id)) }));
  return { ...floor, desks, rooms };
}

/** The buildings as they are now: each seat with hours says whether its person is on shift. A building without such seats is returned unchanged. */
export function applyShifts(buildings: readonly SceneBuilding[], nowMs: number): readonly SceneBuilding[] {
  let changed = false;
  const out = buildings.map((b) => {
    if (!hasShiftDesks(b)) return b;
    changed = true;
    const snap = snapshotOf(b, nowMs);
    return { ...b, shifts: snap, floors: b.floors.map((f) => viewFloor(f, snap)) };
  });
  return changed ? out : buildings;
}

/** What a desk says about its hours: nothing for a desk with none. `waiting` is true when work is assigned to a person who is off shift. */
export function deskShiftLine(info: DeskShiftInfo | undefined, workWaiting: boolean): 'on' | 'manager' | 'off' | 'off_waiting' | null {
  if (!info || (info.start == null && !info.manager)) return null;
  if (info.manager && info.start == null) return 'manager';
  if (info.onShift) return 'on';
  return workWaiting ? 'off_waiting' : 'off';
}

/** Statuses that mean work is assigned to the agent (a record backs each one). */
export const WORK_STATUSES: ReadonlySet<string> = new Set(['working', 'queued', 'waiting_approval', 'blocked', 'meeting']);

/** A key that changes only when somebody's on shift state changes (so the scene is not rebuilt on every clock tick). */
export function activeShiftKey(buildings: readonly SceneBuilding[], nowMs: number): string {
  return buildings.filter(hasShiftDesks).map((b) => {
    const snap = snapshotOf(b, nowMs);
    return `${b.id}:${b.floors.map((f) => f.desks.filter(deskHasHours).map((d) => (deskShiftInfo(d, snap).onShift ? '1' : '0')).join('')).join('.')}`;
  }).join('|');
}

/**
 * Who is off shift now, for the counts (campus, building, Total agents, Operations wall): the agent of every seat whose person is
 * outside their hours, as agent ids and `desk:<id>`. Read from the stored seats and hours.
 */
export function offShiftKeys(buildings: readonly SceneBuilding[], nowMs: number): ReadonlySet<string> {
  const out = new Set<string>();
  for (const b of buildings) {
    if (!hasShiftDesks(b)) continue;
    const snap = snapshotOf(b, nowMs);
    for (const f of b.floors) for (const d of f.desks) if (deskHasHours(d) && !deskShiftInfo(d, snap).onShift) { out.add(`desk:${d.id}`); if (d.agentId) out.add(d.agentId); }
  }
  return out;
}

/** The hours of the people seated in a room, grouped by window, with how many are in each and whether it is on now. */
export function roomWindows(desks: readonly SceneDesk[]): ReadonlyArray<Readonly<{ start: string; end: string; people: number; on: boolean; own: boolean }>> {
  const groups = new Map<string, { start: string; end: string; people: number; on: boolean; own: boolean }>();
  for (const d of desks) {
    const i = d.shiftInfo;
    if (!i || i.start == null || i.end == null || !d.agentId) continue;
    const key = `${i.start}-${i.end}`;
    const g = groups.get(key) ?? { start: i.start, end: i.end, people: 0, on: i.onShift, own: i.own };
    g.people += 1;
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end));
}
