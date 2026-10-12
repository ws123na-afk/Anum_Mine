import { expect, test, type Page, type Route } from '@playwright/test';

// "Sources used" on a run comes from the run's `retrieval` step (ids and scores only); the names
// come from one bounded lookup, GET /api/v1/retrieval/sources (docs/memory.md#at-run-time).
const now = '2026-10-06T09:00:00.000Z';
type Json = Record<string, unknown>;

const memoryNote = {
  id: 'memory_launch', task_id: 'task_rag', content: 'Launch notes: ship on Friday after the signed tag.',
  provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_local', created_at: now },
  retention: { kind: 'task', expires_at: null }, created_at: now,
};
const otherNote = { ...memoryNote, id: 'memory_other', content: 'Unrelated note about the office.' };
const notesFile = {
  id: 'file_notes', name: 'launch-notes.md', content_type: 'text/markdown', size_bytes: 2048, sha256: 'a'.repeat(64), created_by: 'user_local', created_at: now,
  kind: 'markdown', previewable: true, can_manage: true, index: { status: 'indexed', error: null, chunk_count: 2, embedding_model: 'anum-local-hash-v1', indexed_at: now },
};
const names: Record<string, Json> = {
  memory_launch: { source_type: 'memory', source_id: 'memory_launch', found: true, title: 'Launch notes: ship on Friday after the signed tag.', kind: 'task_note', task_id: 'task_rag', size_bytes: null },
  file_notes: { source_type: 'file', source_id: 'file_notes', found: true, title: 'launch-notes.md', kind: 'text/markdown', task_id: null, size_bytes: 2048 },
  memory_deleted: { source_type: 'memory', source_id: 'memory_deleted', found: false, title: null, kind: null, task_id: null, size_bytes: null },
  file_long: { source_type: 'file', source_id: 'file_long', found: true, title: 'quarterly-launch-readiness-review-with-every-region-and-every-owner-signed-off-final-v12.md', kind: 'application/pdf', task_id: null, size_bytes: 4096 },
};

const okStep = (sources: Json[]) => ({
  id: 'step_retrieval', type: 'retrieval', summary: `Used ${sources.length} passages as labeled, untrusted context.`, created_at: now,
  metadata: { status: 'ok', embedding_model: 'anum-local-hash-v1', truncated: false, max_chars: 6000, sources },
});
const memoryPassage = { chunk_id: 'chunk_1', source_type: 'memory', source_id: 'memory_launch', chunk_index: 0, score: 0.72, truncated: false };
const filePassage = { chunk_id: 'chunk_2', source_type: 'file', source_id: 'file_notes', chunk_index: 1, score: 0.41, truncated: true };
const deletedPassage = { chunk_id: 'chunk_3', source_type: 'memory', source_id: 'memory_deleted', chunk_index: 0, score: 0.33, truncated: false };

interface Options {
  /** How GET /api/v1/retrieval/sources answers: names (default), a status code, or held until released. */
  lookup?: 'ok' | 403 | 500 | 'hold';
  /** GET /api/v1/files/{id} answers 404. */
  fileGone?: boolean;
}

async function mockRun(page: Page, retrievalStep: Json | null, options: Options = {}) {
  let created = false;
  const lookups: string[] = [];
  let release: (() => void) | null = null;
  let lookup = options.lookup ?? 'ok';
  const completedTask = { id: 'task_rag', title: 'Web task', prompt: 'Summarise the launch notes', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
  const steps = [
    ...(retrievalStep ? [retrievalStep] : []),
    { id: 'step_model', type: 'model_call', summary: 'Launch notes summarised.', created_at: now, metadata: {} },
  ];
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/tasks') {
      if (request.method() === 'GET') return json(created ? [completedTask] : []);
      created = true;
      return json({ ...completedTask, status: 'created' });
    }
    if (path === '/api/v1/tasks/task_rag/latest-run') return json({ detail: 'Agent run not found' }, 404);
    if (path === '/api/v1/tasks/task_rag/run') return json({ task: completedTask, run: { id: 'run_rag', task_id: 'task_rag', status: 'completed', result: 'Done', steps }, approval: null });
    if (path === '/api/v1/retrieval/sources') {
      lookups.push(url.search);
      if (lookup === 'hold') await new Promise<void>((resolve) => { release = resolve; });
      if (lookup === 403) return json({ error: { code: 'forbidden', message: 'Permission denied: memory:read', correlation_id: 'corr', details: [] } }, 403);
      if (lookup === 500) return json({ error: { code: 'internal_error', message: 'Retrieval store unavailable', correlation_id: 'corr', details: [] } }, 500);
      const ids = [...url.searchParams.getAll('memory_id'), ...url.searchParams.getAll('file_id')];
      return json({ sources: ids.map((id) => names[id] ?? { source_type: id.startsWith('file') ? 'file' : 'memory', source_id: id, found: false, title: null, kind: null, task_id: null, size_bytes: null }) });
    }
    if (path === '/api/v1/memories') return json([otherNote, memoryNote]);
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: 2, failed: 0, skipped: 0, stale: 0, chunks: 2, sources: [] });
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: false });
    if (path === '/api/v1/files') return json([notesFile]);
    if (path === '/api/v1/files/file_notes') return options.fileGone ? json({ detail: 'File not found' }, 404) : json(notesFile);
    if (path === '/api/v1/files/file_notes/preview') return json({ kind: 'markdown', text: '# Launch notes\nShip on Friday.', truncated: false, size_bytes: 2048 });
    return route.abort();
  });
  return {
    lookups,
    release: () => { const go = release; release = null; go?.(); },
    answer: (next: Options['lookup']) => { lookup = next ?? 'ok'; },
  };
}

