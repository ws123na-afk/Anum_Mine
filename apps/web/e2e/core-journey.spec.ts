import { expect, test, type Page } from '@playwright/test';
import { approvalPagesResponse } from './support/approvalPages';

const now = '2026-08-29T09:00:00.000Z';

async function mockApi(page: Page) {
  let created = false;
  const completedTask = { id: 'task_e2e', title: 'Web task', prompt: 'Review the release', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
  await page.route('http://localhost:8000/api/v1/events/stream', async (route) => {
    await route.fulfill({ status: 204 });
  });
  // The Tasks view lists with filters in the query string (GET /api/v1/tasks?limit=50&status=...).
  await page.route(/^http:\/\/localhost:8000\/api\/v1\/tasks(\?.*)?$/, async (route) => {
    if (route.request().method() === 'GET') {
      const statuses = new URL(route.request().url()).searchParams.getAll('status');
      const rows = created && (!statuses.length || statuses.includes('completed')) ? [completedTask] : [];
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(rows) });
      return;
    }
    created = true;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'task_e2e', title: 'Web task', prompt: 'Review the release', status: 'created',
        tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now,
      }),
    });
  });
  // The plain list (`?status=pending&limit=200` from the shell), the page and the summary.
  await page.route(/^http:\/\/localhost:8000\/api\/v1\/approvals(\/page|\/summary)?(\?.*)?$/, async (route) => {
    const paged = approvalPagesResponse(new URL(route.request().url()), []);
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(paged ?? []) });
  });
  // The detail panel refetches the selected task's latest run; the run response is the newest here.
  await page.route('http://localhost:8000/api/v1/tasks/task_e2e/latest-run', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Agent run not found"}' }));
  await page.route('http://localhost:8000/api/v1/tasks/task_e2e/run', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        task: completedTask,
        run: { id: 'run_e2e', task_id: 'task_e2e', status: 'completed', result: 'Done', steps: [{ id: 'step_e2e', type: 'model_call', summary: 'Release reviewed.', created_at: now }] },
        approval: null,
      }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.goto('/#tasks');
});

test('primary task journey reaches a completed real API result', async ({ page, isMobile }) => {
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
  const instructions = page.getByLabel('Task instructions');
  await instructions.fill('Review the release');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByRole('heading', { name: 'Web task' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Answer' }).getByText('Done')).toBeVisible();
  // The run's steps are under the Runs tab of the task detail.
  await page.getByRole('tab', { name: /^Runs/ }).click();
  await expect(page.getByText('Release reviewed.')).toBeVisible();
  // The stream mock answers 204 without a body, so the badge reports the stream as offline.
  await expect(page.getByText('Updates offline')).toBeVisible();
  // On a narrow screen the detail replaces the list; Back shows the composer and its notice.
  if (isMobile) await page.getByRole('button', { name: 'Back to the list' }).click();
  await expect(page.getByText('Workspace state is current.')).toBeVisible();
});

test('task history filters by status and search', async ({ page, isMobile }) => {
  await page.getByLabel('Task instructions').fill('Review the release');
  await page.getByRole('button', { name: 'Run task' }).click();
  if (isMobile) await page.getByRole('button', { name: 'Back to the list' }).click();
  await expect(page.getByText('1 of 1 tasks')).toBeVisible();
  await page.getByLabel('Status').selectOption('failed');
  await expect(page.getByRole('heading', { name: 'No matching tasks' })).toBeVisible();
  await page.getByLabel('Status').selectOption('all');
  await page.getByLabel('Search tasks').fill('release');
  await expect(page.getByText('1 of 1 tasks')).toBeVisible();
});

test('navigation exposes automations and approvals', async ({ page }) => {
  await page.getByRole('button', { name: 'Automations' }).click();
  await expect(page.getByRole('heading', { name: 'Automations', level: 2, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Approvals' }).click();
  await expect(page.getByRole('heading', { name: 'Queue clear' })).toBeVisible();
});

test('interactive controls have accessible names and do not overflow', async ({ page }) => {
  const unnamedButtons = await page.locator('button').evaluateAll((buttons) =>
    buttons.filter((button) => !(button.textContent?.trim() || button.getAttribute('aria-label') || button.getAttribute('title'))).length,
  );
  expect(unnamedButtons).toBe(0);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
