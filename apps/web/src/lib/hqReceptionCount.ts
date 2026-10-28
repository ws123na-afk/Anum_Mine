/**
 * The counts behind the 3D reception rooms (docs/ANUM_MINE_HQ_RECEPTION.md): kept apart from the panel's rules in
 * `hqReception.ts` so the headquarters chunk carries only these few lines; the panel itself is a lazy chunk.
 */
import type { VisitSummary } from '@anum/contracts';

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0);

export function parseSummary(raw: unknown): VisitSummary | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.checked_in !== 'number') return null;
  const rows = <K extends string>(v: unknown, key: K) => (Array.isArray(v) ? v : [])
    .map((x) => (x && typeof x === 'object' ? x as Record<string, unknown> : {}))
    .filter((x) => str(x[key]) && num(x.count) > 0)
    .map((x) => ({ [key]: x[key] as string, count: num(x.count) }) as Record<K, string> & { count: number });
  return { version: 1, checked_in: num(r.checked_in), by_room: rows(r.by_room, 'room_id'), by_building: rows(r.by_building, 'building_id'), unplaced: num(r.unplaced), as_of: str(r.as_of) ?? '' };
}

export type ReceptionRoom = Readonly<{ id: string; buildingId: string; kind: string }>;

/**
 * How many checked-in visitors each reception room shows. A visit counts at its own room. A visit with a building and
 * no room counts at that building's reception room only when the building has exactly one; a visit with neither counts
 * only when the workspace has exactly one reception room. Anything else stays in the panel and is never spread or guessed.
 * A null summary (no answer) gives an empty map, so no room shows a visitor line.
 */
export function receptionCounts(summary: VisitSummary | null, rooms: readonly ReceptionRoom[]): ReadonlyMap<string, number> {
  const out = new Map<string, number>();
  if (!summary) return out;
  const reception = rooms.filter((r) => r.kind === 'reception');
  for (const r of reception) out.set(r.id, 0);
  for (const row of summary.by_room) if (out.has(row.room_id)) out.set(row.room_id, (out.get(row.room_id) ?? 0) + row.count);
  for (const row of summary.by_building) {
    const inBuilding = reception.filter((r) => r.buildingId === row.building_id);
    if (inBuilding.length === 1) out.set(inBuilding[0].id, (out.get(inBuilding[0].id) ?? 0) + row.count);
  }
  if (summary.unplaced > 0 && reception.length === 1) out.set(reception[0].id, (out.get(reception[0].id) ?? 0) + summary.unplaced);
  return out;
}
