import { expect, test, type Page } from '@playwright/test';
import { arabicMeeting, arabicSegments, meetingFixture, segments, setupFixture } from './support/meetingsApi';
import { useSavedLocale } from './support/workspaceApi';

// Calls & Meetings (docs/meetings.md): list, recent activity, detail with AI notes, transcript
// search, imports (file, Zoom), action items to tasks, the follow-up draft, roles, errors, Arabic.
type Meeting = ReturnType<typeof meetingFixture>;

async function mockApi(page: Page, options: { setup?: Record<string, unknown>; items?: Meeting[]; listStatus?: number } = {}) {
  const items: Meeting[] = options.items ?? [meetingFixture()];
  const calls = { sends: [] as unknown[], uploads: [] as string[], callbacks: [] as unknown[], tasks: 0, followUps: [] as unknown[], imports: [] as unknown[], listFails: options.listStatus ?? 0 };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/skills/installations') return json([{ skill_id: 'acme.release-notes', enabled: true }]);
    if (path === '/api/v1/meetings/setup') return json(setupFixture(options.setup));
    if (path === '/api/v1/meetings' && method === 'GET') {
      if (calls.listFails) { calls.listFails = 0; return json({ error: { code: 'internal_error', message: 'Database unavailable' } }, 500); }
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const status = url.searchParams.get('status');
      const found = items.filter((item) => (!q || item.title.toLowerCase().includes(q) || (item.summary ?? '').toLowerCase().includes(q)) && (!status || item.status === status));
      return json({ items: found, total: found.length, offset: 0, limit: 20 });
    }
    if (path === '/api/v1/meetings/transcripts' && method === 'POST') {
      calls.uploads.push(url.searchParams.get('name') ?? '');
      const created = meetingFixture({ id: 'meeting_new', title: url.searchParams.get('title') || 'weekly', status: 'analyzing', summary: null, decisions: [], action_items: [], follow_up: null, provenance: null });
      items.unshift(created);
      // The summary is ready the next time the page asks.
      setTimeout(() => { items[0] = meetingFixture({ id: 'meeting_new', title: created.title }); }, 300);
      return json(created, 201);
    }
    if (path === '/api/v1/meetings/connectors/callback' && method === 'POST') {
      calls.callbacks.push(request.postDataJSON());
      return json({ provider: 'zoom', available: true, connected: true, account_label: 'owner@example.com', connected_at: '2026-10-11T09:00:00Z' });
    }
    if (path === '/api/v1/meetings/connectors/zoom/meetings') return json([
      { external_id: 'zoom-1', title: 'Noon UAE weekly', started_at: '2026-10-10T08:00:00Z', duration_minutes: 30, imported_meeting_id: null },
      { external_id: 'zoom-2', title: 'Amazon.sa review', started_at: '2026-10-09T08:00:00Z', duration_minutes: 45, imported_meeting_id: 'meeting_pmv' },
    ]);
    if (path === '/api/v1/meetings/connectors/zoom/import' && method === 'POST') {
      calls.imports.push(request.postDataJSON());
      const created = meetingFixture({ id: 'meeting_zoom', title: 'Noon UAE weekly', source: 'zoom' });
      items.unshift(created);
      return json(created, 201);
    }
    const one = /^\/api\/v1\/meetings\/([^/]+)(\/.*)?$/.exec(path);
    if (one) {
      const index = items.findIndex((item) => item.id === one[1]);
      const meeting = items[index];
      if (!meeting) return json({ error: { code: 'not_found', message: 'Meeting not found in this workspace' } }, 404);
      const rest = one[2] ?? '';
      if (rest === '' && method === 'GET') return json(meeting);
      if (rest === '/transcript') {
        const rows = (meeting.language === 'ar' ? arabicSegments : segments).filter((row) => {
          const q = (url.searchParams.get('q') ?? '').toLowerCase();
          const speaker = url.searchParams.get('speaker');
          return (!q || row.text.toLowerCase().includes(q)) && (!speaker || row.speaker === speaker);
        });
        return json({ items: rows, total: rows.length, offset: 0, limit: 100, speakers: ['Jane Doe', 'Omar Ali'] });
      }
      const task = /^\/action-items\/([^/]+)\/task$/.exec(rest);
      if (task && method === 'POST') {
        calls.tasks += 1;
        items[index] = { ...meeting, action_items: meeting.action_items.map((item) => item.id === task[1] ? { ...item, task_id: 'task_1' } : item) };
        return json(items[index]);
      }
      if (rest === '/follow-up' && method === 'PUT') {
        const body = request.postDataJSON();
        calls.followUps.push(body);
        items[index] = { ...meeting, follow_up: { ...body, edited: true, send_task_id: null } };
        return json(items[index]);
      }
      if (rest === '/follow-up/send' && method === 'POST') {
        calls.sends.push(request.postDataJSON());
        items[index] = { ...meeting, follow_up: { ...meeting.follow_up!, approval_id: 'approval_1', email_draft_id: 'emd_1', send_task_id: 'task_send', sent_from: 'owner@example.com' } };
        return json(items[index]);
      }
    }
    return route.abort();
  });
  return calls;
}

