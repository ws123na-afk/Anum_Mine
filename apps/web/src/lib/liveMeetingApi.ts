// Network side of the live meeting room: JSON calls and the replayable SSE follower.
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { parseSse, type MeetingBudget, type RoomBundle, type RoomEvent, type RoomMessage, type RoomOutput, type AgendaItem, type OutputKind, type SummaryResult, type WorkKind, type WorkLink } from './liveMeeting';

const root = '/api/v1/agent-meetings';
const one = (id: string) => `${root}/${encodeURIComponent(id)}`;
const post = <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

export interface MeetingSummary { id: string; title: string; status: string; revision: number; created_by: string }

export const meetingRoomApi = {
  list: () => apiRequest<{ items: MeetingSummary[] }>(`${root}?limit=50`, { method: 'GET' }),
  /** This month's agent meeting spend against the workspace cap. Any member may read it. */
  budget: () => apiRequest<MeetingBudget>(`${root}/budget`, { method: 'GET' }),
  /** Owner only. `null` returns to the default (USD 50). */
  setBudget: (monthlyCostLimitUsd: number | null) => apiRequest<MeetingBudget>(`${root}/budget`, { method: 'PUT', body: JSON.stringify({ monthly_cost_limit_usd: monthlyCostLimitUsd }) }),
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
  /** The owner removes an agent from the live meeting: it leaves now (the server stores the time) and cannot rejoin. */
  async removeAgent(id: string, agentId: string) {
    for (let attempt = 0; ; attempt++) {
      const meeting = await apiRequest<{ revision: number }>(one(id), { method: 'GET' });
      try { return await post(`${one(id)}/agents/${encodeURIComponent(agentId)}/remove`, { expected_revision: meeting.revision }); }
      catch (error) { if (attempt >= 1 || (error as { status?: number }).status !== 409) throw error; }
    }
  },
  addAgenda: (id: string, title: string) => post<AgendaItem>(`${one(id)}/agenda`, { title }),
  patchAgenda: (id: string, item: string, body: { title?: string; state?: 'open' | 'current' | 'done' }) => apiRequest<AgendaItem>(`${one(id)}/agenda/${encodeURIComponent(item)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteAgenda: (id: string, item: string) => apiRequestVoid(`${one(id)}/agenda/${encodeURIComponent(item)}`, { method: 'DELETE' }),
  draft: (id: string, body: { kind: OutputKind; body: string; owner_label?: string | null; source_sequences: number[] }) => post<RoomOutput>(`${one(id)}/outputs`, body),
  /** Owner only, ended meeting: one metered model call. Everything comes back as drafts; the monthly budget applies (402 at the cap). */
  draftSummary: (id: string) => post<SummaryResult>(`${one(id)}/summary`),
  /** Confirming a summary or a model draft is a click on screen and carries the hash of the text that was shown. */
  confirmDraft: (id: string, output: RoomOutput) => apiRequest<RoomOutput>(`${one(id)}/outputs/${encodeURIComponent(output.id)}`, { method: 'PATCH', body: JSON.stringify({ state: 'confirmed', input_mode: 'click', payload_hash: output.payload_hash }) }),
  linkWork: (id: string, output: string, kind: WorkKind, target: string) => post<WorkLink>(`${one(id)}/outputs/${encodeURIComponent(output)}/links`, { target_kind: kind, target_id: target, input_mode: 'click' }),
  unlinkWork: (id: string, link: string) => apiRequestVoid(`${one(id)}/links/${encodeURIComponent(link)}`, { method: 'DELETE' }),
  decide: (id: string, output: string, body: { state?: 'confirmed' | 'dismissed' | 'draft'; body?: string }) => apiRequest<RoomOutput>(`${one(id)}/outputs/${encodeURIComponent(output)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  createTask: (id: string, output: string) => post<RoomOutput>(`${one(id)}/outputs/${encodeURIComponent(output)}/task`),
  linkFile: (id: string, fileId: string) => post(`${one(id)}/files`, { file_id: fileId }),
  unlinkFile: (id: string, fileId: string) => apiRequestVoid(`${one(id)}/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
  /** The record of the meeting as text: Markdown, or JSON with the summary, decisions (with their linked work) and confirmed outputs. */
  async exportRecord(id: string, format: 'markdown' | 'json' = 'markdown'): Promise<string> {
    const response = await fetch(`${apiBaseUrl}${one(id)}/export?format=${format}`, { headers: await authHeaders() });
    if (!response.ok) throw await apiErrorFromResponse(response);
    return response.text();
  },
  exportMarkdown(id: string): Promise<string> { return this.exportRecord(id, 'markdown'); },
  /** Which confirmed decisions led to this piece of work (owner only). */
  workLinks: (kind: WorkKind, target: string) => apiRequest<{ items: { link_id: string; meeting_id: string; title: string; status: string; output_id: string; body: string }[] }>(`${root}/work-links?target_kind=${kind}&target_id=${encodeURIComponent(target)}`, { method: 'GET' }),
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
