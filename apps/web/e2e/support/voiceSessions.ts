// `GET /api/v1/voice/sessions` (docs/voice.md#recent-calls) for the Call page: the caller's own calls,
// newest first, two per page so "Load older" is exercised, and their transcripts.
const at = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
const session = (id: string, minutesAgo: number, extra: Record<string, unknown>) => ({
  id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', locale: 'en-US', retention: 'permanent', assistant_name: 'Anum',
  status: 'completed', created_at: at(minutesAgo), updated_at: at(minutesAgo - 4), expires_at: null, segment_count: 0, first_line: null, ...extra,
});
export const voiceSessions = [
  session('voice_recent', 30, { segment_count: 4, first_line: 'Which orders ship today?' }),
  session('voice_arabic', 300, { locale: 'ar-AE', segment_count: 2, first_line: 'كم إطارًا في المخزون؟' }),
  session('voice_erased', 1500, { retention: 'session', status: 'cancelled' }),
];
const transcripts: Record<string, unknown[]> = {
  voice_recent: [
    { id: 's1', session_id: 'voice_recent', role: 'user', text: 'Which orders ship today?', is_final: true, client_sequence: 0, created_at: at(30) },
    { id: 's2', session_id: 'voice_recent', role: 'assistant', text: 'Eight orders ship today; one is late.', is_final: true, client_sequence: 1, created_at: at(30) },
    { id: 's3', session_id: 'voice_recent', role: 'user', text: 'Which one is late?', is_final: true, client_sequence: 2, created_at: at(29) },
    { id: 's4', session_id: 'voice_recent', role: 'assistant', text: 'Order 402-0000001 on Amazon.sa.', is_final: true, client_sequence: 3, created_at: at(29) },
  ],
  voice_arabic: [
    { id: 'a1', session_id: 'voice_arabic', role: 'user', text: 'كم إطارًا في المخزون؟', is_final: true, client_sequence: 0, created_at: at(300) },
    { id: 'a2', session_id: 'voice_arabic', role: 'assistant', text: 'لديك 64 إطارًا.', is_final: true, client_sequence: 1, created_at: at(300) },
  ],
  voice_erased: [],
};

export function voiceSessionsReply(url: URL, method = 'GET', items = voiceSessions): { body: unknown; status: number } | null {
  if (method !== 'GET') return null;
  if (url.pathname === '/api/v1/voice/sessions') {
    const cursor = url.searchParams.get('cursor');
    const limit = Number(url.searchParams.get('limit') ?? 20);
    const status = url.searchParams.get('status');
    const rows = items.filter((s) => !status || s.status === status);
    const start = cursor ? rows.findIndex((s) => s.id === cursor) + 1 : 0;
    const page = rows.slice(start, start + limit);
    return { body: { items: page, next_cursor: start + limit < rows.length ? page[page.length - 1].id : null }, status: 200 };
  }
  const match = /^\/api\/v1\/voice\/sessions\/([^/]+)\/transcript$/.exec(url.pathname);
  if (match && match[1] in transcripts) return { body: transcripts[match[1]], status: 200 };
  return null;
}
