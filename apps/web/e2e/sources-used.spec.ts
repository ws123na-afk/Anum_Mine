import { expect, test, type Page } from '@playwright/test';

// "Sources used" on a run comes from the run's `retrieval` step (ids and scores only).
const now = '2026-10-06T09:00:00.000Z';

async function mockRun(page: Page, retrievalStep: Record<string, unknown> | null) {
  let created = false;
  const completedTask = { id: 'task_rag', title: 'Web task', prompt: 'Summarise the launch notes', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
  await page.route('http://localhost:8000/api/v1/events/stream', (route) => route.fulfill({ status: 204 }));
  await page.route('http://localhost:8000/api/v1/approvals', (route) => route.fulfill({ contentType: 'application/json', body: '[]' }));
  await page.route('http://localhost:8000/api/v1/tasks', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(created ? [completedTask] : []) });
      return;
    }
    created = true;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...completedTask, status: 'created' }) });
  });
  const steps = [
    ...(retrievalStep ? [retrievalStep] : []),
    { id: 'step_model', type: 'model_call', summary: 'Launch notes summarised.', created_at: now, metadata: {} },
  ];
  await page.route('http://localhost:8000/api/v1/tasks/task_rag/run', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ task: completedTask, run: { id: 'run_rag', task_id: 'task_rag', status: 'completed', result: 'Done', steps }, approval: null }),
    }),
  );
}

async function runTask(page: Page) {
  await page.goto('/#tasks');
  await page.getByLabel('Task instructions').fill('Summarise the launch notes');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('Launch notes summarised.')).toBeVisible();
}

test('a run lists the memories and files its prompt used', async ({ page }) => {
  await mockRun(page, {
    id: 'step_retrieval',
    type: 'retrieval',
    summary: 'Used 2 passages from 2 workspace sources as labeled, untrusted context.',
    created_at: now,
    metadata: {
      status: 'ok',
      embedding_model: 'anum-local-hash-v1',
      truncated: false,
      max_chars: 6000,
      sources: [
        { chunk_id: 'chunk_1', source_type: 'memory', source_id: 'memory_launch', chunk_index: 0, score: 0.72, truncated: false },
        { chunk_id: 'chunk_2', source_type: 'file', source_id: 'file_notes', chunk_index: 1, score: 0.41, truncated: true },
      ],
    },
  });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources).toBeVisible();
  await expect(sources.getByText('memory_launch')).toBeVisible();
  await expect(sources.getByText('file_notes')).toBeVisible();
  await expect(sources.getByText('passage 2, 41% match, shortened')).toBeVisible();
});

test('a run whose retrieval found nothing says so', async ({ page }) => {
  await mockRun(page, {
    id: 'step_retrieval', type: 'retrieval', summary: 'No indexed workspace memory or file matched this task.', created_at: now,
    metadata: { status: 'no_results', embedding_model: 'anum-local-hash-v1', truncated: false, max_chars: 6000, sources: [] },
  });
  await runTask(page);
  const sources = page.getByRole('region', { name: 'Sources used' });
  await expect(sources.getByText('No indexed memory or file matched this task.')).toBeVisible();
});

test('a run without a retrieval step shows no sources panel', async ({ page }) => {
  await mockRun(page, null);
  await runTask(page);
  await expect(page.getByRole('region', { name: 'Sources used' })).toHaveCount(0);
});
