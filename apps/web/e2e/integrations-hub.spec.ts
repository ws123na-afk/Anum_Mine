import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Integrations tabs (docs/integrations-setup.md#the-integrations-page): the KPI row, Data sync, API and
// webhooks, Permissions and Logs, all from the mocked API (workspaceApi.ts: Valkey degraded,
// PostgreSQL and the webhook connected, NATS not used; WhatsApp failing one of one account).
const now = new Date().toISOString();
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function withIntegrationAudit(page: Page) {
  const queries: string[] = [];
  await page.route('http://localhost:8000/api/v1/audit-records?**', async (route) => {
    const url = new URL(route.request().url());
    queries.push(url.search);
    const record = (id: string, action: string, target: string, outcome: string) => ({ id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', actor: 'user_local', action, target, outcome, correlation_id: 'c', created_at: now, metadata: {} });
    const first = !url.searchParams.get('cursor');
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(first
      ? { items: [record('a1', 'integration.tested', 'valkey', 'failed'), record('a2', 'integration.credential_set', 'external-webhook', 'success')], next_cursor: 'next' }
      : { items: [record('a3', 'integration.setup_checked', 'whatsapp', 'success')], next_cursor: null }) });
  });
  return queries;
}

test('the KPI row and tabs show real figures, and each tab opens by link and keyboard', async ({ page }) => {
  await mockWorkspaceApi(page);
  const audit = await withIntegrationAudit(page);
  await page.goto('/#integrations');
  await expect(page.getByRole('heading', { level: 1, name: 'Integrations' })).toBeVisible();
  const kpis = page.getByRole('region', { name: 'Integration figures' });
  // Connected: the AI model, PostgreSQL and the webhook; issues: WhatsApp and Valkey.
  await expect(kpis.getByRole('listitem').filter({ hasText: 'Connected' })).toContainText('3');
  await expect(kpis.getByRole('listitem').filter({ hasText: 'Issues' })).toContainText('2');

  const tabs = page.getByRole('tablist', { name: 'Integrations sections' });
  await tabs.getByRole('tab', { name: 'Data sync' }).click();
  await expect(page).toHaveURL(/#integrations-sync$/);
  await expect(page.getByText('1 account failed its last sync')).toBeVisible();
  await page.locator('[data-sync-row="whatsapp"]').getByRole('button', { name: 'Open WhatsApp Business' }).click();
  await expect(page.getByRole('dialog', { name: 'WhatsApp Business' })).toBeVisible();
  await page.keyboard.press('Escape');

  await tabs.getByRole('tab', { name: 'Data sync' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.getByRole('tab', { name: 'API & webhooks' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('https://api.example.com/api/v1/whatsapp/webhook')).toBeVisible();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy the address for WhatsApp Business' }).click();
  await expect(page.getByText(/Copied https:\/\/api\.example\.com\/api\/v1\/whatsapp\/webhook|Copying failed/)).toBeVisible();
  await expect(page.locator('[data-event-service="external-webhook"]')).toContainText('External REST webhook');

  await tabs.getByRole('tab', { name: 'Permissions' }).click();
  await expect(page.locator('[data-permission-row="external-webhook"]')).toContainText('Stored');
  await expect(page.locator('[data-permission-row="valkey"]')).toContainText('Server setting');

  await tabs.getByRole('tab', { name: 'Logs' }).click();
  await expect(page).toHaveURL(/#integrations-logs$/);
  await expect(page.locator('[data-log-row="a1"]')).toContainText('Connection tested');
  await expect(page.locator('[data-log-row="a1"]')).toContainText('Valkey');
  await expect(page.locator('[data-log-row="a1"]')).toContainText('Failed');
  await page.getByRole('button', { name: 'Load more' }).click();
  await expect(page.locator('[data-log-row="a3"]')).toContainText('Setup checked');
  await page.getByRole('combobox', { name: 'Integration' }).selectOption('valkey');
  await expect.poll(() => audit.some((q) => q.includes('target=valkey'))).toBe(true);
  expect(audit.every((q) => q.includes('action=integration.*'))).toBe(true);
  expect(await noOverflow(page)).toBe(true);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
});

test('a member who cannot read the audit trail is told who can, in Arabic, right to left', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.route('http://localhost:8000/api/v1/audit-records?**', (route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'Not allowed', correlation_id: 'c' } }) }));
  await page.goto('/#integrations-logs');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByText('لا يقرأ سجل التدقيق إلا المالكون والمسؤولون. اطلب من أحدهم مراجعته.')).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
});
