import { useEffect, useRef, useState } from 'react';
import type { DomainEvent, TenantContext } from '@anum/contracts';
import { apiBaseUrl, authHeaders, mapEvent, type ApiDomainEvent } from './api';

/**
 * Live connection to GET /api/v1/events/stream (see anum_api/realtime.py).
 *
 * Deliberately built on `fetch` + a manual `text/event-stream` parser rather
 * than the browser's native `EventSource`: EventSource cannot send custom
 * request headers, so it has no way to carry the `authorization: Bearer ...`
 * header OIDC mode needs (see lib/api.ts's authHeaders(), which this hook
 * reuses so both request paths always agree on the caller's identity).
 */
export type EventStreamStatus = 'connecting' | 'open' | 'closed' | 'error';

export interface UseEventStreamResult {
  status: EventStreamStatus;
  events: DomainEvent[];
  error: string | null;
}

interface ParsedSseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** How long to wait before retrying after the stream ends or errors -
 * exported so tests can shrink it instead of waiting out a real delay. */
export const RECONNECT_DELAY_MS = 2000;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });
}

/** Parse one `\n\n`-delimited SSE frame into its id/event/data fields.
 * Returns null for a frame with no `data:` line (e.g. a bare comment/ping). */
export function parseSseFrame(raw: string): ParsedSseFrame | null {
  let id: string | undefined;
  let event: string | undefined;
  const dataLines: string[] = [];

  for (const line of raw.split('\n')) {
    if (line.startsWith('id:')) {
      id = line.slice(3).trim();
    } else if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return null;
  return { id, event, data: dataLines.join('\n') };
}

/**
 * Subscribes to this tenant/workspace's live event stream for as long as
 * the component is mounted, reconnecting (with the last-seen event id as
 * the reconnect cursor, so no events are missed - see realtime.py's
 * `cursor`/`Last-Event-ID` handling) whenever the stream ends or errors.
 * The fetch is aborted on unmount or when tenant/workspace identity changes.
 */
export function useEventStream(tenantContext: TenantContext): UseEventStreamResult {
  const [status, setStatus] = useState<EventStreamStatus>('connecting');
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    lastEventIdRef.current = null;
    setEvents([]);
    setError(null);

    async function connectOnce(): Promise<void> {
      const headers = await authHeaders();
      const cursor = lastEventIdRef.current;
      const url = cursor
        ? `${apiBaseUrl}/api/v1/events/stream?cursor=${encodeURIComponent(cursor)}`
        : `${apiBaseUrl}/api/v1/events/stream`;

      const response = await fetch(url, { headers, signal: controller.signal });
      if (!response.ok || !response.body) {
        throw new Error(`Realtime stream request failed: ${response.status}`);
      }

      setStatus('open');
      setError(null);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      try {
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf('\n\n');
          while (boundary !== -1) {
            const rawFrame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const frame = parseSseFrame(rawFrame);
            if (frame) {
              if (frame.id) lastEventIdRef.current = frame.id;
              try {
                const event = mapEvent(JSON.parse(frame.data) as ApiDomainEvent);
                setEvents((previous) => [...previous, event]);
              } catch {
                // Malformed data payload - drop this one frame, keep streaming.
              }
            }
            boundary = buffer.indexOf('\n\n');
          }
        }
      } finally {
        reader.releaseLock();
      }
    }

    async function runLoop(): Promise<void> {
      while (!controller.signal.aborted) {
        setStatus((previous) => (previous === 'open' ? 'connecting' : previous));
        try {
          await connectOnce();
          // The stream ended (server closed the response) without the
          // client asking for it - reconnect using the cursor we've built
          // up so far rather than treating this as a terminal 'closed'.
        } catch (err) {
          if (controller.signal.aborted) return;
          setStatus('error');
          setError(err instanceof Error ? err.message : 'Realtime stream failed.');
        }
        if (controller.signal.aborted) return;
        try {
          await sleep(RECONNECT_DELAY_MS, controller.signal);
        } catch {
          return;
        }
      }
    }

    setStatus('connecting');
    void runLoop();

    return () => {
      // Aborting first means every `controller.signal.aborted` check above
      // already short-circuits before touching state again - this flip to
      // 'closed' is the only state update guaranteed to run after that, so
      // it can't race with a stray in-flight setStatus('open')/('error').
      controller.abort();
      setStatus('closed');
    };
    // Only reconnect when the identity we're authorized to stream as
    // changes - not on every render, and not just because `tenantContext`
    // is a fresh object reference each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantContext.tenantId, tenantContext.workspaceId]);

  return { status, events, error };
}
