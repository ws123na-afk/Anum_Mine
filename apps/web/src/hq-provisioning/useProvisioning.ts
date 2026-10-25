import { useCallback, useEffect, useRef, useState } from 'react';
import type { ProvisioningGaps } from '@anum/contracts';
import * as api from '../lib/api';
import { describeError, isPermissionDenied } from '../lib/errors';
import { createProvisioningClient } from './client';

export const provisioningClient = createProvisioningClient(api.apiRequest, api.currentScope);
export type ProvisioningStatus = 'loading' | 'ready' | 'error' | 'denied';

/** Reads the gaps once, and again when `refreshKey` changes (the live flow moved) or `reload` is called. */
export function useProvisioningGaps(refreshKey: string | number = 0) {
  const [gaps, setGaps] = useState<ProvisioningGaps | null>(null);
  const [status, setStatus] = useState<ProvisioningStatus>('loading');
  const [error, setError] = useState('');
  const alive = useRef(true);
  const load = useCallback(async () => {
    try {
      const next = await provisioningClient.gaps();
      if (!alive.current) return;
      setGaps(next); setStatus('ready'); setError('');
    } catch (failure) {
      if (!alive.current) return;
      if (isPermissionDenied(failure)) { setGaps(null); setStatus('denied'); return; }
      setError(describeError(failure));
      setStatus((s) => (s === 'ready' ? s : 'error'));
    }
  }, []);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load, refreshKey]);
  return { gaps, status, error, reload: load };
}