async function runTask(page: Page) {
  await page.goto('/#tasks');
  await page.getByLabel('Task instructions').fill('Summarise the launch notes');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('Launch notes summarised.')).toBeVisible();
}

test('a run lists the memories and files its prompt used, by name and kind, from one lookup', async ({ page }) => {
  const api = await mockRun(page, okStep([memoryPassage, filePassage, { ...memoryPassage, chunk_id: 'chunk_4', chunk_index: 2, score: 0.5 }]));
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources).toBeVisible();
  const memory = sources.getByRole('button', { name: /^Open memory Launch notes: ship on Friday/ });
  await expect(memory).toBeVisible();
  await expect(memory.getByText('Task memory')).toBeVisible();
  await expect(memory.getByText('memory_launch')).toBeVisible();
  const file = sources.getByRole('button', { name: /^Open file launch-notes\.md/ });
  await expect(file.getByText('Markdown file')).toBeVisible();
  await expect(file.getByText('file_notes')).toBeVisible();
  await expect(sources.getByText('passage 2, 41% match, shortened')).toBeVisible();
  // Every source of the run is named by a single request, never one per source.
  expect(api.lookups).toEqual(['?memory_id=memory_launch&file_id=file_notes']);
});

test('opening a memory shows it in the Memory view, scrolled to and marked', async ({ page }) => {
  await mockRun(page, okStep([memoryPassage, filePassage]));
  await runTask(page);
  await page.getByRole('region', { name: 'Sources used' }).getByRole('button', { name: /^Open memory Launch notes/ }).click();
  await expect(page).toHaveURL(/#memory$/);
  const row = page.locator('.memoryRow').filter({ hasText: 'Launch notes: ship on Friday' });
  await expect(row.getByText('Used by the run you came from')).toBeVisible();
  await expect(row).toBeFocused();
  await expect(row).toBeInViewport();
  await expect(row).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('.memoryRow').filter({ hasText: 'Unrelated note' }).getByText('Used by the run you came from')).toHaveCount(0);
});

test('opening a file shows its detail in the Files view', async ({ page }) => {
  await mockRun(page, okStep([memoryPassage, filePassage]));
  await runTask(page);
  await page.getByRole('region', { name: 'Sources used' }).getByRole('button', { name: /^Open file launch-notes\.md/ }).click();
  await expect(page).toHaveURL(/#files$/);
  const detail = page.getByRole('region', { name: 'launch-notes.md' });
  await expect(detail.getByText('Markdown (text/markdown)')).toBeVisible();
  await expect(detail.getByRole('region', { name: 'Preview' }).getByRole('heading', { name: 'Launch notes' })).toBeVisible();
  await expect(detail.getByText('2.0 KB')).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Download' })).toBeVisible();
  await expect(detail.getByRole('heading', { name: 'launch-notes.md' })).toBeFocused();
  await detail.getByRole('button', { name: 'Close file detail' }).click();
  await expect(detail).toHaveCount(0);
});

test('a file deleted since the lookup says so in its detail', async ({ page }) => {
  await mockRun(page, okStep([filePassage]), { fileGone: true });
  await runTask(page);
  await page.getByRole('region', { name: 'Sources used' }).getByRole('button', { name: /^Open file launch-notes\.md/ }).click();
  await expect(page.getByText(/This file no longer exists in this workspace/)).toBeVisible();
});

test('a source that no longer exists shows its id and is not a link', async ({ page }) => {
  await mockRun(page, okStep([memoryPassage, deletedPassage]));
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources.getByRole('button', { name: /^Open memory Launch notes/ })).toBeVisible();
  await expect(sources.getByText('memory_deleted')).toBeVisible();
  await expect(sources.getByText('Memory no longer available')).toBeVisible();
  await expect(sources.getByRole('button', { name: /memory_deleted/ })).toHaveCount(0);
});

