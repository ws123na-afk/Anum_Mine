import { expect, test, type Page } from '@playwright/test';
import { commerceHash, commerceReply, commerceState } from './support/commerceApi';
import { useSavedLocale } from './support/workspaceApi';

// Commerce (docs/commerce.md): the dashboard, orders, catalogue with approval-gated price changes,
// alerts and connections, as an owner, a member and a viewer, with empty and error states.
async function mockApi(page: Page, role: 'owner' | 'member' | 'viewer' = 'owner', options: { connected?: boolean; failDashboard?: boolean } = {}) {
  const state = commerceState(role, { connected: options.connected ?? true });
  const sent: { method: string; path: string; body: unknown }[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role, active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (options.failDashboard && path === '/api/v1/commerce/dashboard') return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c' } }, 503);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/commerce')) sent.push({ method: request.method(), path, body });
    const reply = commerceReply(state, request.method(), url, body);
    if (reply) return json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Commerce sections' }).getByRole('button', { name });

test('an owner reads the dashboard: totals, sales by day, late orders, low stock and the buy box', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#commerce');
  await expect(page.getByRole('heading', { name: 'Commerce', level: 1 })).toBeVisible();
  const totals = page.getByRole('region', { name: 'Totals for the range' });
  await expect(totals.getByText('SAR 41,250.50')).toBeVisible();
  await expect(totals.getByText('1 late')).toBeVisible();
  await expect(page.getByRole('img', { name: /Sales by day for 30 days/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Orders to ship' }).getByText('Late')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Low stock' }).getByText('MX-2055516')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Buy box and prices' }).getByText('Buy box lost at SAR 430.00')).toBeVisible();
  await page.getByText('Show as a table').click();
  await expect(page.getByRole('region', { name: /Sales by day/ }).getByRole('table')).toBeVisible();
  await page.getByLabel('Currency').selectOption('AED');
  await expect(page.getByRole('heading', { name: 'Sales by day (AED)' })).toBeVisible();
});

test('orders page through, filter and open with buyer details for the owner', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#commerce');
  await tab(page, 'Orders').click();
  await expect(page.getByText('1 to 9 of 9')).toBeVisible();
  await page.getByLabel('Status').selectOption('');
  await expect(page.getByText('1 to 20 of 30')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('21 to 30 of 30')).toBeVisible();
  await page.getByRole('button', { name: 'Previous' }).click();
  await page.getByRole('button', { name: 'Open order 402-0000001-0000001' }).click();
  const detail = page.getByRole('region', { name: 'Order 402-0000001-0000001' });
  await expect(detail.getByText('Example Buyer')).toBeVisible();
  await detail.getByRole('button', { name: 'Close the order' }).click();
  await page.getByPlaceholder('Order number or SKU').fill('nothing-matches');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('No order matches these filters.')).toBeVisible();
});

test('a price change is proposed and waits for approval with its payload hash', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#commerce');
  await tab(page, 'Catalogue').click();
  await page.getByPlaceholder(/SKU, title, brand or size/).fill('2055516');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('MX-2055516 · Michelin · 205/55R16 · 91V · DOT 23/2025')).toBeVisible();
  await page.getByRole('button', { name: 'Change price' }).first().click();
  const input = page.getByLabel('New price (SAR)');
  await input.fill('0');
  await page.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(page.getByRole('alert')).toContainText('Enter a price above 0');
  await input.fill('435');
  await page.getByRole('button', { name: 'Ask for approval' }).click();
  const pending = page.getByRole('region', { name: 'Waiting for approval' });
  await expect(pending).toContainText('Set MX-2055516 on Amazon.sa to SAR 435.00.');
  await expect(pending).toContainText(commerceHash.slice(0, 8));
  expect(sent.find((s) => s.path.endsWith('/price-change'))?.body).toEqual({ price: 435 });
  await pending.getByRole('button', { name: 'Open Approvals' }).click();
  await expect(page).toHaveURL(/#approvals$/);
});

