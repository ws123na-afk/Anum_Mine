/**
 * Seats at shared tables (docs/ANUM_MINE_GREEN_CONCRETE.md). Pure geometry, no state.
 *
 * A seat is a workstation 1.5 m long and 0.78 m deep. Facing north the person sits on the south side (looking north); facing
 * east the person sits on the west side. Seats of one table group are drawn as one table: where a long leg of seats facing
 * north or south meets a short leg of seats facing east or west at its end (an L), a corner piece joins the two so the table
 * is one piece. The corner is furniture, never a desk and never a person.
 */
import type { MeshBuilder, Rgb } from './hqGeometry.ts';
import { scale } from './hqGeometry.ts';

export type SeatFacing = 'north' | 'south' | 'east' | 'west';
export type SeatPoint = Readonly<{ x: number; z: number; facing: SeatFacing | null; group: string | null }>;
export type TableJoin = Readonly<{ x: number; z: number; width: number; depth: number }>;

const LONG = 1.5;
const DEEP = 0.78;

/** Quarter turns of a station: 0 looks north, 1 east, 2 south, 3 west. */
export function rotOfFacing(facing: SeatFacing | null | undefined): number {
  return facing === 'east' ? 1 : facing === 'south' ? 2 : facing === 'west' ? 3 : 0;
}

const across = (f: SeatFacing | null): boolean => f === 'north' || f === 'south' || f == null;

/** The corner pieces of the L tables among these seats (one per group at most). */
export function tableJoins(seats: readonly SeatPoint[]): TableJoin[] {
  const groups = new Map<string, SeatPoint[]>();
  for (const s of seats) if (s.group) groups.set(s.group, [...(groups.get(s.group) ?? []), s]);
  const out: TableJoin[] = [];
  for (const list of groups.values()) {
    const long = list.filter((s) => across(s.facing));
    const short = list.filter((s) => !across(s.facing));
    if (long.length === 0 || short.length === 0) continue;
    const a = long.reduce((m, s) => (s.x > m.x ? s : m));
    const b = short.reduce((m, s) => (s.z < m.z ? s : m));
    const barEnd = a.x + LONG / 2;
    if (Math.abs(b.x - DEEP / 2 - barEnd) > 0.2 || b.z - LONG / 2 <= a.z) continue;
    const top = a.z - DEEP / 2;
    out.push({ x: b.x - DEEP / 2, z: top, width: DEEP, depth: b.z - LONG / 2 - top });
  }
  return out;
}

/** Draws the corner pieces at the height of the workstation tops. */
export function drawJoins(b: MeshBuilder, y: number, joins: readonly TableJoin[], wood: Rgb, dark: Rgb): void {
  const H = 0.74;
  for (const j of joins) {
    b.box(j.x, y + H - 0.04, j.z, j.width, 0.04, j.depth, wood, { grad: 0.08 });
    b.box(j.x, y + H - 0.045, j.z, j.width, 0.012, j.depth, scale(wood, 0.7));
    b.box(j.x + j.width - 0.065, y, j.z + 0.05, 0.035, H - 0.05, Math.max(0.1, j.depth - 0.1), dark);
  }
}
