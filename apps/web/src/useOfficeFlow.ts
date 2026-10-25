import { useCallback, useEffect, useRef, useState } from 'react';
import type { OfficeFlowSnapshot, OfficeTeam } from '@anum/contracts';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { createFlowClient, diffFlow, isFlowEvent, type FlowChange } from './lib/officeFlow';

const client = createFlowClient(api.apiRequest, api.currentScope);
const POLL_LIVE_MS = 20_000;
const POLL_FALLBACK_MS = 8_000;

export type FlowStatus = 'loading' | 'ready' | 'error' | 'denied';
export type FlowLink = 'live' | 'polling' | 'offline';
export interface ChangeBatch { id: number; changes: FlowChange[] }

/** The live flow of the open workspace: one read on mount, a re-read whenever the event stream
 * reports a task, run, approval or office event, and a timer as the fallback when the stream is
 * down. The state always comes from `/office/flow`; events are only a hint to read again. */
/** The shell owns the one event stream (a second stream per view piled up on every visit). It hands over a counter
 * and the type of its latest event; the flow reads again after a flow event and uses a timer as the fallback. */
export interface FlowTrigger { tick: number; type: string | null; streaming: boolean }
export function useOfficeFlow(enabled = true, trigger: FlowTrigger = { tick: 0, type: null, streaming: false }) {
  const [snapshot, setSnapshot] = useState<OfficeFlowSnapshot | null>(null);
  const [status, setStatus] = useState<FlowStatus>('loading');
  const [error, setError] = useState('');
  const [link, setLink] = useState<FlowLink>('polling');
  const [batch, setBatch] = useState<ChangeBatch>({ id: 0, changes: [] });
  const previous = useRef<OfficeFlowSnapshot | null>(null);
  const alive = useRef(true);
  const inflight = useRef<Promise<void> | null>(null);
  const streaming = useRef(false);

  const load = useCallback((): Promise<void> => {
    if (inflight.current) return inflight.current;
    const run = (async () => {
      try {
        const next = await client.load();
        if (!alive.current) return;
        const changes = diffFlow(previous.current, next);
        previous.current = next;
        setSnapshot(next);
        setStatus('ready');
        setError('');
        if (changes.length) setBatch((b) => ({ id: b.id + 1, changes }));
        setLink(streaming.current ? 'live' : 'polling');
      } catch (failure) {
        if (!alive.current) return;
        if (isPermissionDenied(failure)) { previous.current = null; setSnapshot(null); setStatus('denied'); return; }
        setError(describeError(failure));
        setLink('offline');
        setStatus((s) => (previous.current ? s : 'error'));
      } finally {
        inflight.current = null;
      }
    })();
    inflight.current = run;
    return run;
  }, []);

  useEffect(() => {
    alive.current = true;
    if (!enabled) return () => { alive.current = false; };
    previous.current = null;
    void load();
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      timer = setTimeout(() => { if (document.visibilityState === 'visible') void load(); tick(); }, streaming.current ? POLL_LIVE_MS : POLL_FALLBACK_MS);
    };
    tick();
    const onVisible = () => { if (document.visibilityState === 'visible') void load(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      alive.current = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [enabled, load]);

  // A flow event on the shell's stream: read again once (bursts coalesce).
  const streamingNow = trigger.streaming;
  useEffect(() => { streaming.current = streamingNow; setLink((l) => (l === 'offline' ? l : streamingNow ? 'live' : 'polling')); }, [streamingNow]);
  const lastTick = useRef(trigger.tick);
  useEffect(() => {
    if (!enabled || trigger.tick === lastTick.current) return undefined;
    lastTick.current = trigger.tick;
    if (!trigger.type || !isFlowEvent(trigger.type)) return undefined;
    const timer = setTimeout(() => { void load(); }, 400);
    return () => clearTimeout(timer);
  }, [enabled, trigger.tick, trigger.type, load]);

  return { snapshot, status, error, link, batch, reload: load };
}

export type TeamStatus = 'loading' | 'ready' | 'error' | 'denied';

/** The team of one office (room), floor, building or the workspace; re-read when the flow changes. */
export function useOfficeTeam(filter: { buildingId?: string | null; floorId?: string | null; roomId?: string | null }, refreshKey: string | number = 0) {
  const [team, setTeam] = useState<OfficeTeam | null>(null);
  const [status, setStatus] = useState<TeamStatus>('loading');
  const [error, setError] = useState('');
  const key = `${filter.buildingId ?? ''}|${filter.floorId ?? ''}|${filter.roomId ?? ''}`;
  useEffect(() => {
    let live = true;
    setStatus((s) => (team && s === 'ready' ? s : 'loading'));
    client.team(filter).then((next) => {
      if (!live) return;
      setTeam(next); setStatus('ready'); setError('');
    }, (failure) => {
      if (!live) return;
      if (isPermissionDenied(failure)) { setTeam(null); setStatus('denied'); return; }
      setError(describeError(failure));
      setStatus(team ? 'ready' : 'error');
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, refreshKey]);
  return { team, status, error };
}

export { client as flowClient };
