/**
 * Shifts of an office (docs/ANUM_MINE_GREEN_CONCRETE.md). Pure: no DOM, no network.
 *
 * A desk may belong to the day or the night shift (null means always on, the manager seat). The shift in force is read
 * from the building's own time zone and its shift hours. Hours that were never stored are DEFAULTS (07:00 and 19:00)
 * the owner has not confirmed, and every screen that shows them says so. The room shows the people of the shift in
 * force; a toggle shows the other shift, whose people are then "Off shift" (never "absent"): their screens stay dark
 * and any work assigned to them is reported as waiting, not as being done.
 */
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from './headquartersProjection.ts';

export type Shift = 'day' | 'night';
export type ShiftView = 'active' | 'other';
export const DEFAULT_DAY_START = '07:00';
export const DEFAULT_NIGHT_START = '19:00';
export const HOUR_PATTERN = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

export type ShiftHours = Readonly<{ dayStart: string; nightStart: string; isDefault: boolean }>;
export type ShiftSnapshot = Readonly<{ active: Shift; viewed: Shift; hours: ShiftHours; timezone: string }>;
export type DeskShiftInfo = Readonly<{
  shift: Shift | null; manager: boolean; active: Shift; viewed: Shift;
  /** True when the person is on shift now (the manager seat is always on). */
  onShift: boolean; start: string | null; end: string | null; hoursAreDefault: boolean;
}>;

export const otherShift = (shift: Shift): Shift => (shift === 'day' ? 'night' : 'day');

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

/** The shift in force at a local time of day. The night shift wraps over midnight. */
export function shiftAtMinutes(minutes: number, hours: Pick<ShiftHours, 'dayStart' | 'nightStart'>): Shift {
  const d = minutesOf(hours.dayStart), n = minutesOf(hours.nightStart);
  if (d <= n) return minutes >= d && minutes < n ? 'day' : 'night';
  return minutes >= n && minutes < d ? 'night' : 'day';
}

export const shiftAt = (ms: number, timezone: string, hours: ShiftHours): Shift => shiftAtMinutes(minutesInZone(ms, timezone), hours);

/** Start and end ("HH:MM") of a shift. */
export function windowOf(shift: Shift, hours: Pick<ShiftHours, 'dayStart' | 'nightStart'>): Readonly<{ start: string; end: string }> {
  return shift === 'day' ? { start: hours.dayStart, end: hours.nightStart } : { start: hours.nightStart, end: hours.dayStart };
}

export const hasShiftDesks = (building: SceneBuilding): boolean => building.floors.some((f) => f.desks.some((d) => d.shift != null));
export const roomHasShiftDesks = (desks: readonly SceneDesk[]): boolean => desks.some((d) => d.shift != null);

export function snapshotOf(building: SceneBuilding, view: ShiftView, nowMs: number): ShiftSnapshot {
  const hours = hoursOf(building.shiftDayStart, building.shiftNightStart);
  const active = shiftAt(nowMs, building.timezone, hours);
  return { active, viewed: view === 'other' ? otherShift(active) : active, hours, timezone: building.timezone };
}

const seatKey = (d: Pick<SceneDesk, 'roomId' | 'position'>): string => `${d.roomId}:${d.position.x.toFixed(2)}:${d.position.z.toFixed(2)}`;

function viewFloor(floor: SceneFloor, snap: ShiftSnapshot): SceneFloor {
  if (!floor.desks.some((d) => d.shift != null)) return floor;
  const shown = floor.desks.filter((d) => d.shift == null || d.shift === snap.viewed);
  const taken = new Set(shown.map(seatKey));
  // A table of the other shift whose place nobody uses in this shift stays drawn as an empty seat: the table is furniture.
  const empties = new Map<string, Array<{ x: number; z: number; facing: 'north' | 'south' | 'east' | 'west' | null; group: string | null }>>();
  for (const d of floor.desks) {
    if (d.shift == null || d.shift === snap.viewed || taken.has(seatKey(d))) continue;
    taken.add(seatKey(d));
    const list = empties.get(d.roomId) ?? [];
    list.push({ x: d.position.x, z: d.position.z, facing: d.facing ?? null, group: d.tableGroup ?? null });
    empties.set(d.roomId, list);
  }
  const desks: SceneDesk[] = shown.map((d) => {
    const onShift = d.shift == null || d.shift === snap.active;
    const info: DeskShiftInfo = {
      shift: d.shift ?? null, manager: d.seatKind === 'manager', active: snap.active, viewed: snap.viewed, onShift,
      start: d.shift == null ? null : windowOf(d.shift, snap.hours).start, end: d.shift == null ? null : windowOf(d.shift, snap.hours).end,
      hoursAreDefault: snap.hours.isDefault,
    };
    return { ...d, shiftInfo: info };
  });
  const rooms: RoomGeometry[] = floor.rooms.map((r) => ({
    ...r, deskIds: r.deskIds.filter((id) => desks.some((d) => d.id === id)), emptySeats: empties.get(r.id) ?? [],
  }));
  return { ...floor, desks, rooms };
}

/** The building as the chosen shift sees it. A building without shift desks is returned unchanged. */
export function applyShiftView(buildings: readonly SceneBuilding[], view: ShiftView, nowMs: number): readonly SceneBuilding[] {
  let changed = false;
  const out = buildings.map((b) => {
    if (!hasShiftDesks(b)) return b;
    changed = true;
    const snap = snapshotOf(b, view, nowMs);
    return { ...b, shifts: snap, floors: b.floors.map((f) => viewFloor(f, snap)) };
  });
  return changed ? out : buildings;
}

/** What a desk says about its shift: nothing for a desk with no shift. `waiting` is true when work is assigned to a person who is off shift. */
export function deskShiftLine(info: DeskShiftInfo | undefined, workWaiting: boolean): 'on' | 'manager' | 'off' | 'off_waiting' | null {
  if (!info || (info.shift == null && !info.manager)) return null;
  if (info.manager && info.shift == null) return 'manager';
  if (info.onShift) return 'on';
  return workWaiting ? 'off_waiting' : 'off';
}

/** Statuses that mean work is assigned to the agent (a record backs each one). */
export const WORK_STATUSES: ReadonlySet<string> = new Set(['working', 'queued', 'waiting_approval', 'blocked', 'meeting']);

/** A key that changes only when the shift in force changes in some building with shift desks (so the scene is not rebuilt on every clock tick). */
export function activeShiftKey(buildings: readonly SceneBuilding[], nowMs: number): string {
  return buildings.filter(hasShiftDesks).map((b) => `${b.id}:${snapshotOf(b, 'active', nowMs).active}`).join('|');
}
