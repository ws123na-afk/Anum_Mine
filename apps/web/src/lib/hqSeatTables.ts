/**
 * Seats at shared tables (docs/ANUM_MINE_GREEN_CONCRETE.md). Pure geometry, no state.
 *
 * A seat is a workstation 1.5 m long and 0.78 m deep. Facing north the person sits on the south side (looking north); facing
 * east the person sits on the west side. Seats of one table group are drawn as one table: where a long leg of seats facing
 * north or south meets a short leg of seats facing east or west at either end (a right or a left L), a corner piece joins the
 * two so the table is one piece. The corner is furniture, never a desk and never a person.
 */
import type { MeshBuilder, Rgb } from './hqGeometry.ts';
import { scale } from './hqGeometry.ts';

export type SeatFacing = 'north' | 'south' | 'east' | 'west';
export type SeatPoint = Readonly<{ x: number; z: number; facing: SeatFacing | null; group: string | null }>;
/** A corner piece in room metres; `outer` is the side away from the row (1 east for a right L, -1 west for a left L). */
export type TableJoin = Readonly<{ x: number; z: number; width: number; depth: number; outer: 1 | -1 }>;

const LONG = 1.5;
const DEEP = 0.78;

/** Quarter turns of a station: 0 looks north, 1 east, 2 south, 3 west. */
export function rotOfFacing(facing: SeatFacing | null | undefined): number {
  return facing === 'east' ? 1 : facing === 'south' ? 2 : facing === 'west' ? 3 : 0;
}

const across = (f: SeatFacing | null): boolean => f === 'north' || f === 'south' || f == null;

/**
 * The corner pieces of the L tables among these seats. The L is read from the stored seats: a row of seats facing north or south,
 * and a short leg of seats facing east or west whose tables touch the row's right end (a right L) or its left end (a left L),
 * running towards the door or towards the wall. The piece fills the corner square and the gap down to the first seat of the leg;
 * a corner seat (a leg seat whose table already covers the corner) needs no piece.
 */
export function tableJoins(seats: readonly SeatPoint[]): TableJoin[] {
  const groups = new Map<string, SeatPoint[]>();
  for (const s of seats) if (s.group) groups.set(s.group, [...(groups.get(s.group) ?? []), s]);
  const out: TableJoin[] = [];
  for (const list of groups.values()) {
    const long = list.filter((s) => across(s.facing));
    const short = list.filter((s) => !across(s.facing));
    if (long.length === 0 || short.length === 0) continue;
    for (const right of [true, false]) {
      const a = long.reduce((m, s) => ((right ? s.x > m.x : s.x < m.x) ? s : m));
      const end = a.x + (right ? LONG / 2 : -LONG / 2);
      const leg = short.filter((s) => Math.abs(s.x + (right ? -DEEP / 2 : DEEP / 2) - end) <= 0.2);
      const south = leg.filter((s) => s.z > a.z), north = leg.filter((s) => s.z < a.z);
      const top = a.z - DEEP / 2, bottom = a.z + DEEP / 2;
      if (south.length) {
        const b = south.reduce((m, s) => (s.z < m.z ? s : m));
        if (b.z - LONG / 2 > top + 0.01) out.push({ x: b.x - DEEP / 2, z: top, width: DEEP, depth: b.z - LONG / 2 - top, outer: right ? 1 : -1 });
      }
      if (north.length) {
        const b = north.reduce((m, s) => (s.z > m.z ? s : m));
        if (bottom > b.z + LONG / 2 + 0.01) out.push({ x: b.x - DEEP / 2, z: b.z + LONG / 2, width: DEEP, depth: bottom - b.z - LONG / 2, outer: right ? 1 : -1 });
      }
    }
  }
  return out;
}

/** Draws the corner pieces at the height of the workstation tops. */
export function drawJoins(b: MeshBuilder, y: number, joins: readonly TableJoin[], wood: Rgb, dark: Rgb): void {
  const H = 0.74;
  for (const j of joins) {
    b.box(j.x, y + H - 0.04, j.z, j.width, 0.04, j.depth, wood, { grad: 0.08 });
    b.box(j.x, y + H - 0.045, j.z, j.width, 0.012, j.depth, scale(wood, 0.7));
    b.box(j.outer < 0 ? j.x + 0.03 : j.x + j.width - 0.065, y, j.z + 0.05, 0.035, H - 0.05, Math.max(0.1, j.depth - 0.1), dark);
  }
}
