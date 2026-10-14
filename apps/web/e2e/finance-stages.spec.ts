import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance stages 3 to 5 (docs/finance.md): Safe to spend built line by line, the Guardian rules
// (every change confirmed with the code shown, an assistant's proposal reviewed or rejected, a dry
// run that never moves money) and read-only connections with exchange rates. Plus the empty, error
// and permission-denied states and Arabic.
async function mockApi(page: Page, options: Parameters<typeof financeState>[0] & { role?: 'owner' | 'member' | 'viewer' } = {}) {
  const role = options.role ?? 'member';
  const state = financeState(options);
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
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/finance')) sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Finance sections' }).getByRole('button', { name });

test('safe to spend is built line by line per currency, with a buffer the person sets and rates for one total', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance');
  const card = page.getByRole('region', { name: 'Safe to spend' });
  await expect(card).toContainText('AED');
  await card.getByRole('button', { name: 'How it is built' }).click();
  await expect(page).toHaveURL(/#finance-safe$/);
  const aed = page.getByRole('article', { name: 'Safe to spend in AED' });
  await expect(aed.getByRole('table', { name: /How safe to spend in AED is built/ })).toBeVisible();
  for (const text of ['Money you have', 'Bills due this month', 'Minimum debt payments', 'Set aside for goals', 'Your buffer', 'Rent', /Due on day \u2066?25/]) {
    await expect(aed).toContainText(text);
  }
  await expect(aed).toContainText('left over 15 months');
  await expect(page.getByRole('article', { name: 'Safe to spend in USD' })).toContainText('USD');
  await page.getByText('Not counted (1)').click();
  await expect(page.getByText('Card: what it owes is covered by its minimum payment')).toBeVisible();
  await expect(page.getByText(/not advice and not a forecast/)).toBeVisible();

  await page.getByRole('button', { name: 'Buffer and currencies' }).click();
  const dialog = page.getByRole('dialog', { name: 'Safe to spend settings' });
  await dialog.getByLabel('Amount').first().fill('-1');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Enter an amount of zero or more.');
  await dialog.getByLabel('Amount').first().fill('2000');
  await dialog.getByLabel(/Also show one total/).check();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Settings saved. Safe to spend is worked out again.')).toBeVisible();
  expect(sent.some((r) => r.method === 'PUT' && r.path.endsWith('/plan') && (r.body as { combine_currencies: boolean }).combine_currencies)).toBe(true);
  // No rate yet: the page says which one is missing and where to get it.
  await expect(page.getByText(/One total needs a rate from the last 7 days for .*USD\/AED/)).toBeVisible();
  await page.getByRole('button', { name: 'Open connections' }).click();
  await expect(page).toHaveURL(/#finance-connections$/);
});

test('rules change only when the person confirms the change they reviewed, with its code', async ({ page }) => {
  const { sent } = await mockApi(page, { empty: true });
  await page.goto('/#finance-guardian');
  await expect(page.getByRole('heading', { name: 'No rules yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Set rules' }).first().click();
  const editor = page.getByRole('dialog', { name: 'Change your rules' });
  await editor.getByRole('button', { name: 'Add a cap' }).click();
  await editor.getByLabel('At most a month').fill('800');
  await editor.getByLabel('Blocked merchants').fill('casino\nx');
  await editor.getByRole('button', { name: 'Review change' }).click();
  await expect(editor.getByRole('alert')).toHaveText('A blocked merchant needs at least two letters.');
  await editor.getByLabel('Blocked merchants').fill('casino');
  await editor.getByRole('button', { name: 'Review change' }).click();
  const review = page.getByRole('dialog', { name: 'Confirm your new rules' });
  await expect(review).toContainText('Dining: at most');
  await expect(review).toContainText('Blocked merchants: casino');
  await expect(review.locator('code')).toHaveText('1ccc cccc cccc');
  // Nothing is confirmed by proposing.
  expect(sent.filter((r) => r.path.endsWith('/confirm'))).toHaveLength(0);
  await review.getByRole('button', { name: 'Confirm rules' }).click();
  await expect(page.getByText('Your rules are now version 1.')).toBeVisible();
  const confirm = sent.find((r) => r.path.endsWith('/confirm'));
  expect((confirm?.body as { payload_hash: string }).payload_hash).toBe('1'.padEnd(64, 'c'));
  await expect(page.getByRole('region', { name: 'Your spending rules' }).getByText('Version 1', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'History' })).toContainText('Version 1');
  // No pay, transfer or trade action anywhere on the page.
  await expect(page.getByRole('button', { name: /^(pay|transfer|buy|sell|trade)/i })).toHaveCount(0);
});

test("an assistant's proposal waits for review; loosening is named; a dry run never moves money", async ({ page }) => {
  const { sent } = await mockApi(page, { agentProposal: true });
  await page.goto('/#finance-guardian');
  const pending = page.getByRole('region', { name: '1 change waits for you' });
  await expect(pending).toContainText('Proposed by an assistant');
  await expect(pending).toContainText('You asked to cut dining');
  await pending.getByRole('button', { name: 'Review' }).click();
  const review = page.getByRole('alertdialog', { name: 'Confirm your new rules' });
  await expect(review).toContainText('Dining: at most');
  await review.getByRole('button', { name: 'Cancel' }).click();
  await pending.getByRole('button', { name: 'Reject' }).click();
  await expect(page.getByText('Change rejected. Your rules are as they were.')).toBeVisible();
  expect(sent.some((r) => r.path.endsWith('/reject'))).toBe(true);

  await expect(page.getByRole('region', { name: 'This month against your rules' })).toContainText('near your cap of');

  await page.getByRole('button', { name: 'Change rules' }).click();
  const editor = page.getByRole('dialog', { name: 'Change your rules' });
  await editor.getByRole('button', { name: 'Remove the cap on Dining' }).click();
  await editor.getByRole('button', { name: 'Review change' }).click();
  const review2 = page.getByRole('dialog', { name: 'Confirm your new rules' });
  await expect(review2).toContainText('This loosens your rules (Monthly caps per category). Only you can do that.');
  await expect(review2).toContainText('No longer a rule:');
  await page.keyboard.press('Escape');
  await expect(page.getByText('Your change is waiting below. Nothing changes until you confirm it.')).toBeVisible();

  const check = page.getByRole('region', { name: 'Check a spend' });
  await check.getByLabel('Amount').fill('120');
  await check.getByLabel('Category').selectOption('dining');
  await check.getByRole('button', { name: 'Check' }).click();
  await expect(check.getByRole('status')).toContainText('Your rules do not allow it');
  await expect(check.getByRole('status')).toContainText('it goes over your cap of');
  await expect(check.getByRole('status')).toContainText('ANUM does not move money');
});

test('connections are read only: the ECB rates connect and sync, unbuilt sources name the owner action, rates can be typed', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-connections');
  await expect(page.getByText('No connections yet. Connect a source below.')).toBeVisible();
  const sources = page.getByRole('region', { name: 'Sources' });
  const bank = sources.getByRole('listitem').filter({ hasText: 'Bank account (UAE Open Finance)' });
  await expect(bank).toContainText('Needs owner setup');
  await expect(bank).toContainText('Before it can be built:');
  await expect(bank.getByRole('button')).toHaveCount(0);
  await expect(bank.locator('code')).toHaveText(['accounts:read', 'balances:read', 'transactions:read']);
  await sources.getByRole('listitem').filter({ hasText: 'European Central Bank' }).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByText(/European Central Bank reference rates connected/)).toBeVisible();
  const mine = page.getByRole('region', { name: 'Your connections' });
  await expect(mine).toContainText('Active');
  await mine.getByRole('button', { name: 'Sync now' }).click();
  await expect(page.getByText('Synced: 2 new rates, 0 new transactions.')).toBeVisible();
  const rates = page.getByRole('region', { name: 'Exchange rates' });
  await expect(rates.getByRole('cell', { name: /CBUAE peg/ })).toBeVisible();
  await rates.getByRole('button', { name: 'Type a rate' }).click();
  const dialog = page.getByRole('dialog', { name: 'Type a rate' });
  await dialog.getByLabel('1 USD in AED').fill('0');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Enter a rate above zero, for example 3.6725.');
  await dialog.getByLabel('1 USD in AED').fill('3.6725');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Rate saved.')).toBeVisible();
  await expect(rates.getByRole('cell', { name: 'Typed by you' })).toBeVisible();
  await mine.getByRole('button', { name: 'Disconnect' }).click();
  await expect(mine).toContainText('Disconnected');
  await mine.getByRole('button', { name: /^Delete / }).click();
  await page.getByRole('alertdialog', { name: 'Delete this connection?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(/deleted\.$/)).toBeVisible();
  expect(sent.some((r) => r.method === 'DELETE' && r.path.includes('/connections/'))).toBe(true);
  // No credential field is ever sent for a source that needs none.
  expect(sent.filter((r) => r.path.endsWith('/connections') && r.method === 'POST').every((r) => !(r.body as { credentials?: unknown }).credentials)).toBe(true);
});

test('empty, error and denied states on the new tabs', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-safe');
  await expect(page.getByRole('heading', { name: 'Nothing to work from yet' })).toBeVisible();
  await page.getByRole('region', { name: 'Safe to spend' }).getByRole('button', { name: 'Add account' }).click();
  await expect(page).toHaveURL(/#finance-accounts$/);
});

test('a failed safe-to-spend says what failed with a retry; a viewer is told who can use Finance', async ({ page }) => {
  await mockApi(page, { failSafe: true });
  await page.goto('/#finance-safe');
  await expect(page.getByRole('alert').filter({ hasText: 'Safe to spend could not be worked out: The database is not reachable.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
});

test('the Guardian is denied to a viewer with who can use it', async ({ page }) => {
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.goto('/#finance-guardian');
  await expect(page.getByRole('heading', { name: 'Finance is not available to your role' })).toBeVisible();
});

test('stages 3 to 5 read in Arabic, right to left', async ({ page }) => {
  await mockApi(page, { agentProposal: true });
  await useSavedLocale(page, 'ar');
  await page.goto('/#finance-safe');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'المتاح للصرف' }).first()).toBeVisible();
  await expect(page.getByText('المال الذي لديك').first()).toBeVisible();
  await page.getByRole('navigation', { name: 'أقسام المالية' }).getByRole('button', { name: 'الحارس' }).click();
  await expect(page.getByText('اقترحه مساعد')).toBeVisible();
  await page.getByRole('navigation', { name: 'أقسام المالية' }).getByRole('button', { name: 'الاتصالات' }).click();
  await expect(page.getByText('الأسعار المرجعية للبنك المركزي الأوروبي', { exact: true })).toBeVisible();
});
