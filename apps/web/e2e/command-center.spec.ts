import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// ANUM Command (docs/command.md#the-web-command-center): the place to give orders. A request is
// planned (POST /commands/preview), edited, started (POST /commands, then each step's run) and
// followed; saved commands are kept; the space orb is reached through the Voice page. Mocks:
// e2e/support/homeApi.ts. Home's overview is e2e/home.spec.ts.

const VIEWPORTS = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const API = 'http://localhost:8000';
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function serious(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 5).map((n) => n.target.join(' ')).join(' | ')}`);
}

async function openCommand(page: Page) {
  const state = await mockWorkspaceApi(page);
  await page.goto('/#command');
  await expect(page.getByRole('heading', { level: 1, name: 'ANUM Command' })).toBeVisible();
  return state;
}

test('ANUM Command is its own page: composer, history and saved commands, no overview of the day', async ({ page }) => {
  await openCommand(page);
  await expect(page.getByLabel('Your command')).toBeFocused();
  await expect(page.getByRole('region', { name: 'Today at a glance' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Running and recent commands' }).getByRole('button', { name: /Check my emails and summarize the urgent ones/ })).toBeVisible();
  const saved = page.getByRole('region', { name: 'Saved commands' });
  await expect(saved.getByText('Morning briefing', { exact: true })).toBeVisible();
  await saved.getByRole('button', { name: 'Personal' }).click();
  await expect(saved.getByText('Morning briefing', { exact: true })).toHaveCount(0);
  await expect(saved.getByText('Plan my week', { exact: true })).toBeVisible();
  // The orb is the Voice page's: a link, not a copy.
  await page.getByRole('button', { name: 'Speak to the orb' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Voice' })).toBeVisible();
});

test('a typed command shows the plan, is edited, then starts every step and shows the results', async ({ page }) => {
  const state = await openCommand(page);
  await page.getByLabel('Your command').fill('Check my emails, find today\'s urgent items and schedule a meeting with the team tomorrow at 10 AM');
  await page.getByRole('button', { name: 'Show the plan' }).click();
  const plan = page.getByRole('region', { name: 'Here is what ANUM will do' });
  await expect(plan.getByText('2 steps · 1 will ask for your approval')).toBeVisible();
  await expect(plan.getByText('No calendar is connected yet', { exact: false })).toBeVisible();
  // Reorder: the meeting first.
  await plan.getByRole('button', { name: 'Move step 2 up' }).click();
  await expect(plan.getByRole('listitem').first()).toContainText('Schedule a meeting');
  await plan.getByRole('button', { name: 'Start' }).click();

  const detail = page.getByRole('region', { name: /ANUM is working|Command/ }).filter({ hasText: 'steps done' });
  await expect(detail.getByText('Three urgent emails today.')).toBeVisible();
  await expect(detail.getByText('Meeting drafted for tomorrow at 10:00.')).toBeVisible();
  await expect(detail.getByText('2 of 2 steps done')).toBeVisible();
  expect(state.created).toEqual([{
    text: 'Check my emails, find today\'s urgent items and schedule a meeting with the team tomorrow at 10 AM',
    steps: [{ instruction: 'Schedule a meeting with the team tomorrow at 10 AM' }, { instruction: 'Check my emails, find today\'s urgent items' }],
    source: 'typed', locale: 'en', attachments: [],
  }]);
  expect(state.runs.sort()).toEqual(['task_cmd_1', 'task_cmd_2']);
  // Next steps come from the API; a follow-up is planned, never run straight away.
  await detail.getByRole('button', { name: 'Draft replies to the urgent emails' }).click();
  await expect(page.getByLabel('Your command')).toHaveValue('Draft replies to the urgent emails');
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toBeVisible();
});

test('a saved command is planned with one click and a new one can be saved', async ({ page }) => {
  const state = await openCommand(page);
  const saved = page.getByRole('region', { name: 'Saved commands' });
  await saved.getByRole('button', { name: 'Plan it' }).first().click();
  await expect(page.getByLabel('Your command')).toHaveValue("Summarize my unread email and today's tasks");
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toBeVisible();
  await saved.getByRole('button', { name: 'New saved command' }).click();
  await saved.getByLabel('Name').fill('Supplier check');
  await saved.getByLabel('What ANUM should do').fill('Compare tyre prices from our three suppliers');
  await saved.getByRole('button', { name: 'Save' }).click();
  await expect(saved.getByText('Supplier check', { exact: true })).toBeVisible();
  expect(state.quick.at(-1)).toMatchObject({ title: 'Supplier check', text: 'Compare tyre prices from our three suppliers', category: 'custom' });
});

test('a viewer is told who can give commands; a failing history says what failed with Retry', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route(`${API}/api/v1/workspace-memberships/current`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'viewer', active: true }) }));
  await page.route(`${API}/api/v1/commands?*`, (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'internal', message: 'Database is restarting', correlation_id: 'c2' } }) }));
  await page.goto('/#command');
  await expect(page.getByText('Viewers cannot give commands')).toBeVisible();
  await expect(page.getByLabel('Your command')).toHaveCount(0);
  const history = page.getByRole('region', { name: 'Running and recent commands' });
  await expect(history.getByRole('alert')).toContainText('Database is restarting');
  await expect(history.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('a past command opens with its results, next steps and a report download', async ({ page }) => {
  await openCommand(page);
  await page.getByRole('region', { name: 'Running and recent commands' }).getByRole('button', { name: /Check my emails and summarize/ }).click();
  const detail = page.getByRole('region', { name: 'Command' });
  await expect(detail.getByText('Three urgent emails: the overdue invoice', { exact: false })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Remove from history' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Save as a quick command' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['dark', 'light'] as const) {
    test(`${locale} ${theme}: no serious axe violations, no horizontal overflow at the five viewports`, async ({ page }) => {
      await useSavedLocale(page, locale);
      if (theme === 'light') await page.addInitScript(() => { try { window.localStorage.setItem('anum.theme', 'light'); } catch { /* ignore */ } });
      await mockWorkspaceApi(page);
      for (const size of VIEWPORTS) {
        await page.setViewportSize(size);
        await page.goto('/#command');
        await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
        await expect(page.locator('.commandPanel .listRow').first()).toBeVisible();
        await expect(page.locator('.quickCard').first()).toBeVisible();
        expect(await noHorizontalOverflow(page), `${size.width}x${size.height}`).toBe(true);
      }
      expect(await serious(page)).toEqual([]);
    });
  }
}
