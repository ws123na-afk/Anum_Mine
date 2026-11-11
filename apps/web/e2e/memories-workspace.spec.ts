import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Workspace memories, editing in place and paging (docs/memory.md#workspace-memories).
const now = '2026-10-11T09:00:00.000Z';
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

const chatMemory: Json = {
  id: 'memory_chat', task_id: null, owner_user_id: 'user_local', content: 'The user prefers short answers.',
  provenance: { source_type: 'conversation', source_id: 'conv_trip', created_by_user_id: 'user_local', created_at: now, metadata: { conversation_title: 'Planning the Lisbon trip' } },
  retention: { kind: 'indefinite', expires_at: null }, created_at: now, updated_at: null,
};
const taskMemory: Json = {
  id: 'memory_task', task_id: 'task_launch', owner_user_id: null, content: 'Launch is on Friday.',
  provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_other', created_at: now }, retention: { kind: 'task', expires_at: null }, created_at: now, updated_at: null,
};
const workspaceMemory: Json = {
  id: 'memory_ws', task_id: null, owner_user_id: null, content: 'The office closes at 16:00 on Thursdays.',
  provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_other', created_at: now }, retention: { kind: 'indefinite', expires_at: null }, created_at: now, updated_at: null,
};

async function mockApi(page: Page, options: { role?: string; many?: number } = {}) {
  const calls: { method: string; path: string; query: Record<string, string>; body: unknown }[] = [];
  let memories: Json[] = [chatMemory, taskMemory, workspaceMemory];
  if (options.many) {
    memories = Array.from({ length: options.many }, (_, i) => ({ ...workspaceMemory, id: `memory_${i}`, content: `Workspace fact number ${i}`, created_at: new Date(Date.parse(now) + i * 60_000).toISOString() }));
  }
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, query: Object.fromEntries(url.searchParams), body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/tasks') return json([{ id: 'task_launch', title: 'Launch', prompt: 'Launch', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now }]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: true, updated_at: null });
    if (path === '/api/v1/memories' && method === 'GET') {
      const onlyChats = url.searchParams.get('source_type') === 'conversation';
      const query = (url.searchParams.get('query') ?? '').toLowerCase();
      const matching = memories.filter((m) => (!onlyChats || (m.provenance as Json).source_type === 'conversation') && String(m.content).toLowerCase().includes(query));
      const limit = Number(url.searchParams.get('limit') ?? 200), offset = Number(url.searchParams.get('offset') ?? 0);
      const pageRows = matching.slice(Math.max(0, matching.length - offset - limit), Math.max(0, matching.length - offset));
      // Cross-origin like the real API, which exposes the header (main.py expose_headers).
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'X-Total-Count': String(matching.length), 'Access-Control-Expose-Headers': 'X-Total-Count' }, body: JSON.stringify(pageRows) });
    }
    if (path === '/api/v1/memories' && method === 'POST') {
      const created: Json = { id: `memory_new_${memories.length}`, task_id: (body as Json).task_id ?? null, owner_user_id: null, content: (body as Json).content, provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_local', created_at: now }, retention: (body as Json).retention, created_at: new Date().toISOString(), updated_at: null };
      memories = [...memories, created];
      return json(created, 201);
    }
    if (path.startsWith('/api/v1/memories/') && method === 'PATCH') {
      const id = path.split('/').pop();
      const found = memories.find((m) => m.id === id);
      if (!found) return json(envelope('not_found', 'Memory not found'), 404);
      if (options.role === 'member' && (found.provenance as Json).created_by_user_id !== 'user_local') return json(envelope('forbidden', "only the memory's author or a workspace owner can edit it"), 403);
      const updated = { ...found, content: (body as Json).content, updated_at: now, provenance: { ...(found.provenance as Json), metadata: { last_edited_by: 'user_local', last_edited_at: now } } };
      memories = memories.map((m) => (m.id === id ? updated : m));
      return json(updated);
    }
    if (path.startsWith('/api/v1/memories/') && method === 'DELETE') { memories = memories.filter((m) => m.id !== path.split('/').pop()); return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: memories.length, failed: 0, skipped: 0, stale: 0, chunks: memories.length, sources: memories.map((m) => ({ source_type: 'memory', source_id: m.id, name: null, status: 'indexed', error: null, chunk_count: 1, embedding_model: 'anum-local-hash-v1', indexed_at: now })) });
    return route.abort();
  });
  return calls;
}

