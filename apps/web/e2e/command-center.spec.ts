import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The ANUM Command Center, the web home (docs/design/anum360.md#web-command-center): the shell
// (sidebar with workspaces, global search, notifications, user menu) and the command flow against
// the mocked workspace API (e2e/support/workspaceApi.ts).

const VIEWPORTS = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function serious(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 5).map((n) => n.target.join(' ')).join(' | ')}`);
}

async function openHome(page: Page) {
  await mockWorkspaceApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: 'ANUM Command Center' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Current tasks' })).toBeVisible();
}

test('the home is the Command Center with real workspace data in every panel', async ({ page }) => {
  await openHome(page);
  await expect(page.getByRole('main', { name: 'ANUM Command Center' })).toBeVisible();
  // Current tasks: the open task from the API; Recent commands: both tasks.
  const current = page.getByRole('region', { name: /Current tasks/ });
  await expect(current.getByRole('button', { name: /Review the release/ })).toBeVisible();
  const recent = page.getByRole('region', { name: 'Recent commands' });
  await expect(recent.getByRole('button', { name: /Summarize the customer feedback/ })).toBeVisible();
  // Results preview lists the workspace's documents.
  await expect(page.getByRole('region', { name: 'Results preview' }).getByRole('button', { name: /release-notes\.md/ })).toBeVisible();
  // Suggestions come from the pending approval and the unread mail.
  const suggestions = page.getByRole('region', { name: 'Suggestions for you' });
  await expect(suggestions.getByRole('button', { name: /1 approval is waiting for you/ })).toBeVisible();
  await expect(suggestions.getByRole('button', { name: /1 unread email/ })).toBeVisible();
  // The tracker follows the newest task (waiting for approval).
  await expect(page.getByRole('region', { name: 'Waiting for your approval' }).getByText('Waiting for your approval of a risky step')).toBeVisible();
  await suggestions.getByRole('button', { name: /1 approval is waiting/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
});

test('a typed command creates and runs a task, then the tracker shows every step done', async ({ page }) => {
  await openHome(page);
  const done = { id: 'task_cmd', title: 'Compare tyre prices', prompt: 'Compare tyre prices', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
  let created: unknown = null;
  await page.route('http://localhost:8000/api/v1/tasks', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    created = route.request().postDataJSON();
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...done, status: 'created' }) });
  });
  await page.route('http://localhost:8000/api/v1/tasks/task_cmd/run', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
    task: done, approval: null,
    run: { id: 'run_cmd', task_id: 'task_cmd', status: 'completed', result: 'Supplier A is cheapest.', steps: [{ id: 's1', type: 'model_call', summary: 'Planned.', created_at: done.created_at }, { id: 's2', type: 'final', summary: 'Answered.', created_at: done.created_at }] },
  }) }));
  await page.getByLabel('Your command').fill('Compare tyre prices');
  await page.getByRole('button', { name: 'Send the command' }).click();
  const tracker = page.getByRole('region', { name: 'Last command' });
  await expect(tracker.getByText('Supplier A is cheapest.')).toBeVisible();
  await expect(tracker.getByText('Done')).toHaveCount(5);
  expect(created).toEqual({ title: 'Compare tyre prices', prompt: 'Compare tyre prices' });
});

test('a shortcut prefills the composer and a tile opens its view', async ({ page }) => {
  await openHome(page);
  await page.getByRole('button', { name: 'Prepare a report' }).click();
  await expect(page.getByLabel('Your command')).toHaveValue('Prepare a report on ');
  await expect(page.getByLabel('Your command')).toBeFocused();
  await page.getByRole('region', { name: 'What ANUM can do' }).getByRole('button', { name: 'Tasks' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Tasks' })).toBeVisible();
});

test('Ctrl+K searches views, tasks, documents and emails', async ({ page }) => {
  await openHome(page);
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', { name: 'Search ANUM' });
  await expect(palette).toBeVisible();
  await palette.getByRole('combobox').fill('release');
  await expect(palette.getByRole('option', { name: /release-notes\.md/ })).toBeVisible();
  await expect(palette.getByRole('option', { name: /Review the release/ })).toBeVisible();
  await palette.getByRole('combobox').fill('knowl');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Knowledge Base' })).toBeVisible();
});

test('notifications list the pending approval; the user menu opens Settings sections', async ({ page }) => {
  await openHome(page);
  await page.getByRole('button', { name: 'Notifications, 1 waiting' }).click();
  await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /external\.action/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
  await page.getByRole('button', { name: /Account menu/ }).click();
  await page.getByRole('dialog', { name: 'Your account' }).getByRole('button', { name: 'Language' }).click();
  await expect(page.locator('.languageChoices')).toBeVisible();
});

test('the sidebar lists the workspaces from the API and offers Add workspace', async ({ page, isMobile }) => {
  test.skip(isMobile, 'on phones the workspace list lives in the top-bar switcher');
  await openHome(page);
  const nav = page.getByRole('navigation', { name: 'Primary' });
  await expect(nav.getByRole('list', { name: 'Workspaces' }).getByRole('button', { name: /Foundation|workspace_foundation/ })).toHaveAttribute('aria-current', 'true');
  await nav.getByRole('button', { name: 'Add workspace' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a workspace' });
  await expect(dialog.getByLabel('Name')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

for (const locale of ['en', 'ar'] as const) {
  test(`${locale}: no serious axe violations, no horizontal overflow at the five viewports`, async ({ page }) => {
    await useSavedLocale(page, locale);
    await mockWorkspaceApi(page);
    for (const size of VIEWPORTS) {
      await page.setViewportSize(size);
      await page.goto('/#home');
      await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
      await expect(page.locator('.commandPanel .listRow').first()).toBeVisible();
      expect(await noHorizontalOverflow(page), `${size.width}x${size.height}`).toBe(true);
    }
    expect(await serious(page)).toEqual([]);
  });
}
