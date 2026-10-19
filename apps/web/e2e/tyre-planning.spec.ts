import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { emptyOperations, mockTyre } from './support/tyreApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Tyre planning (docs/tyre-planning.md): the owner connects TyrePulse, maps it with the preset and
// syncs; members read the dashboard, forecasts, plan and alerts and generate reports; viewers read.

test('owner connects TyrePulse, maps with the preset and syncs', async ({ page }) => {
  await mockWorkspaceApi(page);
  const state = await mockTyre(page, { connected: false, mapped: false, synced: false });
  await page.goto('/#tyre');
  await expect(page.getByRole('heading', { name: 'Tyre planning', level: 1 })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Connection' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Connect the source')).toBeVisible();
  // Nothing to save without a credential; the read-only connection string is write-only.
  await page.getByRole('button', { name: 'Save connection' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Enter the credential' })).toBeVisible();
  // TyrePulse sign-in is the default: the project URL, the publishable key and the reader user.
  await expect(page.getByRole('radio', { name: /TyrePulse sign-in/ })).toBeChecked();
  await page.getByLabel('Supabase project URL').fill('https://example.supabase.co');
  await page.getByLabel('Publishable (anon) key').fill('sb_secret_example');
  await page.getByLabel('Reader e-mail').fill('anum-reader@example.com');
  await page.getByLabel('Reader password').fill('example');
  await page.getByRole('button', { name: 'Save connection' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'That is a secret (service_role) key' })).toBeVisible();
  await page.getByLabel('Publishable (anon) key').fill('sb_publishable_example');
  await page.getByRole('button', { name: 'Save connection' }).click();
  await expect(page.getByText('Connection saved. Test it next.')).toBeVisible();
  expect(state.saved?.kind).toBe('supabase_auth');
  expect(JSON.parse(state.saved?.secret ?? '{}')).toEqual({ api_key: 'sb_publishable_example', email: 'anum-reader@example.com', password: 'example' });
  await expect(page.getByText('anum-reader@example.com · key …mple')).toBeVisible();
  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByText('Signed in as anum-reader@example.com (read-only).', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Discover tables' }).click();
  await expect(page.getByText('Found 3 tables.')).toBeVisible();
  await page.getByRole('button', { name: 'Use the TyrePulse preset' }).click();
  await expect(page.getByText('TyrePulse preset', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Preview on a sample' }).click();
  await expect(page.getByRole('region', { name: 'Sample of the first rows of each table' })).toContainText('no tread or pressure reading (6)');
  await page.getByRole('button', { name: 'Save mapping' }).click();
  await expect(page.getByText('Mapping saved. Sync the data next.')).toBeVisible();
  await page.getByRole('button', { name: 'Sync now' }).first().click();
  await expect(page.getByText('Synced. 3 new alerts.')).toBeVisible();
  expect(state.requests).toEqual(expect.arrayContaining(['PUT /connection', 'POST /connection/test', 'POST /discover', 'PUT /mapping', 'POST /sync']));
});

test('dashboard, forecasts with search and paging, plan, alerts and reports', async ({ page }) => {
  await mockWorkspaceApi(page);
  const state = await mockTyre(page, { role: 'member' });
  await page.goto('/#tyre');
  await expect(page.getByText('Average tread')).toBeVisible();
  await expect(page.locator('.tyreMetric').filter({ hasText: 'Pressure compliance' })).toContainText('91.3%');
  await expect(page.getByRole('heading', { name: 'Due soonest' })).toBeVisible();
  // Fleet operations from TyrePulse: inspections, schedules, checklists and work orders with real counts.
  const operations = page.getByRole('region', { name: 'Fleet operations data' });
  await expect(operations.locator('.tyreMetric').filter({ hasText: 'Vehicles inspected (30 days)' })).toContainText('6 in 90 days, of 6 vehicles');
  await expect(operations.locator('.tyreMetric').filter({ hasText: 'Inspections (30 days)' })).toContainText(/1,?837 synced/);
  await expect(operations.locator('.tyreMetric').filter({ hasText: 'Open work orders' })).toContainText('7');
  await expect(operations.locator('.tyreMetric').filter({ hasText: 'Workshop checklists (30 days)' })).toContainText('1 failed');
  await expect(operations).toContainText('Readings that match no tyre record stay readings of that vehicle position.');

  // Keyboard: arrows move between tabs.
  await page.getByRole('tab', { name: 'Dashboard' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Forecasts' })).toBeFocused();
  await expect(page.getByRole('region', { name: 'Tyre removal forecasts' })).toBeVisible();
  await expect(page.getByText('Showing 1-25 of 30')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('Showing 26-30 of 30')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search' }).fill('fake-sn-0001');
  await page.getByRole('search', { name: 'Filter forecasts' }).getByRole('button', { name: 'Search' }).click();
  await expect(page.getByText('Showing 1-1 of 1')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Due now' })).toBeVisible();
  await page.getByText('How forecasts and the plan are worked out').click();
  await expect(page.getByText(/Theil-Sen line/)).toBeVisible();

  await page.getByRole('tab', { name: 'Purchase plan' }).click();
  await expect(page.getByRole('region', { name: 'Purchase plan' })).toContainText('1,200 SAR');
  await expect(page.getByText('Only workspace owners can change the assumptions.')).toBeVisible();

  await page.getByRole('tab', { name: 'Alerts' }).click();
  await expect(page.locator('.tyreAlert')).toHaveCount(3);
  await page.locator('.tyreAlert').first().getByRole('button', { name: 'Acknowledge' }).click();
  await expect(page.locator('.tyreAlert').first()).toContainText('acknowledged by user_local');

  await page.getByRole('tab', { name: 'Reports' }).click();
  await expect(page.getByText('No reports yet. Generate one above or schedule one below.')).toBeVisible();
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByText('Report generated.')).toBeVisible();
  await expect(page.locator('.tyreReport')).toContainText('Tyres fitted');
  await page.locator('.tyreReport').getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText(/No email or WhatsApp integration is connected/)).toBeVisible();
  expect(state.requests).toContain('POST /alerts/a1/acknowledge');
});

test('viewer reads but is told who can act; nothing synced says what is next', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockTyre(page, { role: 'viewer', synced: false });
  await page.goto('/#tyre');
  await expect(page.getByText('Only workspace owners can connect the data source')).toBeVisible();
  await expect(page.getByText('Members and owners can sync.')).toBeVisible();
  await page.getByRole('tab', { name: 'Dashboard' }).click();
  await expect(page.getByText('The data has not been synced yet. A member or owner can sync it.')).toBeVisible();
  await page.getByRole('tab', { name: 'Reports' }).click();
  await expect(page.getByText('Members and owners can generate, send and schedule reports.').first()).toBeVisible();
});

test('no operations data synced says how to map it', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockTyre(page, { operations: emptyOperations });
  await page.goto('/#tyre');
  await expect(page.getByRole('region', { name: 'Fleet operations data' })).toContainText('No inspections, schedules, checklists or work orders were synced.');
});

test('a failing API says what failed and offers a retry', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route('http://localhost:8000/api/v1/tyre/**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Database unavailable' } }) }));
  await page.goto('/#tyre');
  await expect(page.getByRole('alert')).toContainText('Tyre planning could not be loaded: The server could not reach its database');
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  test(`${locale}: tyre planning has no serious axe violations`, async ({ page }) => {
    test.setTimeout(90_000);
    await mockWorkspaceApi(page);
    await mockTyre(page);
    await useSavedLocale(page, locale);
    await page.goto('/#tyre');
    await expect(page.locator('.tyreMetric').first()).toBeVisible();
    for (const tab of locale === 'ar' ? ['لوحة المعلومات', 'التوقعات', 'التنبيهات', 'الاتصال'] : ['Dashboard', 'Forecasts', 'Alerts', 'Connection']) {
      await page.getByRole('tab', { name: tab }).click();
      await page.waitForTimeout(150);
      const results = await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
      expect(serious, tab).toEqual([]);
    }
    if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  });
}
