/**
 * The browser's wiring of the presence hub (lazy chunk: the stream code loads with the headquarters, not with the shell):
 * sign-in headers, the open workspace and real clocks. See lib/presenceStream.ts.
 */
import type { OfficePresenceSnapshot } from '@anum/contracts';
import * as api from './api.ts';
import { PRESENCE_PATH, PRESENCE_STREAM_PATH } from './officePresenceClient.ts';
import { PresenceHub, acquirePresenceHub, presenceWithStaleness, type PresenceHubDeps, type StreamResponse } from './presenceStream.ts';

async function openPresenceStream(lastEventId: string | null, signal: AbortSignal): Promise<StreamResponse> {
  const response = await fetch(`${api.apiBaseUrl}${PRESENCE_STREAM_PATH}`, {
    headers: { ...(await api.authHeaders()), accept: 'text/event-stream', ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}) },
    cache: 'no-store', signal,
  });
  const reader = response.ok && response.body ? response.body.getReader() : null;
  const decoder = new TextDecoder();
  return {
    status: response.ok && !reader ? 502 : response.status,
    contentType: response.headers.get('content-type'),
    async read() {
      if (!reader) return { done: true };
      const next = await reader.read();
      return next.done ? { done: true } : { done: false, value: decoder.decode(next.value, { stream: true }) };
    },
    cancel() { void reader?.cancel().catch(() => undefined); },
  };
}

export function createBrowserPresenceHub(): PresenceHub {
  const deps: PresenceHubDeps = {
    openStream: openPresenceStream,
    loadSnapshot: (signal) => api.apiRequest<OfficePresenceSnapshot>(PRESENCE_PATH, { method: 'GET', signal }),
    scope: () => api.currentScope(),
    now: () => Date.now(),
    setTimer: (fn, ms) => { const id = window.setTimeout(fn, ms); return () => window.clearTimeout(id); },
    random: () => Math.random(),
  };
  return new PresenceHub(deps);
}

export { acquirePresenceHub, presenceWithStaleness };
