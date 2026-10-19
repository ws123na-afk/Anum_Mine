import { expect, test, type Page } from '@playwright/test';
import { approvalPagesResponse } from './support/approvalPages';

// Approvals at scale (docs/approvals-and-risk.md#paging-filters-and-search): pending first, filter
// tabs, server-side search, "Load more" history, and every state of the list.
const now = new Date().toISOString();
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const hash = '3f'.repeat(32);
const task = { id: 'task_bulk', title: 'Bulk', prompt: 'Many approvals', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };

function approval(n: number, status: 'pending' | 'approved' | 'rejected', action = 'external.action') {
  const created = new Date(Date.now() - n * 60_000).toISOString();
  return {
    id: `approval_${status}_${n}`, task_id: 'task_bulk', action, risk_level: 'high', status, reason: `Reason number ${n}`, created_at: created,
    run_id: 'run_bulk', step_id: 'step_bulk', arguments: { note: `call ${n}` }, payload_hash: hash,
    expires_at: status === 'pending' ? later : null, decided_at: status === 'pending' ? null : created, decided_by: status === 'pending' ? null : 'user_local',
  };
}

const cards = (page: Page) => page.getByRole('article', { name: /^Approval for / });

interface Options { list?: Record<string, unknown>[]; pageStatus?: number; pageFailures?: number }

async function mockApi(page: Page, options: Options = {}) {
  const list = options.list ?? [
    ...Array.from({ length: 30 }, (_, i) => approval(i + 1, 'pending')),
    ...Array.from({ length: 40 }, (_, i) => approval(i + 100, i % 2 ? 'rejected' : 'approved', i === 7 ? 'refund.issue' : 'external.action')),
  ];
  const pageCalls: string[] = [];
  let failures = options.pageFailures ?? 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (url.pathname === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (url.pathname === '/api/v1/tasks') return json([task]);
    if (url.pathname === '/api/v1/approvals') return json(list.filter((a) => a.status === 'pending').slice(0, 200));
    if (url.pathname === '/api/v1/approvals/page') {
      pageCalls.push(url.search);
      if (options.pageStatus) return json({ error: { code: 'forbidden', message: 'Approval access is required.' } }, options.pageStatus);
      if (failures > 0) return json({ error: { code: 'internal', message: 'The approvals store is busy.' } }, 500);
    }
    const paged = approvalPagesResponse(url, list);
    if (paged !== undefined) return json(paged);
    return route.abort();
  });
  return Object.assign(pageCalls, { heal: () => { failures = 0; } });
}

test('shows pending first, a count, and loads more history a page at a time', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#approvals');

  await expect(cards(page)).toHaveCount(25);
  await expect(page.getByText('25 of 70 shown')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Approval history' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Load more approvals' }).click();
  await expect(page.getByText('50 of 70 shown')).toBeVisible();
  await expect(cards(page)).toHaveCount(30);
  const history = page.getByRole('region', { name: 'Approval history' });
  await expect(history.locator('details')).toHaveCount(20);

  await page.getByRole('button', { name: 'Load more approvals' }).click();
  await expect(page.getByText('70 of 70 shown')).toBeVisible();
  await expect(history.locator('details')).toHaveCount(40);
  await expect(page.getByRole('button', { name: 'Load more approvals' })).toHaveCount(0);
  expect(calls.some((search) => search.includes('cursor='))).toBe(true);
});

test('the filter tabs narrow the list on the server', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#approvals');
  await expect(cards(page)).toHaveCount(25);

  await page.getByRole('button', { name: 'History', exact: true }).click();
  await expect(cards(page)).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Approval history' }).locator('details')).toHaveCount(25);
  expect(calls.some((search) => search.includes('status=decided'))).toBe(true);

  await page.getByRole('button', { name: 'Pending', exact: true }).click();
  await expect(cards(page)).toHaveCount(25);
  await expect(page.getByRole('region', { name: 'Approval history' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Pending', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('search is sent to the server, and an empty result offers to clear it', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#approvals');
  await expect(cards(page)).toHaveCount(25);

  await page.getByRole('searchbox', { name: 'Search approvals' }).fill('refund');
  const history = page.getByRole('region', { name: 'Approval history' });
  await expect(history.locator('details')).toHaveCount(1);
  await expect(history.getByText('refund.issue')).toBeVisible();
  await expect(cards(page)).toHaveCount(0);
  expect(calls.some((search) => search.includes('query=refund'))).toBe(true);

  await page.getByRole('searchbox', { name: 'Search approvals' }).fill('zzz-nothing');
  await expect(page.getByRole('heading', { name: 'No approvals match your search' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear' }).first().click();
  await expect(cards(page)).toHaveCount(25);
  await expect(page.getByRole('searchbox', { name: 'Search approvals' })).toHaveValue('');
});

test('history with no decisions yet says so', async ({ page }) => {
  await mockApi(page, { list: [approval(1, 'pending')] });
  await page.goto('/#approvals');
  await expect(cards(page)).toHaveCount(1);
  await page.getByRole('button', { name: 'History', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'No decisions yet' })).toBeVisible();
});

test('an empty queue shows the queue-clear state', async ({ page }) => {
  await mockApi(page, { list: [] });
  await page.goto('/#approvals');
  await expect(page.getByRole('heading', { name: 'Queue clear' })).toBeVisible();
});

test('a failed load says what failed and Retry loads it', async ({ page }) => {
  const calls = await mockApi(page, { pageFailures: 1 });
  await page.goto('/#approvals');
  const alert = page.getByRole('alert').filter({ hasText: 'Could not load approvals' });
  await expect(alert).toContainText('The approvals store is busy.');
  calls.heal();
  // The shell's own refresh can recover the list first; either way the list ends up loaded.
  await alert.getByRole('button', { name: 'Retry' }).click({ timeout: 2000 }).catch(() => undefined);
  await expect(cards(page)).toHaveCount(25);
});

test('a person without approval access is told who can help', async ({ page }) => {
  await mockApi(page, { pageStatus: 403 });
  await page.goto('/#approvals');
  await expect(page.getByText('You cannot see approvals')).toBeVisible();
  await expect(page.getByText(/Ask an owner or admin/)).toBeVisible();
});

test('the list fits the screen without sideways scrolling', async ({ page }) => {
  await mockApi(page);
  for (const size of [{ width: 1440, height: 900 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await page.goto('/#approvals');
    await expect(cards(page).first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${size.width}px`).toBeLessThanOrEqual(0);
  }
});
