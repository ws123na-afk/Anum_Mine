// In-app calls over the live API (docs/in-app-calls.md#api). Every rule (same workspace,
// participants only, who sends the offer) is the server's; this only calls it.
import type {
  AppCall, AppCallConfig, AppCallEndReason, AppCallIceCandidate, AppCallIceServers, AppCallMedia, AppCallPage, AppCallSignal,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { parseSse, reconnectDelay } from './appCalls';

const id = encodeURIComponent;
const post = <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

export const getCallConfig = () => apiRequest<AppCallConfig>('/api/v1/app-calls/config', { method: 'GET' });
export const getIceServers = () => apiRequest<AppCallIceServers>('/api/v1/app-calls/ice-servers', { method: 'GET' });
export const placeCall = (calleeUserId: string, media: AppCallMedia, clientId: string) =>
  post<AppCall>('/api/v1/app-calls', { callee_user_id: calleeUserId, media, client_id: clientId });
export const acceptCall = (callId: string, clientId: string) => post<AppCall>(`/api/v1/app-calls/${id(callId)}/accept`, { client_id: clientId });
export const endCall = (callId: string, reason: AppCallEndReason = 'hangup') => post<AppCall>(`/api/v1/app-calls/${id(callId)}/end`, { reason });
export const sendHeartbeat = (callId: string) => apiRequestVoid(`/api/v1/app-calls/${id(callId)}/heartbeat`, { method: 'POST' });
export const sendDescription = (callId: string, kind: 'offer' | 'answer', sdp: string) => post<{ id: string }>(`/api/v1/app-calls/${id(callId)}/signals`, { kind, sdp });
export const sendCandidates = (callId: string, candidates: AppCallIceCandidate[]) => post<{ id: string }>(`/api/v1/app-calls/${id(callId)}/signals`, { kind: 'ice', candidates });

export function listCalls(options: { cursor?: string | null; missed?: boolean; limit?: number } = {}): Promise<AppCallPage> {
  const query = new URLSearchParams();
  query.set('limit', String(options.limit ?? 20));
  if (options.cursor) query.set('cursor', options.cursor);
  if (options.missed) query.set('missed', 'true');
  return apiRequest<AppCallPage>(`/api/v1/app-calls?${query}`, { method: 'GET' });
}

export interface CallStreamHandlers {
  onCall(call: AppCall): void;
  onSignal(signal: AppCallSignal): void;
  onState(state: 'connecting' | 'live' | 'offline'): void;
}

/**
 * This device's call stream, reconnecting until ``signal`` aborts. The server re-sends the
 * current calls on every connection, and signals are de-duplicated by id here.
 */
export async function runCallStream(clientId: string, handlers: CallStreamHandlers, signal: AbortSignal): Promise<void> {
  const seen = new Set<string>();
  let failures = 0;
  while (!signal.aborted) {
    handlers.onState('connecting');
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/app-calls/stream?client_id=${id(clientId)}`, { headers: await authHeaders(), signal });
      if (!response.ok || !response.body) throw await apiErrorFromResponse(response);
      handlers.onState('live');
      failures = 0;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (!signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSse(buffer);
        buffer = parsed.rest;
        for (const frame of parsed.frames) {
          if (frame.event === 'call') handlers.onCall(JSON.parse(frame.data) as AppCall);
          else if (frame.event === 'signal') {
            const message = JSON.parse(frame.data) as AppCallSignal;
            if (seen.has(message.id)) continue;
            seen.add(message.id);
            handlers.onSignal(message);
          }
        }
      }
    } catch (error) {
      if (signal.aborted) return;
      failures += 1;
      handlers.onState('offline');
      // 401/403/503 will not heal by retrying fast; the backoff caps at 15 s.
      void error;
    }
    if (signal.aborted) return;
    await new Promise((resolve) => setTimeout(resolve, reconnectDelay(failures)));
  }
}
