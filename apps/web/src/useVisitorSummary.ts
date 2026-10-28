import { useCallback, useEffect, useRef, useState } from 'react';
import type { VisitSummary } from '@anum/contracts';
import * as api from './lib/api.ts';
import { parseSummary } from './lib/hqReceptionCount.ts';

const EVERY_MS = 60_000;

/**
 * How many visitors are checked in now (`GET /api/v1/office/visitors/summary`, counts only), for the 3D reception rooms.
 * Read only while the open office has a reception room, again every minute while the tab is visible and after every
 * change in the Reception panel (`reload`). Any failure (no permission, no PostgreSQL, offline) gives null: the rooms
 * then show no visitor line at all, never a guessed number.
 */
export function useVisitorSummary(enabled: boolean, workspaceId: string): { summary: VisitSummary | null; reload: () => void } {
  const [summary, setSummary] = useState<VisitSummary | null>(null);
  const generation = useRef(0);
  const read = useCallback(async () => {
    const id = ++generation.current;
    try {
      const next = parseSummary(await api.apiRequest<unknown>('/api/v1/office/visitors/summary', { method: 'GET' }));
      if (id === generation.current) setSummary(next);
    } catch {
      if (id === generation.current) setSummary(null);
    }
  }, []);
  useEffect(() => {
    setSummary(null);
    if (!enabled) { generation.current += 1; return undefined; }
    void read();
    const tick = () => { if (document.visibilityState === 'visible') void read(); };
    const timer = window.setInterval(tick, EVERY_MS);
    document.addEventListener('visibilitychange', tick);
    return () => { generation.current += 1; window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, [enabled, workspaceId, read]);
  const reload = useCallback(() => { if (enabled) void read(); }, [enabled, read]);
  return { summary, reload };
}
