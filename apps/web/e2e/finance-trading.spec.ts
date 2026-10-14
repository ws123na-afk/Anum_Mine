import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance stages 6 to 8 (docs/finance.md): SIMULATED paper and shadow trading against recorded
// prices, every request checked by the Guardian rules and run only after the person approves the
// code shown; kill switches; limited live, which is off and refuses every live request. Plus the
// empty, error and permission-denied states and Arabic.
async function mockApi(page: Page, options: Parameters<typeof financeState>[0] = {}) {
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

test('a simulated order runs only after the person reviews it and approves the code shown', async ({ page }) => {
  const { sent, state } = await mockApi(page);
  await page.goto('/#finance-trading');
  await expect(page.getByRole('heading', { name: 'Paper trading', level: 2 })).toBeVisible();
  await expect(page.getByRole('note').first()).toContainText('SIMULATED');
  await expect(page.getByRole('note').first()).toContainText('say nothing certain');
  const detail = page.getByRole('region', { name: /Practice/ });
  await expect(detail.getByRole('table', { name: 'Simulated positions in Practice' })).toContainText('EXMPL');
  await expect(detail).toContainText('fee 0.10% of the order value');

  await detail.getByLabel('Symbol').fill('exmpl');
  await detail.getByLabel('Quantity').fill('0');
  await detail.getByRole('button', { name: 'Check and review' }).click();
  await expect(detail.getByRole('alert')).toHaveText('Enter a quantity above zero with at most 8 decimals.');
  await detail.getByLabel('Quantity').fill('3');
  await detail.getByRole('button', { name: 'Check and review' }).click();
  const review = page.getByRole('dialog', { name: 'Review the request' });
  await expect(review).toContainText('Buy 3 EXMPL');
  await expect(review).toContainText('SIMULATED');
  await expect(review).toContainText('Your rules allow it');
  await expect(review).toContainText('Fill price after slippage');
  await expect(review.getByText(/Code to approve/)).toBeVisible();
  // Nothing ran before the approval.
  expect(state.trading.orders).toHaveLength(1);
  await review.getByRole('button', { name: 'Approve this simulated order' }).click();
  await expect(page.getByText('Approved and filled in the simulation. No real money moved.')).toBeVisible();
  const approve = sent.find((r) => r.path.endsWith('/approve'));
  expect((approve?.body as { payload_hash: string }).payload_hash).toMatch(/^[0-9a-f]{64}$/);
  const created = sent.find((r) => r.method === 'POST' && r.path.endsWith('/execution/intents'));
  expect((created?.body as { idempotency_key: string; mode: string }).mode).toBe('paper');
  expect((created?.body as { idempotency_key: string }).idempotency_key).toMatch(/^web-/);
  await expect(detail.getByRole('list').filter({ hasText: 'Buy 3 EXMPL' })).toContainText('Filled');
});

test('rules that block an order are shown and the order cannot be approved', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-trading');
  const detail = page.getByRole('region', { name: /Practice/ });
  await detail.getByLabel('Symbol').fill('BLOCKED');
  await detail.getByLabel('Quantity').fill('1');
  await detail.getByRole('button', { name: 'Check and review' }).click();
  const review = page.getByRole('dialog', { name: 'Review the request' });
  await expect(review).toContainText('Your rules block it: it cannot run');
  await expect(review).toContainText('Blocked by your rules');
  await expect(review.getByRole('button', { name: 'Approve this simulated order' })).toHaveCount(0);
  await review.getByRole('button', { name: 'Close' }).click();
  await detail.getByLabel('Symbol').fill('NOPRICE');
  await detail.getByRole('button', { name: 'Check and review' }).click();
  await expect(page.getByRole('dialog', { name: 'Review the request' }).getByRole('alert')).toContainText('No recent price is recorded for this symbol');
});

