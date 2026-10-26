import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgentCatalog, AgentProfile } from '@anum/contracts';
import * as api from '../lib/api';
import { agentsApi } from '../lib/agents';
import { describeError } from '../lib/errors';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
const PAGE = 50;

/** The agents a person can pick (their own visible list, searchable and paged) and the tool catalogue for risk lines.
 * `seed` is an agent that must be in the list even when it is not on the first page (the one that was clicked). */
export function useAssignAgents(seedId: string | null) {
  const [items, setItems] = useState<AgentProfile[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const alive = useRef(true);
  const seq = useRef(0);

  const load = useCallback(async (query: string, after: string | null) => {
    const mine = ++seq.current;
    setStatus((s) => (after ? s : 'loading'));
    try {
      const page = await agents.list({ category: 'all', q: query, status: 'all', cursor: after, limit: PAGE });
      let rows = page.items;
      if (!after && !query && seedId && !rows.some((a) => a.id === seedId)) {
        try { rows = [await agents.get(seedId), ...rows]; } catch { /* the seed is shown as unavailable by the dialog */ }
      }
      if (!alive.current || mine !== seq.current) return;
      setItems((old) => (after ? [...old, ...rows.filter((r) => !old.some((o) => o.id === r.id))] : rows));
      setCursor(page.next_cursor);
      setStatus('ready'); setError('');
    } catch (e) {
      if (!alive.current || mine !== seq.current) return;
      setError(describeError(e)); setStatus('error');
    }
  }, [seedId]);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { const id = setTimeout(() => void load(q, null), q ? 250 : 0); return () => clearTimeout(id); }, [q, load]);
  useEffect(() => {
    agents.catalog().then((c) => { if (alive.current) setCatalog(c); }, () => { if (alive.current) setCatalogFailed(true); });
  }, []);

  const replace = (agent: AgentProfile) => setItems((old) => old.map((a) => (a.id === agent.id ? agent : a)));
  return { items, q, setQ, status, error, catalog, catalogFailed, hasMore: cursor !== null, more: () => load(q, cursor), retry: () => load(q, null), replace };
}

export const agentClient = agents;