test('the list shows recent call summaries and opens a meeting with labelled AI notes and a searchable transcript', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#meetings');
  await expect(page.getByRole('heading', { level: 1, name: 'Calls & Meetings' })).toBeVisible();
  const activity = page.getByRole('region', { name: 'Recent activity' });
  await expect(activity.getByRole('button', { name: 'Call summary: PMV meeting' })).toBeVisible();
  await activity.getByRole('button', { name: 'Call summary: PMV meeting' }).click();

  const detail = page.locator('.meetingDetail');
  await expect(detail.getByRole('heading', { name: 'PMV meeting' })).toBeFocused();
  await expect(detail.getByText('AI-generated · claude-sonnet-5-5')).toBeVisible();
  await expect(detail.getByText('Restock Noon UAE first', { exact: true })).toBeVisible();
  await expect(detail.locator('.actionItems li')).toHaveCount(2);
  await expect(page).toHaveURL(/#meetings\?meeting=meeting_pmv$/);

  await detail.getByRole('searchbox', { name: 'Search the transcript' }).fill('tyre');
  await detail.getByRole('searchbox', { name: 'Search the transcript' }).press('Enter');
  await expect(detail.locator('.transcriptLines li')).toHaveCount(1);
  await expect(detail.locator('.transcriptLines mark')).toHaveText('tyre');
  await expect(detail.locator('.transcriptTime').first()).toHaveText('00:05');

  await detail.getByRole('button', { name: 'Close meeting' }).click();
  await expect(detail).toHaveCount(0);
});

test('an action item becomes a task once, and the follow-up draft is checked, saved and offered to the mail app', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#meetings?meeting=meeting_pmv');
  const detail = page.locator('.meetingDetail');
  await detail.getByRole('button', { name: 'Create a task for Send the tyre stock report' }).click();
  await expect(detail.getByText('Task created. Running it follows the workspace approval policy.')).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Task created' })).toBeVisible();
  expect(calls.tasks).toBe(1);

  const to = detail.getByRole('textbox', { name: 'To' });
  await to.fill('jane@example.com, not-an-email');
  await expect(detail.getByText('“not-an-email” is not an email address.')).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  await to.fill('jane@example.com, omar@example.com');
  await detail.getByRole('button', { name: 'Save draft' }).click();
  await expect(detail.getByText('Draft saved.')).toBeVisible();
  expect(calls.followUps).toEqual([{ to: ['jane@example.com', 'omar@example.com'], subject: 'PMV call recap', body: 'Hello all,\n\nThanks for joining.' }]);
  // No mailbox connected: sending is off and says what to connect; the mail app link works.
  await expect(detail.getByRole('button', { name: 'Send with approval' })).toBeDisabled();
  await expect(detail.getByText('Connect a mailbox under Inbox')).toBeVisible();
  await expect(detail.getByRole('link', { name: 'Open in mail app' })).toHaveAttribute('href', /^mailto:jane%40example\.com,omar%40example\.com\?subject=PMV%20call%20recap/);
});

test('with a connected mailbox the saved follow-up is sent through an approval', async ({ page }) => {
  const calls = await mockApi(page, {
    setup: { email_accounts: [{ id: 'emb_1', address: 'owner@example.com', provider: 'gmail' }, { id: 'emb_2', address: 'sales@example.com', provider: 'outlook' }] },
    items: [meetingFixture({ follow_up: { to: ['jane@example.com'], subject: 'PMV call recap', body: 'Thanks.', edited: true, send_task_id: null } })],
  });
  await page.goto('/#meetings?meeting=meeting_pmv');
  const detail = page.locator('.meetingDetail');
  await detail.getByLabel('Send from').selectOption('emb_2');
  await expect(detail.getByText('Sending saves a reply draft in sales@example.com and asks for approval')).toBeVisible();
  await detail.getByRole('button', { name: 'Send with approval' }).click();
  await expect(detail.getByText('Waiting for approval in Approvals. Once approved it is sent from owner@example.com')).toBeVisible();
  expect(calls.sends).toEqual([{ account_id: 'emb_2' }]);
  await detail.getByRole('button', { name: 'Open Approvals' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
});

test('a transcript file is imported, processed and opened; recordings explain how to set up transcription', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#meetings');
  await page.getByRole('button', { name: 'Import a meeting' }).first().click();
  const drawer = page.getByRole('dialog', { name: 'Import a meeting' });
  await drawer.getByRole('tab', { name: 'Recording' }).click();
  await expect(drawer.getByText('Recording transcription is not set up')).toBeVisible();
  await expect(drawer.getByText(/local Whisper server/)).toBeVisible();

  await drawer.getByRole('tab', { name: 'Transcript file' }).click();
  await drawer.getByLabel('Title').fill('Weekly sync');
  await drawer.locator('input[type="file"]').setInputFiles({ name: 'Weekly.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF') });
  await expect(drawer.getByText('Weekly.pdf is not a supported file.')).toBeVisible();
  await drawer.locator('input[type="file"]').setInputFiles({ name: 'weekly.vtt', mimeType: 'text/vtt', buffer: Buffer.from('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n') });
  await expect(page.getByText('Imported “Weekly sync”.')).toBeVisible();
  expect(calls.uploads).toEqual(['weekly.vtt']);
  const detail = page.locator('.meetingDetail');
  await expect(detail.getByRole('heading', { name: 'Weekly sync' })).toBeVisible();
  // Polling picks up the finished summary.
  await expect(detail.getByText('AI-generated · claude-sonnet-5-5')).toBeVisible({ timeout: 10_000 });
});