test('while the names load the ids show, then the names replace them', async ({ page }) => {
  const api = await mockRun(page, okStep([memoryPassage]), { lookup: 'hold' });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources.getByRole('status').getByText('Looking up the names of these sources...')).toBeVisible();
  await expect(sources.getByText('memory_launch')).toBeVisible();
  await expect(sources.getByRole('button', { name: /^Open memory/ })).toHaveCount(0);
  api.answer('ok');
  api.release();
  await expect(sources.getByRole('button', { name: /^Open memory Launch notes/ })).toBeVisible();
  await expect(sources.getByText('Looking up the names of these sources...')).toHaveCount(0);
});

test('a failed lookup says what failed and retries', async ({ page }) => {
  const api = await mockRun(page, okStep([memoryPassage, filePassage]), { lookup: 500 });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  const alert = sources.getByRole('alert');
  await expect(alert).toContainText('Could not load the names of these sources: Retrieval store unavailable');
  await expect(sources.getByText('memory_launch')).toBeVisible();
  api.answer('ok');
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(sources.getByRole('button', { name: /^Open file launch-notes\.md/ })).toBeVisible();
  await expect(sources.getByRole('alert')).toHaveCount(0);
});

test('without memory read permission the ids show with who can see the names', async ({ page }) => {
  await mockRun(page, okStep([memoryPassage]), { lookup: 403 });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources.getByText(/Names are hidden: seeing workspace memory and files needs a viewer, member or owner role/)).toBeVisible();
  await expect(sources.getByText('memory_launch')).toBeVisible();
  await expect(sources.getByRole('button', { name: /^Open memory/ })).toHaveCount(0);
  await expect(sources.getByRole('button', { name: 'Retry' })).toHaveCount(0);
});

test('a run whose retrieval found nothing says so without a lookup', async ({ page }) => {
  const api = await mockRun(page, {
    id: 'step_retrieval', type: 'retrieval', summary: 'No indexed workspace memory or file matched this task.', created_at: now,
    metadata: { status: 'no_results', embedding_model: 'anum-local-hash-v1', truncated: false, max_chars: 6000, sources: [] },
  });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources.getByText('No indexed memory or file matched this task.')).toBeVisible();
  expect(api.lookups).toEqual([]);
});

test('a run without a retrieval step shows no sources panel', async ({ page }) => {
  await mockRun(page, null);
  await runTask(page);
  await expect(page.getByRole('region', { name: 'Sources used' })).toHaveCount(0);
});

// The responsive gate's sizes, plus the Tauri desktop window (apps/desktop/src-tauri/tauri.conf.json,
// `frontendDist: ../../web/dist`), which opens at 1440x900 and shrinks to 960x640.
for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 960, height: 640 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test.describe(`at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });

    test('the sources panel and the views it opens fit without horizontal overflow', async ({ page }) => {
      await mockRun(page, okStep([memoryPassage, { ...filePassage, source_id: 'file_long' }, deletedPassage]));
      await runTask(page);
      const sources = page.getByRole('region', { name: 'Sources used' });
      await sources.scrollIntoViewIfNeeded();
      const file = sources.getByRole('button', { name: /^Open file quarterly-launch-readiness/ });
      await expect(file).toBeVisible();
      const box = (await sources.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(size.width);
      const row = (await file.boundingBox())!;
      expect(row.x + row.width).toBeLessThanOrEqual(box.x + box.width);
      const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(await overflow()).toBeLessThanOrEqual(0);

      await sources.getByRole('button', { name: /^Open memory Launch notes/ }).click();
      const marked = page.locator('.memoryRow').filter({ hasText: 'Launch notes: ship on Friday' });
      await expect(marked).toBeInViewport();
      expect(await overflow()).toBeLessThanOrEqual(0);
    });
  });
}
