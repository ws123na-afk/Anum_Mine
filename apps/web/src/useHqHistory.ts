import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { EMPTY_STATE, historyQuery, loadMore, type HistoryFilters, type HistoryScope, type HistoryState } from './lib/hqHistory';
import { createHistoryClient } from './lib/hqHistoryApi';

export const historyClient = createHistoryClient(api.apiRequest, api.currentScope);
export type HistoryStatus = 'loading' | 'ready' | 'error' | 'denied' | 'invalid';

/**
 * The office history of the open workspace for one filter set and scope. The first read starts when the
 * inputs change; `loadOlder` continues from the saved cursor. An answer that arrives after the inputs
 * changed again is dropped, so a slow request never overwrites a newer one.
 */
export function useHqHistory(filters: HistoryFilters, scope: HistoryScope, enabled = true) {
  const [state, setState] = useState<HistoryState>(EMPTY_STATE);
  const [status, setStatus] = useState<HistoryStatus>('loading');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const key = useMemo(() => JSON.stringify([filters, scope]), [filters, scope]);
  const latest = useRef({ filters, scope, state });
  latest.current = { filters, scope, state };

  const run = useCallback(async (first: boolean) => {
    const { filters: f, scope: s, state: current } = latest.current;
    const id = first ? ++generation.current : generation.current;
    if (first) { setStatus('loading'); setError(''); setState(EMPTY_STATE); }
    else setBusy(true);
    try {
      const next = await loadMore(async (cursor) => {
        const query = historyQuery(f, s, cursor);
        if (!query.ok) throw Object.assign(new Error(query.error), { invalid: true });
        return historyClient.page(query.path);
      }, first ? EMPTY_STATE : current, s, { first, isCurrent: () => id === generation.current });
      if (id !== generation.current) return;
      setState(next);
      setStatus('ready');
      setError('');
    } catch (failure) {
      if (id !== generation.current) return;
      if ((failure as { invalid?: boolean }).invalid) { setStatus('invalid'); return; }
      if (isPermissionDenied(failure)) { setStatus('denied'); return; }
      setError(describeError(failure));
      setStatus(first ? 'error' : 'ready');
      if (!first) setError(describeError(failure));
    } finally {
      if (id === generation.current) setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    void run(true);
    return () => { generation.current += 1; };
  }, [key, enabled, run]);

  const loadOlder = useCallback(() => { if (!busy && latest.current.state.cursor) void run(false); }, [busy, run]);
  const reload = useCallback(() => { void run(true); }, [run]);
  return { state, status, error, busy, loadOlder, reload };
}
