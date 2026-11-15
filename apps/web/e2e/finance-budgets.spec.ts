import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';

// Finance budgets, salary allocation, recurring payments and categories (docs/finance.md#budgets-and-planning,
// #recurring-payments, #category-ontology, #learned-merchants). The API is mocked at http://localhost:8000/** like the
// other Finance specs; every figure is SYNTHETIC.
async function mockApi(page: Page, options: { denied?: boolean } = {}) {
  const state = financeState({ denied: options.denied });
  const sent: { method: string; path: string; body: unknown }[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

async function noSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test('a budget is planned from categories, shows spent and risk, and the salary is split across budgets', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-budgets');
  const budgets = page.getByRole('region', { name: /^Your budgets/ });
  await expect(budgets.getByText('No budgets yet')).toBeVisible();
  await budgets.getByRole('button', { name: 'New budget' }).click();
  const dialog = page.getByRole('dialog', { name: 'New budget' });
  await dialog.getByRole('button', { name: 'Save budget' }).click();
  await expect(dialog.getByRole('alert')).toHaveText(/Enter a name/);
  await dialog.getByLabel('Name').fill('Food');
  await dialog.getByLabel('Limit or target').fill('500');
  await dialog.getByRole('checkbox', { name: 'Groceries' }).check();
  await dialog.getByRole('checkbox', { name: 'Vegetables' }).check();
  await dialog.getByRole('button', { name: 'Save budget' }).click();
  await expect(page.getByText('Food was saved.')).toBeVisible();
  const saved = sent.find((r) => r.method === 'POST' && r.path === '/api/v1/finance/budgets/plans');
  expect((saved?.body as { categories: string[]; subcategory_ids: string[] }).subcategory_ids).toEqual(['sys:vegetables']);
  const row = budgets.getByRole('listitem').filter({ hasText: 'Food' });
  await expect(row.getByText('Close to the limit')).toBeVisible();
  await expect(row.getByRole('progressbar', { name: /Food: 84 percent used/ })).toBeVisible();
  const allocation = page.getByRole('region', { name: 'Salary allocation' });
  await expect(allocation.getByText(/Income this month/)).toBeVisible();
  await expect(allocation.getByText(/Not planned yet/)).toBeVisible();
  for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
    await page.setViewportSize({ width, height });
    await noSidewaysScroll(page);
  }
});

test('recurring: a found payment is dismissed, a kept one turns on self-linking and says it is rarely used', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('tab', { name: 'Recurring' }).click();
  const found = page.getByRole('region', { name: 'Recurring payments found' });
  await found.getByRole('button', { name: 'Not a bill' }).click();
  await expect(page.getByText('NETFLIX.COM will not be suggested again.')).toBeVisible();
  expect(sent.some((r) => r.path === '/api/v1/finance/recurring/reject')).toBe(true);
  const kept = page.getByRole('region', { name: 'Kept recurring payments' });
  const rent = kept.getByRole('listitem').filter({ hasText: 'Rent' });
  await rent.getByLabel('How much you use Rent').selectOption('rarely');
  await expect(rent.getByText('Still billing while you rarely use it')).toBeVisible();
  await rent.getByRole('button', { name: 'Turn on self-linking' }).click();
  await expect(rent.getByText('Links by itself')).toBeVisible();
  await kept.getByRole('button', { name: 'Link matching payments now' }).click();
  await expect(page.getByText('1 linked; 0 left for you on the Bills page.')).toBeVisible();
});

test('categories list groups with built-in subcategories, and a learned merchant is switched off', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-suggestions?view=categories');
  await expect(page.getByRole('heading', { name: 'Food and drinks' })).toBeVisible();
  await expect(page.getByText('Vegetables')).toBeVisible();
  await page.getByLabel('Search categories').fill('zakat');
  await expect(page.getByRole('heading', { name: 'Religious and charitable' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Food and drinks' })).toHaveCount(0);
  const learned = page.getByRole('region', { name: 'Learned merchants' });
  await learned.getByRole('button', { name: 'Switch off' }).click();
  await expect(learned.getByText('Switched off')).toBeVisible();
  for (const [width, height] of [[1440, 900], [768, 1024], [390, 844]] as const) {
    await page.setViewportSize({ width, height });
    await noSidewaysScroll(page);
  }
});

test('budgets say who can use them when Finance is not allowed', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-budgets');
  await expect(page.getByText(/finance/i).first()).toBeVisible();
});