test('an assistant proposal waits for the person, who can reject it', async ({ page }) => {
  await mockApi(page, { agentProposal: true });
  await page.goto('/#finance-trading');
  const waiting = page.getByRole('region', { name: '1 request waits for your approval' });
  await expect(waiting).toContainText('Proposed by an assistant');
  await waiting.getByRole('button', { name: 'Review' }).click();
  const review = page.getByRole('dialog', { name: 'Review the request' });
  await expect(review).toContainText('only you can approve it');
  await expect(review).toContainText('Your rules ask for your approval');
  await review.getByRole('button', { name: 'Reject' }).click();
  await expect(page.getByText('Rejected. Nothing ran.')).toBeVisible();
});

test('prices are recorded, a portfolio is created and reset, and waiting orders can be cancelled', async ({ page }) => {
  const { sent } = await mockApi(page, { empty: true });
  await page.goto('/#finance-trading');
  await expect(page.getByRole('heading', { name: 'No practice portfolio yet' })).toBeVisible();
  await expect(page.getByText('No prices recorded yet.')).toBeVisible();
  await page.getByRole('button', { name: 'New portfolio' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New portfolio' });
  await dialog.getByLabel('Name').fill('Practice');
  await dialog.getByLabel('Fee (basis points)').fill('2000');
  await dialog.getByRole('button', { name: 'Create portfolio' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Enter basis points from 0 to 1000, with at most 2 decimals.');
  await dialog.getByLabel('Fee (basis points)').fill('10');
  await dialog.getByRole('button', { name: 'Create portfolio' }).click();
  await expect(page.getByText('Portfolio created. It is simulated: no real money is used.')).toBeVisible();

  const prices = page.getByRole('region', { name: 'Prices you recorded' });
  await prices.getByLabel('Symbol', { exact: true }).fill('EXMPL');
  await prices.getByLabel('Price', { exact: true }).fill('100');
  await prices.getByRole('button', { name: 'Record price' }).click();
  await expect(prices.getByText('Price recorded.')).toBeVisible();
  expect(sent.some((r) => r.method === 'POST' && r.path.endsWith('/prices'))).toBe(true);

  const detail = page.getByRole('region', { name: /Practice/ });
  await detail.getByLabel('Symbol').fill('EXMPL');
  await detail.getByLabel('Quantity').fill('2');
  await detail.getByLabel('Order type').selectOption('limit');
  await detail.getByLabel('Limit price').fill('90');
  await detail.getByRole('button', { name: 'Check and review' }).click();
  await page.getByRole('dialog', { name: 'Review the request' }).getByRole('button', { name: 'Approve this simulated order' }).click();
  await expect(page.getByText('Approved. The simulated order waits for a price that meets its limit.')).toBeVisible();
  await detail.getByRole('button', { name: /^Cancel Buy 2 EXMPL/ }).click();
  await expect(detail.getByText('Order cancelled.')).toBeVisible();

  await detail.getByRole('button', { name: 'Reset' }).click();
  await page.getByRole('alertdialog', { name: 'Reset this portfolio?' }).getByRole('button', { name: 'Reset' }).click();
  await expect(page.getByText('Reset. A new simulated run has started.')).toBeVisible();
  await expect(detail.getByText('No orders in this run yet.')).toBeVisible();
});

test('kill switches stop requests, live is refused, and live limits need the person’s approval', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-execution');
  const kill = page.getByRole('region', { name: 'Kill switches' });
  await expect(kill.getByText('Only workspace owners and organisation administrators can change it.')).toBeVisible();
  await kill.getByRole('button', { name: 'Stop everything' }).first().click();
  await page.getByRole('alertdialog', { name: 'Turn the kill switch on?' }).getByRole('button', { name: 'Stop everything' }).click();
  await expect(kill.getByText('Kill switch on. Nothing runs.')).toBeVisible();
  expect(sent.some((r) => r.method === 'PUT' && (r.body as { scope: string; engaged: boolean }).engaged)).toBe(true);
  await kill.getByRole('button', { name: 'Allow again' }).click();
  await page.getByRole('alertdialog', { name: 'Turn the kill switch off?' }).getByRole('button', { name: 'Allow again' }).click();

  const executors = page.getByRole('region', { name: 'How a request runs' });
  await expect(executors.getByRole('listitem').filter({ hasText: 'Live' })).toContainText('Not enabled');
  const live = page.getByRole('region', { name: 'Limited live' });
  await expect(live).toContainText('Live execution is not enabled.');
  await expect(live.getByRole('listitem').filter({ hasText: 'A live executor for a licensed provider' })).toContainText('(not met)');
  await live.getByText('What the owner must provide before live could ever be switched on').click();
  await expect(live).toContainText('explicit written approval');
  await expect(live).toContainText('Wider live use (stage 9) is not started.');

  await live.getByRole('button', { name: 'Set limits' }).click();
  const editor = page.getByRole('dialog', { name: 'Your live limits' });
  await editor.getByRole('button', { name: 'Add a currency' }).first().click();
  await editor.getByLabel('Amount').first().fill('100');
  await editor.getByLabel('Currencies').fill('AED');
  await editor.getByLabel('Instruments').fill('EXMPL');
  await editor.getByRole('button', { name: 'Review change' }).click();
  const review = page.getByRole('dialog', { name: 'Review the request' });
  await expect(review).toContainText('Change of your live limits');
  await expect(review.getByRole('button', { name: 'Approve this simulated order' })).toHaveCount(0);
  await review.getByRole('button', { name: 'Approve this change' }).click();
  await expect(live.getByText('Limits saved. Live execution stays off.')).toBeVisible();
  await expect(live).toContainText('Your live limits (version 1)');
  await expect(live).toContainText('Live execution is not enabled.');
});

test('an owner can stop the whole organisation', async ({ page }) => {
  await mockApi(page, { role: 'owner' });
  await page.goto('/#finance-execution');
  const kill = page.getByRole('region', { name: 'Kill switches' });
  const org = kill.getByRole('listitem').filter({ hasText: 'Organisation kill switch' });
  await org.getByRole('button', { name: 'Stop everything' }).click();
  await page.getByRole('alertdialog', { name: 'Turn the kill switch on?' }).getByRole('button', { name: 'Stop everything' }).click();
  await expect(org).toContainText('On: stopped');
});

test('errors, permission denied and Arabic', async ({ page }) => {
  await mockApi(page, { failTrading: true });
  await page.goto('/#finance-trading');
  await expect(page.getByRole('alert')).toContainText('Paper trading could not be loaded');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.goto('/#finance-execution');
  await page.reload();
  await expect(page.getByText(/viewer to member/).first()).toBeVisible();

  await page.unrouteAll();
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-trading');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'التداول التجريبي', level: 2 })).toBeVisible();
  await expect(page.getByRole('note').first()).toContainText('محاكاة');
});

test('wider live use is off: the proof is measured, a step up is refused, and an incident freezes widening until reviewed', async ({ page }) => {
  const { state } = await mockApi(page);
  state.trading.widerFrozen = true;
  await page.goto('/#finance-execution');
  const wider = page.getByRole('region', { name: 'Wider live use' });
  await expect(wider).toContainText('Wider live use is not enabled.');
  await expect(wider.getByRole('listitem').filter({ hasText: 'Live actions carried out' })).toContainText('(not met)');
  await expect(wider.getByRole('alert')).toContainText('Widening is frozen after an incident');
  await expect(wider.getByRole('button', { name: 'Propose a step up' })).toBeDisabled();
  await wider.getByRole('button', { name: 'I have reviewed it' }).click();
  await expect(wider.getByText('Reviewed. Widening is no longer frozen')).toBeVisible();
  await wider.getByRole('button', { name: 'Propose a step up' }).click();
  const editor = page.getByRole('dialog', { name: 'Propose tier 1' });
  await editor.getByRole('button', { name: 'Review change' }).click();
  await expect(editor.getByRole('alert')).toContainText('Wider live use is not enabled');
});