test('a new product validates its tyre size before saving', async ({ page }) => {
  const { sent } = await mockApi(page, 'member');
  await page.goto('/#commerce');
  await tab(page, 'Catalogue').click();
  await page.getByRole('button', { name: 'Add product' }).click();
  await page.getByRole('textbox', { name: 'SKU', exact: true }).fill('GY-1956515');
  await page.getByLabel('Tyre size').fill('195-65-15');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('alert')).toContainText('Write the size like 205/55R16.');
  await page.getByLabel('Tyre size').fill('195/65R15');
  await page.getByLabel('Title (Arabic)').fill('جوديير');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Added GY-1956515.' })).toBeVisible();
  const created = sent.find((s) => s.method === 'POST' && s.path.endsWith('/products'))?.body as { tyre: { size: string; rim_inches: number }; title_ar: string };
  expect(created.tyre).toMatchObject({ size: '195/65R15', rim_inches: 15 });
  expect(created.title_ar).toBe('جوديير');
});

test('alerts can be acknowledged and connections synced', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#commerce');
  await tab(page, 'Alerts').click();
  await expect(page.getByText('Low stock: MX-2055516 on Amazon.sa')).toBeVisible();
  await page.getByRole('button', { name: 'Acknowledge' }).first().click();
  await expect(page.getByText('Low stock: MX-2055516 on Amazon.sa')).toHaveCount(0);
  await tab(page, 'Connections').click();
  await expect(page.getByText('Reconnect needed')).toBeVisible();
  await expect(page.getByText(/staff account that holds only view keys/)).toBeVisible();
  await page.getByRole('button', { name: 'Sync now' }).first().click();
  await expect(page.getByText('Amazon KSA + UAE is up to date (5 records).')).toBeVisible();
});

test('an owner adds a noon connection; the form says what is missing', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#commerce');
  await tab(page, 'Connections').click();
  await page.getByRole('button', { name: 'Add connection' }).click();
  await page.getByRole('radio', { name: 'noon' }).check();
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Enter the API key.');
  await page.getByLabel('API key').fill('noon-key-example');
  await page.getByRole('checkbox', { name: 'noon UAE' }).uncheck();
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByText(/^Connected noon Saudi/)).toBeVisible();
  expect(sent.find((s) => s.method === 'POST' && s.path.endsWith('/connectors'))?.body).toEqual({ channel: 'noon', marketplaces: ['SA'], settings: {}, credentials: { api_key: 'noon-key-example' } });
});

test('a viewer reads everything but changes nothing, and sees who can', async ({ page }) => {
  await mockApi(page, 'viewer');
  await page.goto('/#commerce');
  await tab(page, 'Catalogue').click();
  await expect(page.getByText('Members and owners can edit the catalogue.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Change price' })).toHaveCount(0);
  await tab(page, 'Orders').click();
  await page.getByRole('button', { name: 'Open order 402-0000001-0000001' }).click();
  await expect(page.getByText('Buyer details are visible to workspace owners only.')).toBeVisible();
  await tab(page, 'Connections').click();
  await expect(page.getByText('Only workspace owners can connect channels.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sync now' })).toHaveCount(0);
  await expect(page.getByText('Workspace owners can change these rules.')).toBeVisible();
});

test('with no channel connected the owner is led to connect one', async ({ page }) => {
  await mockApi(page, 'owner', { connected: false });
  await page.goto('/#commerce');
  await expect(page.getByRole('heading', { name: 'Connect your first sales channel' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add connection' })).toBeVisible();
});

test('a failed dashboard says what failed and retries', async ({ page }) => {
  await mockApi(page, 'owner', { failDashboard: true });
  await page.goto('/#commerce');
  await expect(page.getByRole('alert')).toContainText('The dashboard could not be loaded');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('the catalogue reads in Arabic, right to left', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#commerce');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('navigation', { name: 'أقسام التجارة' }).getByRole('button', { name: 'الكتالوج' }).click();
  await expect(page.getByText('ميشلان برايمسي 4 مقاس 205/55R16')).toBeVisible();
  await expect(page.getByText('13 قطعة في المخزون')).toBeVisible();
});
