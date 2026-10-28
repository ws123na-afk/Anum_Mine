import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './lib/api.ts';
import type { ReceptionRoom } from './lib/hqReceptionCount.ts';

const EVERY_MS = 60_000;
type CountLib = typeof import('./lib/hqReceptionCount.ts');

/**
 * How many visitors are checked in now at each reception room (`GET /api/v1/office/visitors/summary`, counts only), for the 3D
 * reception rooms. Read only while the open office has a reception room, again every minute while the tab is visible and after
 * every change in the Reception panel (`reload`). Any failure (no permission, no PostgreSQL, offline) gives null: the rooms then
 * show no visitor line at all, never a guessed number. The count rules (`lib/hqReceptionCount.ts`) are a small lazy chunk, so
 * the headquarters entry chunk carries only this hook.
 */
export function useVisitorSummary(rooms: readonly ReceptionRoom[], workspaceId: string): { counts: ReadonlyMap<string, number> | null; reload: () => void } {
  // The reception rooms and their buildings: a change (a room added, moved or removed) reads the counts again.
  const key = rooms.filter((r) => r.kind === 'reception').map((r) => `${r.id}@${r.buildingId}`).join(',');
  const enabled = key !== '';
  const [counts, setCounts] = useState<ReadonlyMap<string, number> | null>(null);
  const generation = useRef(0);
  const latest = useRef(rooms);
  latest.current = rooms;
  const read = useCallback(async () => {
    const id = ++generation.current;
    try {
      const [lib, raw]: [CountLib, unknown] = await Promise.all([import('./lib/hqReceptionCount.ts'), api.apiRequest<unknown>('/api/v1/office/visitors/summary', { method: 'GET' })]);
      const summary = lib.parseSummary(raw);
      if (id === generation.current) setCounts(summary ? lib.receptionCounts(summary, latest.current) : null);
    } catch {
      if (id === generation.current) setCounts(null);
    }
  }, []);
  useEffect(() => {
    setCounts(null);
    if (!enabled) { generation.current += 1; return undefined; }
    void read();
    const tick = () => { if (document.visibilityState === 'visible') void read(); };
    const timer = window.setInterval(tick, EVERY_MS);
    document.addEventListener('visibilitychange', tick);
    return () => { generation.current += 1; window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, [enabled, key, workspaceId, read]);
  const reload = useCallback(() => { if (enabled) void read(); }, [enabled, read]);
  return { counts, reload };
}
