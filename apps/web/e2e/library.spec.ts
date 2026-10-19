import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Favorites, the knowledge base and reports against a stateful mocked API
// (docs/knowledge-base.md, docs/reports.md).
type Json = Record<string, unknown>;
const now = '2026-10-11T09:00:00Z';
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

function report(id: string, overrides: Json = {}): Json {
  return {
    id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', type: 'tyre', title: `Tyre cost ${id}`,
    period_start: '2026-09-01', period_end: '2026-09-30', summary: 'Costs rose on puncture repairs.',
    metrics: [{ label: 'Total cost', value: '12,400', unit: 'SAR', change_percent: 8.5 }],
    highlights: ['Puncture repairs doubled'], data: { columns: ['reason', 'cost'], rows: [['Puncture', 4200], ['Wear', 8200]] },
    body_markdown: '# Tyre cost', visibility: 'workspace', owner_user_id: null, created_by: 'user_local', created_by_kind: 'agent',
    source: { conversation_id: 'conv_1', turn_id: 't2', automation_run_id: null }, created_at: now, updated_at: now, can_manage: true, ...overrides,
  };
}

interface Options { reportsForbidden?: boolean; reportsFailOnce?: boolean; generateForbidden?: boolean; viewer?: boolean }

async function mockLibrary(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let reports = Array.from({ length: 25 }, (_, i) => report(`report_${String(i).padStart(2, '0')}`));
  let favorites: Json[] = [{ id: 'fav_1', item_type: 'report', item_id: 'report_00', label: 'Tyre cost report_00', created_at: now }];
  let collections: Json[] = [];
  const items: Record<string, Json[]> = {};
  let failures = options.reportsFailOnce ? 1 : 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() && request.headers()['content-type']?.includes('json') ? request.postDataJSON() : null;
    calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const q = url.searchParams;

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: now, updated_at: now, archived_at: null, archived_by: null });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.viewer ? 'viewer' : 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/conversations' && method === 'GET') return json([{ id: 'conv_1', title: 'Tyre costs', created_at: now, updated_at: now }]);
    if (path === '/api/v1/conversations/conv_1') {
      return json({ id: 'conv_1', title: 'Tyre costs', created_at: now, updated_at: now, turns: [
        { id: 't1', conversation_id: 'conv_1', role: 'user', content: 'How did tyre costs change?', status: 'complete', created_at: now },
        { id: 't2', conversation_id: 'conv_1', role: 'assistant', content: 'Tyre costs rose 8.5% in September.', status: 'complete', created_at: now },
      ], reports: [{ report_id: 'report_00', turn_id: 't2', title: 'Tyre cost, September', type: 'tyre', period_start: '2026-09-01', period_end: '2026-09-30', metrics: [{ label: 'Total cost', value: '12,400', unit: 'SAR', change_percent: 8.5 }], highlights: ['Puncture repairs doubled'] }] });
    }

    if (path === '/api/v1/favorites') {
      if (method === 'GET') {
        const type = q.get('item_type');
        const rows = favorites.filter((f) => !type || f.item_type === type);
        return json({ items: rows, total: rows.length, limit: 30, offset: 0 });
      }
      if (method === 'POST') {
        const fav = { id: `fav_${favorites.length + 2}`, item_type: (body as Json).item_type, item_id: (body as Json).item_id, label: 'Starred', created_at: now };
        favorites = [fav, ...favorites];
        return json(fav, 201);
      }
      if (method === 'DELETE') { favorites = favorites.filter((f) => !(f.item_type === q.get('item_type') && f.item_id === q.get('item_id'))); return route.fulfill({ status: 204 }); }
    }

    if (path === '/api/v1/reports' && method === 'GET') {
      if (options.reportsForbidden) return json(envelope('forbidden', 'Workspace permission denied'), 403);
      if (failures > 0) { failures -= 1; return json(envelope('service_unavailable', 'The database is unavailable'), 503); }
      const rows = reports.filter((r) => (!q.get('q') || String(r.title).toLowerCase().includes(q.get('q')!.toLowerCase())) && (!q.get('type') || r.type === q.get('type')));
      const offset = Number(q.get('offset') ?? 0), limit = Number(q.get('limit') ?? 20);
      return json({ items: rows.slice(offset, offset + limit).map((r) => ({ ...r, row_count: 2 })), total: rows.length, limit, offset, types: [{ type: 'tyre', count: reports.length }] });
    }
    if (path === '/api/v1/reports/generators') return json([{ type: 'task_summary', title: 'Task summary', description: '' }, { type: 'automation_summary', title: 'Automation summary', description: '' }]);
    if (path === '/api/v1/reports/generate') {
      if (options.generateForbidden) return json(envelope('forbidden', 'Workspace permission denied'), 403);
      const created = report('report_new', { type: 'task_summary', title: 'Task summary, last 7 days', created_by_kind: 'user', source: { conversation_id: null, turn_id: null, automation_run_id: null } });
      reports = [created, ...reports];
      return json(created, 201);
    }
    const one = /^\/api\/v1\/reports\/([\w-]+)$/.exec(path);
    if (one) {
      const found = reports.find((r) => r.id === one[1]);
      if (!found) return json(envelope('not_found', 'Report not found'), 404);
      if (method === 'GET') return json(found);
      if (method === 'PATCH') { Object.assign(found, body as Json); return json(found); }
      if (method === 'DELETE') { reports = reports.filter((r) => r.id !== one[1]); return route.fulfill({ status: 204 }); }
    }
    if (/^\/api\/v1\/reports\/[\w-]+\/export$/.test(path)) {
      return route.fulfill({ status: 200, contentType: 'text/csv', body: 'reason,cost\nPuncture,4200\n', headers: { 'content-disposition': 'attachment; filename="tyre-cost-2026-10-11.csv"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' } });
    }
    if (path === '/api/v1/automation/workflows' && method === 'POST') return json({ id: 'wf_report', ...(body as Json), tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', status: 'active', version: 1, created_at: now, updated_at: now }, 201);
    if (path === '/api/v1/automation/schedules' && method === 'POST') return json({ id: 'sched_1', ...(body as Json), next_run_at: now, created_at: now, updated_at: now }, 201);

    if (path === '/api/v1/knowledge/collections') {
      if (method === 'GET') return json(collections);
      const created = { id: `kbc_${collections.length + 1}`, name: (body as Json).name, description: (body as Json).description, visibility: (body as Json).visibility, created_by: 'user_local', created_at: now, updated_at: now, item_count: 0, can_manage: true };
      collections = [...collections, created];
      items[created.id as string] = [];
      return json(created, 201);
    }
    const notes = /^\/api\/v1\/knowledge\/collections\/([\w-]+)\/notes$/.exec(path);
    if (notes) {
      const note = { id: 'kbi_note', collection_id: notes[1], kind: 'note', source_id: 'mem_1', title: (body as Json).title, tags: (body as Json).tags, created_by: 'user_local', created_at: now, updated_at: now, index: { state: 'indexed', error: null, chunk_count: 1 }, content_type: 'text/markdown', size_bytes: null, available: true, content: (body as Json).content };
      items[notes[1]].push(note);
      return json(note, 201);
    }
    const listed = /^\/api\/v1\/knowledge\/collections\/([\w-]+)\/items$/.exec(path);
    if (listed) { const rows = items[listed[1]] ?? []; return json({ items: rows, total: rows.length, limit: 50, offset: 0, tags: [...new Set(rows.flatMap((r) => r.tags as string[]))] }); }
    if (path === '/api/v1/knowledge/ask') {
      return json({ status: 'answered', answer: 'Fit winter tyres from November to March [1].', model: 'claude', citations: [{ n: 1, item_id: 'kbi_note', collection_id: 'kbc_1', title: 'Winter policy', kind: 'note', provenance: 'memory', source_id: 'mem_1', chunk_index: 0, score: 0.91, snippet: 'Fit winter tyres from November to March.' }] });
    }
    if (path === '/api/v1/knowledge/search') return json({ query: q.get('q'), mode: 'hybrid', semantic: 'ok', hits: [] });
    if (path === '/api/v1/files' && method === 'GET') return json([]);
    if (path === '/api/v1/email/accounts') return json([{ id: 'acct_1', provider: 'gmail', provider_name: 'Gmail', address: 'shop@example.com', display_name: null, connected_by: 'user_local', visibility: 'workspace', status: 'connected', scopes: [], last_sync_at: null, last_sync_status: null, last_error: null, syncing: false, message_count: 0, unread_count: 0, created_at: now, can_manage: true }]);
    if (/^\/api\/v1\/reports\/[\w-]+\/share\/email$/.test(path)) {
      return json({ channel: 'email', status: 'awaiting_approval', approval: { id: 'approval_1', payload_hash: 'a'.repeat(64) }, detail: 'Waiting', draft_id: 'draft_1', message_id: null });
    }
    return route.abort();
  });
  return calls;
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test('favorites: filter by type, open a starred report, remove it, then the empty state', async ({ page }) => {
  await mockLibrary(page);
  await page.goto('/#favorites');
  await expect(page.getByRole('heading', { name: 'Favorites', level: 1 })).toBeVisible();
  await page.getByRole('group', { name: 'Filter favorites by type' }).getByRole('button', { name: 'Report' }).click();
  await expect(page.locator('.favoritesTable tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'Tyre cost report_00', exact: true }).click();
  await expect(page).toHaveURL(/#reports\/report_00$/);
  await expect(page.getByRole('dialog', { name: 'Tyre cost report_00' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Favorites' }).click();
  await page.getByRole('button', { name: 'Remove Tyre cost report_00 from favorites' }).click();
  await expect(page.getByText('Nothing starred yet')).toBeVisible();
  await noHorizontalOverflow(page);
});

test('reports: page, filter, view, download, rename, share and delete', async ({ page }) => {
  const calls = await mockLibrary(page);
  await page.goto('/#reports');
  await expect(page.locator('.reportRow')).toHaveCount(20);
  await page.getByRole('button', { name: 'Show more (20 of 25)' }).click();
  await expect(page.locator('.reportRow')).toHaveCount(25);
  await page.getByRole('searchbox', { name: 'Search', exact: true }).fill('report_03');
  await page.getByRole('search').getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.reportRow')).toHaveCount(1);
  await page.getByRole('button', { name: 'Tyre cost report_03', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Tyre cost report_03' })).toBeVisible();
  // The drawer is named by the report's title, which the rename below changes.
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('table')).toContainText('Puncture');
  await expect(drawer.locator('.reportChange')).toHaveText('+8.5%');
  const download = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'Download CSV' }).click();
  expect((await download).suggestedFilename()).toBe('tyre-cost-2026-10-11.csv');
  await drawer.getByRole('textbox', { name: 'Title' }).fill('September tyre cost');
  await drawer.getByRole('button', { name: 'Save title' }).click();
  await expect(drawer.getByText('Renamed.')).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'September tyre cost' })).toBeVisible();
  await drawer.getByRole('button', { name: 'Make private' }).click();
  await expect(drawer.getByText('Now private to you.')).toBeVisible();
  expect(calls.some((c) => c.method === 'PATCH' && (c.body as Json)?.visibility === 'private')).toBe(true);
  await drawer.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this report?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Deleted September tyre cost.')).toBeVisible();
  await noHorizontalOverflow(page);
});

test('reports: generate now and schedule as an automation', async ({ page }) => {
  const calls = await mockLibrary(page);
  await page.goto('/#reports');
  await page.getByRole('button', { name: 'Generate now' }).click();
  await expect(page.getByRole('dialog', { name: 'Task summary, last 7 days' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
  await page.getByRole('combobox', { name: 'Schedule' }).selectOption({ label: 'Every day at 09:00' });
  await page.getByRole('button', { name: 'Schedule it' }).click();
  await expect(page.getByText(/Scheduled "Scheduled report: Task summary"/)).toBeVisible();
  const workflow = calls.find((c) => c.path === '/api/v1/automation/workflows')?.body as { steps: { action: string; input: Json }[] };
  expect(workflow.steps[0]).toMatchObject({ action: 'report.generate', input: { type: 'task_summary', period_days: 7 } });
  expect((calls.find((c) => c.path === '/api/v1/automation/schedules')?.body as Json).cron).toBe('0 9 * * *');
});

test('reports: permission denied, a failed load with retry, and a refused generate explain what to do', async ({ page }) => {
  await mockLibrary(page, { reportsFailOnce: true, generateForbidden: true });
  await page.goto('/#reports');
  await expect(page.getByRole('alert')).toContainText("Couldn't load reports: The server could not reach its database");
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.reportRow').first()).toBeVisible();
  await page.getByRole('button', { name: 'Generate now' }).click();
  await expect(page.getByText('Your role cannot create reports. Members and owners can; ask a workspace owner.')).toBeVisible();

  const denied = await page.context().newPage();
  await mockLibrary(denied, { reportsForbidden: true });
  await denied.goto('/#reports');
  await expect(denied.getByRole('alert')).toContainText('Members with task access can read reports');
});

test('the Assistant shows a report card whose View Full Report opens the stored report', async ({ page }) => {
  await mockLibrary(page);
  await page.goto('/#assistant');
  const card = page.getByRole('region', { name: 'Report: Tyre cost, September' });
  await expect(card).toContainText('12,400 SAR');
  await expect(card).toContainText('+8.5%');
  await card.getByRole('button', { name: 'View Full Report' }).click();
  await expect(page).toHaveURL(/#reports\/report_00$/);
  await expect(page.getByRole('dialog', { name: 'Tyre cost report_00' })).toBeVisible();
});

test('knowledge base: create a collection, write a note, ask with citations', async ({ page }) => {
  const calls = await mockLibrary(page);
  await page.goto('/#knowledge');
  await expect(page.getByText('Start your knowledge base')).toBeVisible();
  await page.getByRole('textbox', { name: 'Name' }).fill('Tyre policies');
  await page.getByRole('radio', { name: 'Private to me' }).check();
  await page.getByRole('button', { name: 'Create collection' }).click();
  await expect(page.getByText('Created Tyre policies.')).toBeVisible();
  await expect(page.getByText(/Private: only you see this collection/)).toBeVisible();
  // Adding opens a dialog; an upload into a private collection says plainly that documents are workspace files.
  await page.locator('.kbCollectionActions').getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('dialog').getByText(/an uploaded document is a workspace file/)).toBeVisible();
  await page.getByRole('tab', { name: 'Write a note' }).click();
  await page.getByRole('textbox', { name: 'Title' }).fill('Winter policy');
  await page.getByRole('textbox', { name: 'Text' }).fill('Fit winter tyres from November to March.');
  await page.getByRole('textbox', { name: 'Tags' }).fill('Policy, winter');
  await page.getByRole('button', { name: 'Save note' }).click();
  await expect(page.getByText('Saved the note Winter policy.')).toBeVisible();
  await expect(page.locator('.libraryRow')).toContainText('Indexed');
  expect((calls.find((c) => c.path.endsWith('/notes'))?.body as Json).tags).toEqual(['policy', 'winter']);
  await page.getByRole('searchbox', { name: 'Question or search words' }).fill('When do we fit winter tyres?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.locator('.kbAnswerText')).toHaveText('Fit winter tyres from November to March [1].');
  await expect(page.locator('.kbCitations li')).toContainText('Winter policy');
  await expect(page.locator('.kbCitations li')).toContainText('Note');
  await noHorizontalOverflow(page);
});

test('Arabic: reports, knowledge base and favorites read right to left without overflow', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockLibrary(page);
  await page.goto('/#reports');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'التقارير', level: 2 })).toBeVisible();
  await expect(page.locator('.reportRow').first()).toContainText('تقرير الإطارات');
  await noHorizontalOverflow(page);
  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'قاعدة المعرفة' }).click();
  // The Knowledge Base entry opens on Memories; Collections is its second tab.
  const tabs = page.getByRole('navigation', { name: 'أقسام قاعدة المعرفة' });
  await expect(tabs.getByRole('button', { name: 'الذكريات' })).toHaveAttribute('aria-current', 'page');
  await tabs.getByRole('button', { name: 'المجموعات' }).click();
  await expect(page).toHaveURL(/#knowledge$/);
  await expect(page.getByText('ابدأ قاعدة معرفتك')).toBeVisible();
  await noHorizontalOverflow(page);
  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'المفضلة' }).click();
  await expect(page.getByRole('button', { name: /^إزالة\W*Tyre cost report_00\W*من المفضلة$/ })).toBeVisible();
  await noHorizontalOverflow(page);
});

test('reports: share by email asks for approval and nothing is sent yet', async ({ page }) => {
  const calls = await mockLibrary(page);
  await page.goto('/#reports/report_00');
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('tab', { name: 'Email' }).click();
  await drawer.getByRole('textbox', { name: /^To/ }).fill('boss@example.com, team@example.com');
  await drawer.getByRole('textbox', { name: 'Message' }).fill('September figures');
  await drawer.getByRole('button', { name: 'Ask to send' }).click();
  await expect(drawer.getByText(/Waiting for approval/)).toBeVisible();
  const body = calls.find((c) => c.path.endsWith('/share/email'))?.body as Json;
  expect(body).toMatchObject({ account_id: 'acct_1', to: ['boss@example.com', 'team@example.com'], note: 'September figures', include_table: true });
});