test('Zoom meetings with transcripts are listed and imported; already imported ones are marked', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#meetings');
  await page.getByRole('button', { name: 'Import a meeting' }).first().click();
  const drawer = page.getByRole('dialog', { name: 'Import a meeting' });
  await drawer.getByRole('tab', { name: 'Zoom' }).click();
  await expect(drawer.getByText('owner@example.com')).toBeVisible();
  await expect(drawer.locator('.externalMeetings li').nth(1).getByText('imported')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Teams' }).click();
  await expect(drawer.getByText('Microsoft Teams is not set up on this server')).toBeVisible();
  await drawer.getByRole('tab', { name: 'Zoom' }).click();
  await drawer.getByRole('button', { name: 'Import Noon UAE weekly' }).click();
  await expect(page.getByText('Imported “Noon UAE weekly”.')).toBeVisible();
  expect(calls.imports).toEqual([{ external_id: 'zoom-1', language: 'auto' }]);
});

test('the Zoom sign-in returns to the callback page and finishes connecting', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/meetings/connected?code=code-one&state=state-one');
  await expect(page.getByText('Zoom is connected as owner@example.com.')).toBeVisible();
  expect(calls.callbacks).toEqual([{ code: 'code-one', state: 'state-one' }]);
  await expect(page).toHaveURL(/\/#meetings$/);
});

test('viewers read meetings but are told who can import; failures say what failed and retry', async ({ page }) => {
  await mockApi(page, { setup: { can_create: false, can_manage_all: false }, listStatus: 500 });
  await page.goto('/#meetings');
  await expect(page.getByText('You can read meetings. Importing and editing them needs a member or owner role')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Import a meeting' })).toHaveCount(0);
  await expect(page.getByRole('alert').filter({ hasText: 'Meetings could not be loaded: Database unavailable' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.locator('.meetingsTable .memoryRow')).toHaveCount(1);
  await page.locator('.meetingsTable').getByRole('button', { name: 'Open PMV meeting' }).click();
  await expect(page.getByText('Only the person who imported this meeting or a workspace owner can change or delete it.')).toBeVisible();
  await expect(page.locator('.meetingDetail').getByRole('button', { name: 'Create a task for Send the tyre stock report' })).toHaveCount(0);
});

test('an empty workspace offers the first import, and a search with no match offers to clear it', async ({ page }) => {
  await mockApi(page, { items: [] });
  await page.goto('/#meetings');
  await expect(page.getByRole('heading', { name: 'No meetings yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Import your first meeting' }).click();
  await expect(page.getByRole('dialog', { name: 'Import a meeting' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test.describe('Arabic', () => {
  test('an Arabic meeting reads right to left with Arabic labels', async ({ page }) => {
    await useSavedLocale(page, 'ar');
    await mockApi(page, { items: [arabicMeeting] });
    await page.goto('/#meetings');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.getByRole('heading', { level: 1, name: 'المكالمات والاجتماعات' })).toBeVisible();
    await expect(page.getByRole('button', { name: /ملخص مكالمة: .*اجتماع خطة الإطارات/ })).toBeVisible();
    await page.locator('.meetingsTable .rowLink').first().click();
    const detail = page.locator('.meetingDetail');
    await expect(detail.getByRole('heading', { name: 'الملخص' })).toBeVisible();
    await expect(detail.locator('.meetingSummary')).toHaveAttribute('dir', 'rtl');
    await expect(detail.locator('.transcriptLines')).toHaveAttribute('dir', 'rtl');
    await expect(detail.locator('.transcriptLines li').first()).toContainText('صباح الخير');
    await expect(detail.getByRole('button', { name: /إنشاء مهمة لـ/ })).toBeVisible();
  });
});
