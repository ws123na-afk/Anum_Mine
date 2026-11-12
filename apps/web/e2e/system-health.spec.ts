import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { mockBilling } from './support/billingApi';
import { mockSystemHealth, type HealthOptions } from './support/systemHealthApi';

// System health (docs/system-health.md): the platform owner's read-only checks in the platform console, problems
// first with what to do, the ids the owner notices need, the outside uptime check, a refusal for anyone who is not
// a platform operator and a failed read that can be retried.
const serious = async (page: Page) => (await new AxeBuilder({ page }).include('.blConsole').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze())
  .violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);

async function open(page: Page, options: HealthOptions = {}) {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  const state = await mockSystemHealth(page, options);
  await page.goto('/#platformBilling-health');
  return state;
}

test('problems come first with their numbers and what to do, and the notice ids are ready to paste', async ({ page }) => {
  await open(page);
  const main = page.locator('#main');
  await expect(main.getByRole('heading', { name: 'System health', level: 2 })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Billing console sections' }).getByRole('button', { name: 'System health' })).toHaveAttribute('aria-current', 'page');
  await expect(main.getByText('5 checks need you')).toBeVisible();
  const rows = main.locator('.hlItem');
  await expect(rows).toHaveCount(11);
  // Red first: the off-site backup older than 26 hours, then the mock model.
  await expect(rows.nth(0)).toContainText('Off-site backup');
  await expect(rows.nth(0)).toContainText('Problem');
  await expect(rows.nth(0)).toContainText('31 hours ago');
  await expect(rows.nth(0)).toContainText('sudo bash infra/oracle/backup.sh offsite');
  await expect(rows.nth(1)).toContainText('The server uses the mock model (ANUM_MODEL_PROVIDER=mock).');
  await expect(rows.filter({ hasText: 'Restore drill' })).toContainText('No restore drill has succeeded yet.');
  await expect(rows.filter({ hasText: 'Errors' })).toContainText('4 errors in the last 24 hours.');
  const notices = rows.filter({ hasText: 'Owner notices' });
  await expect(notices.locator('code')).toHaveText('ANUM_SYSTEM_HEALTH_TENANT_ID=tenant_a\nANUM_SYSTEM_HEALTH_WORKSPACE_ID=workspace_a');
  // Green checks say no fix.
  await expect(rows.filter({ hasText: 'Disk space' })).toContainText('61 GB free of 96 GB (63.5%).');
  await expect(rows.filter({ hasText: 'Disk space' })).not.toContainText('What to do:');
  // The outside uptime check names the public health address.
  await expect(main.getByRole('heading', { name: 'Outside uptime check' })).toBeVisible();
  await expect(main.locator('.hlMonitor code')).toHaveText('http://localhost:8000/health');
  expect(await serious(page)).toEqual([]);
});

test('all green, a re-check reads again, and the Arabic page reads right to left', async ({ page }) => {
  const state = await open(page, { healthy: true });
  const main = page.locator('#main');
  await expect(main.getByText('All checks are green')).toBeVisible();
  await expect(main.locator('.hlItem code')).toHaveCount(0);
  await main.getByRole('button', { name: 'Check again' }).click();
  await expect(main.getByText('All checks are green')).toBeVisible();
  expect(state.reads).toBeGreaterThanOrEqual(2);

  const ar = await page.context().newPage();
  await mockWorkspaceApi(ar);
  await mockBilling(ar);
  await mockSystemHealth(ar);
  await useSavedLocale(ar, 'ar');
  await ar.goto('/#platformBilling-health');
  await expect(ar.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(ar.locator('#main').getByRole('heading', { name: 'صحة النظام', level: 2 })).toBeVisible();
  await expect(ar.locator('#main .hlItem').first()).toContainText('مشكلة');
  expect(await serious(ar)).toEqual([]);
});

test('someone who is not a platform operator is told who can see it, and a failed read can be retried', async ({ page }) => {
  await open(page, { denied: true });
  await expect(page.locator('#main').getByText('Only a platform operator can see System health')).toBeVisible();

  const retry = await page.context().newPage();
  await mockWorkspaceApi(retry);
  await mockBilling(retry);
  await mockSystemHealth(retry, { failing: true });
  await retry.goto('/#platformBilling-health');
  const main = retry.locator('#main');
  await expect(main.getByText(/Could not read the server's health/)).toBeVisible();
  await main.getByRole('button', { name: 'Try again' }).click();
  await expect(main.locator('.hlItem')).toHaveCount(11);
});
