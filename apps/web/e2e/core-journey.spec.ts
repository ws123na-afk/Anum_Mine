import { expect, test, type Page } from '@playwright/test';

const now = '2026-08-29T09:00:00.000Z';

async function mockApi(page: Page) {
  let created = false;
  const completedTask = { id: 'task_e2e', title: 'Web task', prompt: 'Review the release', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
  await page.route('http://localhost:8000/api/v1/events/stream', async (route) => {
    await route.fulfill({ status: 204 });
  });
  await page.route('http://localhost:8000/api/v1/tasks', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(created ? [completedTask] : []) });
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
  await page.route('http://localhost:8000/api/v1/approvals', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: '[]' });
  });
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

test('primary task journey reaches a completed real API result', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
  const instructions = page.getByLabel('Task instructions');
  await instructions.fill('Review the release');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('Workspace state is current.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Web task' })).toBeVisible();
  await expect(page.getByText('Release reviewed.')).toBeVisible();
});

test('navigation exposes the operational workbench and approvals', async ({ page }) => {
  await page.getByRole('button', { name: 'Workbench' }).click();
  await expect(page.getByRole('heading', { name: 'Automation workbench' })).toBeVisible();
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
