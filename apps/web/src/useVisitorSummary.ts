import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './lib/api.ts';
import type { ReceptionRoom } from './lib/hqReceptionCount.ts';

const EVERY_MS = 60_000;
/** The event the API records after every change to the visitor register (ids only). */
export const VISITOR_EVENT = 'office.visitors_changed';
type CountLib = typeof import('./lib/hqReceptionCount.ts');

/**
 * How many visitors are checked in now at each reception room (`GET /api/v1/office/visitors/summary`, counts only), for the 3D
 * reception rooms. Read only while the open office has a reception room, again every minute while the tab is visible, after
 * every change in the Reception panel (`reload`) and at once when the workspace event stream delivers `office.visitors_changed`
 * (a check in or out in another tab or browser). Any failure (no permission, no PostgreSQL, offline) gives null: the rooms then
 * show no visitor line at all, never a guessed number. The count rules (`lib/hqReceptionCount.ts`) are a small lazy chunk, so
 * the headquarters entry chunk carries only this hook.
 */
export function useVisitorSummary(rooms: readonly ReceptionRoom[], workspaceId: string, live?: Readonly<{ tick: number; type: string | null }>): { counts: ReadonlyMap<string, number> | null; reload: () => void } {
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
  // The shell's workspace event stream: a visit checked in, out or changed anywhere (another tab, another browser, the
  // reception desk) is `office.visitors_changed`, and the count is read again at once instead of at the next minute.
  const seen = useRef(live?.tick ?? 0);
  const tick = live?.tick ?? 0, type = live?.type ?? null;
  useEffect(() => {
    if (tick === seen.current) return;
    seen.current = tick;
    if (enabled && type === VISITOR_EVENT) void read();
  }, [tick, type, enabled, read]);
  const reload = useCallback(() => { if (enabled) void read(); }, [enabled, read]);
  return { counts, reload };
}
