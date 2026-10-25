// Network side of the live meeting room: JSON calls and the replayable SSE follower.
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { parseSse, type RoomBundle, type RoomEvent, type RoomMessage, type RoomOutput, type AgendaItem, type OutputKind } from './liveMeeting';

const root = '/api/v1/agent-meetings';
const one = (id: string) => `${root}/${encodeURIComponent(id)}`;
const post = <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

export interface MeetingSummary { id: string; title: string; status: string; revision: number; created_by: string }

export const meetingRoomApi = {
  list: () => apiRequest<{ items: MeetingSummary[] }>(`${root}?limit=50`, { method: 'GET' }),
  room: (id: string) => apiRequest<RoomBundle>(`${one(id)}/room`, { method: 'GET' }),
  /** Every stored message, oldest first (at most 200 per meeting). */
  async turns(id: string): Promise<RoomMessage[]> {
    const out: RoomMessage[] = [];
    let after = 0;
    for (let page = 0; page < 5; page++) {
      const result = await apiRequest<{ items: RoomMessage[]; next_after: number | null }>(`${one(id)}/turns?after=${after}&limit=100`, { method: 'GET' });
      out.push(...result.items);
      if (!result.next_after) break;
      after = result.next_after;
    }
    return out;
  },
  events: (id: string, after: number) => apiRequest<{ items: RoomEvent[]; next_after: number | null }>(`${one(id)}/events?after=${after}&limit=200`, { method: 'GET' }),
  presence: (id: string, action: 'join' | 'leave') => post(`${one(id)}/presence`, { action }),
  hand: (id: string, action: 'raise' | 'lower') => post(`${one(id)}/hand`, { action }),
  say: (id: string, body: string) => post<RoomMessage>(`${one(id)}/messages`, { request_key: 'room-' + crypto.randomUUID(), body }),
  /** One governed model turn. The server picks the speaker, enforces every budget and records the result. */
  runTurn: (id: string) => post(`${one(id)}/run`, { run_key: 'room-' + crypto.randomUUID(), turns: 1 }),
  async setState(id: string, status: 'active' | 'paused' | 'finished' | 'cancelled') {
    for (let attempt = 0; ; attempt++) {
      const meeting = await apiRequest<{ revision: number }>(one(id), { method: 'GET' });
      try { return await post(`${one(id)}/state`, { status, expected_revision: meeting.revision }); }
      catch (error) { if (attempt >= 1 || (error as { status?: number }).status !== 409) throw error; }
    }
  },
  addAgenda: (id: string, title: string) => post<AgendaItem>(`${one(id)}/agenda`, { title }),
  patchAgenda: (id: string, item: string, body: { title?: string; state?: 'open' | 'current' | 'done' }) => apiRequest<AgendaItem>(`${one(id)}/agenda/${encodeURIComponent(item)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteAgenda: (id: string, item: string) => apiRequestVoid(`${one(id)}/agenda/${encodeURIComponent(item)}`, { method: 'DELETE' }),
  draft: (id: string, body: { kind: OutputKind; body: string; owner_label?: string | null; source_sequences: number[] }) => post<RoomOutput>(`${one(id)}/outputs`, body),
  decide: (id: string, output: string, body: { state?: 'confirmed' | 'dismissed'; body?: string }) => apiRequest<RoomOutput>(`${one(id)}/outputs/${encodeURIComponent(output)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  createTask: (id: string, output: string) => post<RoomOutput>(`${one(id)}/outputs/${encodeURIComponent(output)}/task`),
  linkFile: (id: string, fileId: string) => post(`${one(id)}/files`, { file_id: fileId }),
  unlinkFile: (id: string, fileId: string) => apiRequestVoid(`${one(id)}/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
  async exportMarkdown(id: string): Promise<string> {
    const response = await fetch(`${apiBaseUrl}${one(id)}/export?format=markdown`, { headers: await authHeaders() });
    if (!response.ok) throw await apiErrorFromResponse(response);
    return response.text();
  },
  deleteTranscript: (id: string) => apiRequest<{ turns: number; events: number }>(`${one(id)}/transcript`, { method: 'DELETE' }),
  async agentName(id: string): Promise<string | null> {
    try { return (await apiRequest<{ name: string }>(`/api/v1/agents/${encodeURIComponent(id)}`, { method: 'GET' })).name; } catch { return null; }
  },
};

/**
 * Follow the event log from `after`. Resumes with Last-Event-ID after any break (network drop,
 * the server's bounded stream lifetime), so no event is lost or repeated. Resolves when the
 * meeting is over and drained, or when `signal` aborts.
 */
export async function followRoom(id: string, after: number, onEvent: (event: RoomEvent) => void, onStatus: (online: boolean) => void, signal: AbortSignal): Promise<void> {
  let cursor = after, failures = 0;
  while (!signal.aborted) {
    try {
      const response = await fetch(`${apiBaseUrl}${one(id)}/events/stream`, { headers: { ...(await authHeaders()), 'Last-Event-ID': String(cursor) }, signal });
      if (response.status === 403 || response.status === 404) throw await apiErrorFromResponse(response);
      if (!response.ok || !response.body) throw new Error('stream ' + response.status);
      onStatus(true); failures = 0;
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSse(buffer);
        buffer = parsed.rest;
        for (const e of parsed.events) { if (e.seq > cursor) { cursor = e.seq; onEvent(e); } }
        if (parsed.end) return;
      }
      // The server ends a stream on purpose after a few minutes: resume at once from the same cursor.
      await new Promise((resolve) => setTimeout(resolve, 300));
      continue;
    } catch (error) {
      if (signal.aborted) return;
      if ((error as { status?: number }).status === 403 || (error as { status?: number }).status === 404) throw error;
      onStatus(false);
    }
    failures += 1;
    await new Promise((resolve) => setTimeout(resolve, Math.min(15_000, 1000 * 2 ** Math.min(failures, 4))));
  }
}
