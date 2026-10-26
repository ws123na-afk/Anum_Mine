/** The one-click campus flow (docs/ANUM_MINE_STARTER_CAMPUS.md#first-run): read the plan, show it, confirm exactly that plan on a click.
 * Governance is the starter campus's own: the preview stores nothing, `confirm` always sends `input_mode: "click"` with the hash the person was
 * shown, and the hook has no way to confirm except `create()`, which only a button handler calls. A stale hash (409) re-reads the plan and asks again. */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { StarterCampusResult } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError, isPermissionDenied } from '../lib/errors';
import { createStarterCampusClient } from '../hq-starter-campus/client';
import { canCreate, INITIAL_SETUP, type SetupState } from './campusSetupModel';
export { canCreate, createCounts, type SetupPhase, type SetupState } from './campusSetupModel';

export const campusClient = createStarterCampusClient(api.apiRequest, api.currentScope);

export function useCampusSetup(onCreated: (result: StarterCampusResult) => void): SetupState & { reload: () => void; create: () => void } {
  const [state, setState] = useState<SetupState>(INITIAL_SETUP);
  const alive = useRef(true);
  const serial = useRef(0);
  const busy = useRef(false);
  const latest = useRef(state);
  latest.current = state;
  const created = useRef(onCreated);
  created.current = onCreated;

  const read = useCallback(async (keep: Partial<SetupState> = {}) => {
    const mine = ++serial.current;
    setState((s) => ({ ...s, ...keep, phase: 'loading', detail: keep.detail ?? null }));
    try {
      const preview = await campusClient.preview(null);
      if (!alive.current || mine !== serial.current) return;
      setState((s) => ({ ...s, ...keep, phase: 'ready', preview, result: keep.result ?? null, detail: keep.detail ?? null }));
    } catch (e) {
      if (!alive.current || mine !== serial.current) return;
      setState((s) => ({ ...s, ...keep, phase: isPermissionDenied(e) ? 'denied' : 'error', detail: describeError(e) }));
    }
  }, []);
  useEffect(() => { alive.current = true; void read(); return () => { alive.current = false; }; }, [read]);

  const create = useCallback(async () => {
    const now = latest.current;
    if (busy.current || !canCreate(now) || !now.preview) return;
    busy.current = true;
    setState((s) => ({ ...s, phase: 'creating', detail: null, stale: false }));
    try {
      const result = await campusClient.confirm(now.preview);
      if (!alive.current) return;
      setState((s) => ({ ...s, phase: result.outcome === 'failed' ? 'failed' : result.outcome === 'partial' ? 'partial' : 'done', result, detail: null }));
      // Anything the server wrote (or found already written) is shown at once; a failed run wrote nothing.
      if (result.outcome !== 'failed') created.current(result);
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof ApiError && e.status === 409) { busy.current = false; await read({ stale: true }); return; }
      setState((s) => ({ ...s, phase: isPermissionDenied(e) ? 'denied' : 'ready', detail: describeError(e) }));
    } finally { busy.current = false; }
  }, [read]);

  return { ...state, reload: () => void read(), create: () => void create() };
}