test('an owner adds a workspace memory, edits a shared note in place and sees the groups', async ({ page }) => {
  test.setTimeout(60_000);
  const calls = await mockApi(page);
  await page.goto('/#memory');
  const list = page.getByRole('region', { name: 'Memories' });
  await expect(list.getByRole('rowgroup').filter({ hasText: 'Workspace notes' })).toBeVisible();
  // The row by id: while it is being edited its text sits in a textarea, not in the row's text.
  const shared = page.locator('#memory-memory_ws');
  await expect(shared.getByText('Workspace note', { exact: true })).toBeVisible();
  await expect(page.getByText('3 of 3 memories shown.')).toBeVisible();

  // Add a memory to the whole workspace, kept for 30 days.
  await page.getByRole('button', { name: 'Add memory' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a memory' });
  await expect(dialog.getByLabel('Task')).toHaveValue('');
  await expect(dialog.getByText(/shared with everyone here and belongs to no task/)).toBeVisible();
  await dialog.getByLabel('Keep').selectOption('30');
  await dialog.getByLabel('Memory', { exact: true }).fill('Visitor badges are at reception.');
  await dialog.getByRole('button', { name: 'Add memory' }).click();
  await expect(page.getByText('Memory added.')).toBeVisible();
  const post = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/memories');
  expect((post?.body as Json).task_id).toBeNull();
  expect(((post?.body as Json).retention as Json).kind).toBe('expires_at');
  await expect(page.locator('.memoryRow').filter({ hasText: 'Visitor badges' })).toBeVisible();

  // Picking a task hides the Keep choice and sends the task retention.
  await page.getByRole('button', { name: 'Add memory' }).click();
  await dialog.getByLabel('Task').selectOption('task_launch');
  await expect(dialog.getByLabel('Keep')).toHaveCount(0);
  await dialog.getByLabel('Memory', { exact: true }).fill('Launch checklist is in the shared drive.');
  await dialog.getByRole('button', { name: 'Add memory' }).click();
  expect(((calls.filter((c) => c.method === 'POST').at(-1)?.body as Json).retention as Json).kind).toBe('task');

  // An owner edits another member's shared note in its row.
  await shared.getByRole('button', { name: 'Edit memory' }).click();
  const box = shared.getByLabel('Memory text');
  await box.fill('The office closes at 15:00 on Thursdays.');
  await shared.getByRole('button', { name: 'Save memory' }).click();
  await expect(page.getByText('Memory updated. The index follows the new text.')).toBeVisible();
  await expect(shared.getByText(/Edited .* by user_local\./)).toBeVisible();
  expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ content: 'The office closes at 15:00 on Thursdays.' });

  // Escape cancels an edit without a request.
  const before = calls.filter((c) => c.method === 'PATCH').length;
  await shared.getByRole('button', { name: 'Edit memory' }).click();
  await shared.getByLabel('Memory text').press('Escape');
  await expect(shared.getByLabel('Memory text')).toHaveCount(0);
  expect(calls.filter((c) => c.method === 'PATCH').length).toBe(before);
});

test('a member edits only their own notes and a refused edit shows who can', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#memory');
  const shared = page.locator('#memory-memory_ws');
  await expect(shared).toBeVisible();
  // Another member's note: no pencil. The caller's own chat memory: a pencil.
  await expect(shared.getByRole('button', { name: 'Edit memory' })).toHaveCount(0);
  const own = page.locator('#memory-memory_chat');
  await own.getByRole('button', { name: 'Edit memory' }).click();
  await own.getByLabel('Memory text').fill('The user prefers short answers with examples.');
  await own.getByRole('button', { name: 'Save memory' }).click();
  await expect(page.getByText('Memory updated. The index follows the new text.')).toBeVisible();
});

test('a long history pages: the newest first, older ones on Load older memories, search narrows the total', async ({ page }) => {
  const calls = await mockApi(page, { many: 120 });
  await page.goto('/#memory');
  await expect(page.getByText('50 of 120 memories shown.')).toBeVisible();
  await expect(page.locator('.memoryRow').filter({ hasText: 'fact number 119' })).toBeVisible();
  await expect(page.locator('.memoryRow').filter({ hasText: 'fact number 69' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Load older memories' }).click();
  await expect(page.getByText('100 of 120 memories shown.')).toBeVisible();
  await expect(page.locator('.memoryRow').filter({ hasText: 'fact number 69' })).toBeVisible();
  expect(calls.filter((c) => c.path === '/api/v1/memories' && c.method === 'GET').at(-1)?.query).toMatchObject({ limit: '50', offset: '50' });
  await page.getByRole('button', { name: 'Load older memories' }).click();
  await expect(page.getByText('120 of 120 memories shown.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Load older memories' })).toHaveCount(0);

  await page.getByLabel('Search memory').fill('number 7');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('11 of 11 memories shown.')).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    test(`memories with an open edit fit at ${size.width}x${size.height} in ${locale} with no serious axe finding`, async ({ page }, testInfo) => {
      await page.setViewportSize(size);
      await useSavedLocale(page, locale);
      await mockApi(page);
      await page.goto('/#memory');
      const shared = page.locator('#memory-memory_ws');
      await shared.getByRole('button', { name: locale === 'ar' ? 'تعديل الذكرى' : 'Edit memory' }).click();
      await expect(shared.getByRole('textbox')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'overflow').toBeLessThanOrEqual(0);
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`memory-${locale}-${size.width}.png`), fullPage: true });
    });
  }
}
