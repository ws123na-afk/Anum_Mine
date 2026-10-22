import type { Page, Route } from '@playwright/test';

// Calls & Meetings mocks (docs/meetings.md), shared by meetings.spec.ts and the accessibility and
// localization gates. `handleMeetings` answers /api/v1/meetings/** and the skill list it reads.
const now = new Date().toISOString();

export function meetingFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'meeting_pmv', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_by: 'user_local', title: 'PMV meeting',
    source: 'transcript', status: 'ready', language: 'en', requested_language: 'auto', occurred_at: now, duration_seconds: 1800,
    agent_skill_id: 'acme.release-notes', recording_file_id: null, source_file_name: 'pmv.vtt', external_id: null, segment_count: 3,
    error_code: null, error: null, summary: 'The team reviewed the PMV plan and agreed to restock Noon UAE first.',
    decisions: ['Restock Noon UAE first'],
    action_items: [
      { id: 'act_report', text: 'Send the tyre stock report', owner: 'Omar Ali', due_date: '2026-10-16', done: false, task_id: null },
      { id: 'act_prices', text: 'Confirm supplier prices', owner: null, due_date: null, done: false, task_id: null },
    ],
    follow_up: { to: [], subject: 'PMV call recap', body: 'Hello all,\n\nThanks for joining.', edited: false, send_task_id: null },
    provenance: { label: 'ai_generated', source: 'meeting_transcript', provider: 'anthropic', model: 'claude-sonnet-5-5', generated_at: now, transcript_chars: 300, truncated: false },
    created_at: now, updated_at: now, ...overrides,
  };
}

export const arabicMeeting = meetingFixture({
  id: 'meeting_ar', title: 'اجتماع خطة الإطارات', language: 'ar', agent_skill_id: null, segment_count: 2,
  summary: 'راجع الفريق خطة الإطارات واتفق على إعادة تعبئة مخزون نون الإمارات أولًا.',
  decisions: ['إعادة التعبئة لنون الإمارات أولًا'],
  action_items: [{ id: 'act_ar', text: 'إرسال تقرير المخزون', owner: 'أحمد', due_date: null, done: false, task_id: null }],
  follow_up: { to: [], subject: 'ملخص الاجتماع', body: 'مرحبًا بالجميع،\n\nشكرًا لحضوركم.', edited: false, send_task_id: null },
});

export const segments = [
  { id: 'seg_1', meeting_id: 'meeting_pmv', seq: 0, speaker: 'Jane Doe', start_ms: 1000, end_ms: 4000, text: 'Welcome to the PMV planning call.' },
  { id: 'seg_2', meeting_id: 'meeting_pmv', seq: 1, speaker: 'Omar Ali', start_ms: 5000, end_ms: 9000, text: 'I will send the tyre stock report by Friday.' },
  { id: 'seg_3', meeting_id: 'meeting_pmv', seq: 2, speaker: 'Jane Doe', start_ms: 10000, end_ms: 12000, text: 'We decided to restock Noon UAE first.' },
];
export const arabicSegments = [
  { id: 'seg_ar1', meeting_id: 'meeting_ar', seq: 0, speaker: 'سارة', start_ms: 0, end_ms: 3000, text: 'صباح الخير، نراجع خطة الإطارات.' },
  { id: 'seg_ar2', meeting_id: 'meeting_ar', seq: 1, speaker: 'أحمد', start_ms: 4000, end_ms: 7000, text: 'سأرسل تقرير المخزون يوم الخميس.' },
];

export function setupFixture(overrides: Record<string, unknown> = {}) {
  return {
    transcription: { configured: false, provider: 'none', model: null, target: null, problem: 'Recording transcription is off on this server. Ask the server administrator to set ANUM_TRANSCRIPTION_PROVIDER.', max_recording_bytes: 26214400, accepted_extensions: ['.m4a', '.mp3', '.mp4', '.wav', '.webm'] },
    transcript_extensions: ['.vtt', '.srt', '.txt', '.docx'], max_transcript_bytes: 5242880,
    connectors: [
      { provider: 'zoom', available: true, problem: null, connected: true, account_label: 'owner@example.com', connected_at: now },
      { provider: 'teams', available: false, problem: 'Not set up on this server: register a Microsoft Entra app registration with delegated Graph permissions and set ANUM_MICROSOFT_CLIENT_ID.', connected: false, account_label: null, connected_at: null },
    ],
    model_ready: true, email_accounts: [], can_create: true, can_manage_all: true, ...overrides,
  };
}

/** Answers a meetings request with the read-only fixtures; false when the path is not a meetings path. */
export async function handleMeetings(route: Route, items = [meetingFixture(), arabicMeeting]): Promise<boolean> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  // Live agent meetings (the room launcher above the imported meetings): none in these fixtures.
  if (path === '/api/v1/agent-meetings' && request.method() === 'GET') { await json({ items: [], next_cursor: null }); return true; }
  if (path === '/api/v1/meetings/setup') { await json(setupFixture()); return true; }
  if (path === '/api/v1/meetings' && request.method() === 'GET') {
    const status = url.searchParams.get('status');
    const found = items.filter((item) => !status || item.status === status);
    await json({ items: found, total: found.length, offset: 0, limit: Number(url.searchParams.get('limit') ?? 20) });
    return true;
  }
  const one = /^\/api\/v1\/meetings\/([^/]+)(\/transcript)?$/.exec(path);
  if (one && request.method() === 'GET') {
    const meeting = items.find((item) => item.id === one[1]);
    if (!meeting) { await json({ error: { code: 'not_found', message: 'Meeting not found in this workspace' } }, 404); return true; }
    if (one[2]) {
      const rows = meeting.language === 'ar' ? arabicSegments : segments;
      await json({ items: rows, total: rows.length, offset: 0, limit: 100, speakers: [...new Set(rows.map((row) => row.speaker))].sort() });
    } else await json(meeting);
    return true;
  }
  return false;
}

export async function routeMeetingsOnly(page: Page): Promise<void> {
  await page.route('http://localhost:8000/api/v1/meetings**', async (route) => { if (!(await handleMeetings(route))) await route.abort(); });
}
