import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// IBKR trading (docs/finance-live-trading.md): connect, account and P&L from IBKR, the limit order ticket,
// approvals showing the exact order, the kill switch, the daily loss explanation, Shariah labels, the
// "log in again" state, loading, error with retry, permission denied, Arabic and the responsive gate.
// The API is mocked at http://localhost:8000/** like the other specs; real IBKR is never involved.
const stamp = '2026-11-04T15:00:00Z';
const HASH = 'a'.repeat(64);

interface Options { connected?: boolean; expired?: boolean; denied?: boolean; failFirst?: boolean; mismatch?: boolean }

function overview(o: Options, state: { pending: boolean; killed: boolean }) {
  const connection = {
    id: 'fin_ibkr_1', environment: 'paper', gateway_host: 'ibkr-gateway', hint: 'DU...567', status: 'active', session: o.expired ? 'expired' : 'authenticated',
    session_message: o.expired ? 'login_needed' : null, session_checked_at: stamp, verified_at: stamp, trading_type: 'STKCASH', last_sync_at: stamp,
    last_error: null, statements_connected: true, created_at: stamp,
  };
  const check = (code: string, ok: boolean, message: string) => ({ code, ok, message });
  return {
    mode: 'paper', deployment_live_enabled: false,
    settings: {
      limits: { max_capital: '50', max_order: '10', max_daily_loss: '5', max_orders_per_day: 3, price_band_percent: '2', session_buffer_minutes: 15, fee_buffer: '1' },
      allowed: [{ symbol: 'SPUS', conid: 1001, name: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', exchange: 'ARCA', shariah_screened: true }],
      buy_plans: [{ id: 'fin_plan_1', symbol: 'SPUS', amount: '10.00', day_of_month: 5, active: true, last_proposed: null }],
      fractional_ok: false, cooling_off_hours: 24,
      live: { on: false, requested_at: null, switched_on_at: null, effective_at: null, switched_off_at: null },
      kill_switch: state.killed, kill_switch_at: state.killed ? stamp : null,
      reconciliation: { paper: { state: o.mismatch ? 'mismatch' : 'ok', checked_at: stamp, baseline_at: stamp, episodes: o.mismatch ? 1 : 0, explained: 0,
        mismatches: o.mismatch ? [{ code: 'unknown_order', ref: '9', message: 'IBKR has an open order on an allowed ETF that ANUM did not send.' }] : [] } },
      version: 3, updated_at: stamp,
    },
    connections: o.connected === false ? [] : [connection],
    snapshots: o.connected === false ? {} : { paper: {
      account_ok: true, cash: { USD: '40.50' }, settled_cash: { USD: '40.50' }, net_liquidation: '49.80', daily_pnl: '-0.42', unrealized_pnl: '-0.70',
      realized_pnl: '0.00', synced_at: stamp,
      positions: [{ conid: 1001, symbol: 'SPUS', quantity: '1', market_price: '9.30', market_value: '9.30', average_cost: '10.00', unrealized_pnl: '-0.70', realized_pnl: '0', currency: 'USD' }],
      open_orders: [],
    } },
    statements: o.connected === false ? {} : { paper: { ready: true, dividends_received: '0.42', withholding_tax: '-0.13', trades: 2 } },
    live_gates: [check('deployment_flag', false, 'The operator setting ANUM_FINANCE_LIVE_ENABLED is off.'), check('paper_proof', false, 'The paper period is not complete yet.'),
      check('kill_switches', !state.killed, 'No kill switch is on.')],
    switch_gates: [check('deployment_flag', false, 'The operator setting ANUM_FINANCE_LIVE_ENABLED is off.')],
    paper_proof: [check('paper_orders', false, '3 of 20 paper orders reached IBKR.'), check('paper_days', false, '2 of 14 days since the first paper order.'),
      check('paper_mismatches', true, 'No unexplained mismatches.')],
    market: { open: true, message: 'The US regular session is open.', closes_at: null },
    pending: state.pending ? [{ id: 'fin_int_1', environment: 'paper', mode: 'paper', symbol: 'SPUS', side: 'buy', order_type: 'limit', quantity: '1', limit_price: '9.3',
      amount: '9.30', currency: 'USD', status: 'pending_approval', payload_hash: HASH, created_at: stamp, expires_at: new Date(Date.now() + 300_000).toISOString(),
      requested_by: 'person', reason: null, refusal: null, refusal_code: null, estimate: null }] : [],
    unread_alerts: 1, gateway_hosts: ['ibkr-gateway', '127.0.0.1', 'localhost'],
    etfs: { SPUS: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', HLAL: 'Wahed FTSE USA Shariah ETF', SPY: 'SPDR S&P 500 ETF Trust' },
    shariah_etfs: ['HLAL', 'SPUS'], approval_minutes: 5, paper_min_orders: 20, paper_min_days: 14,
    risk_note: 'Real money can be lost. Figures come from IBKR\'s own records; ANUM never predicts or promises earnings.',
  };
}

async function mockApi(page: Page, o: Options = {}) {
  const state = { pending: true, killed: false };
  const finance = financeState({});
  const calls: { method: string; path: string; body: unknown }[] = [];
  let failures = o.failFirst ? 1 : 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'owner', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (!path.startsWith('/api/v1/finance/ibkr')) {
      const reply = financeReply(finance, request.method(), url, body);
      if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
      return json({}, 404);
    }
    calls.push({ method: request.method(), path, body });
    if (o.denied) return json({ error: { code: 'forbidden', message: 'Your role is viewer. Ask a workspace owner to change your role to member to use finance.', correlation_id: 'c', details: [] } }, 403);
    if (path.endsWith('/overview')) {
      if (failures > 0) { failures -= 1; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      return json(overview(o, state));
    }
    if (path.endsWith('/events')) {
      const items = [{ id: 'e1', environment: 'paper', kind: 'order_filled', severity: 'info', message: 'Filled: buy 1 SPUS at 9.30, average price 9.30.', ref: 'fin_int_0', alert: true, created_at: stamp, read_at: null }];
      return json({ items, total: 1, unread: 1, limit: 20, offset: 0 });
    }
    if (path.endsWith('/orders') && request.method() === 'GET') return json({ items: [], total: 0, limit: 20, offset: 0 });
    if (path.endsWith('/quote')) return json({ symbol: 'SPUS', last: '9.30', bid: '9.29', ask: '9.31', delayed: true, availability: 'D', age_seconds: 2, band_percent: '2' });
    if (path.endsWith('/intents/fin_int_1/approve')) {
      state.pending = false;
      return json({ order: { id: 'fin_int_1', state: 'submitted', message: null, environment: 'paper', symbol: 'SPUS', side: 'buy', quantity: '1', limit_price: '9.3' } });
    }
    if (path.endsWith('/orders') && request.method() === 'POST') return json({ ...overview(o, state).pending[0], id: 'fin_int_2' }, 201);
    if (path.endsWith('/kill-switch')) { state.killed = true; return json({ kill_switch: true, report: [{ environment: 'paper', orders: [{ order_id: '1', cancelled: true }] }] }); }
    if (path.endsWith('/connections') && request.method() === 'POST') return json({ id: 'fin_ibkr_1', verified_at: stamp }, 201);
    return json({});
  });
  return { calls };
}

const panel = (page: Page) => page.getByRole('region', { name: /Interactive Brokers/ });

test('the paper account shows IBKR figures with unrealised and realised apart, and the daily loss stop never sells', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-ibkr');
  await expect(panel(page)).toContainText('PAPER');
  const account = page.getByRole('region', { name: /Account and positions/ });
  await expect(account).toContainText('Unrealised (not taken)');
  await expect(account).toContainText('Realised (taken by selling)');
  await expect(account).toContainText('Dividends received');
  await expect(account).toContainText('Not reported by IBKR or the fund issuer');
  await expect(page.getByRole('region', { name: /Your hard limits/ })).toContainText('It never sells anything you hold');
  await expect(page.getByRole('region', { name: /Allowed ETFs/ })).toContainText('Shariah-screened by its issuer');
  await expect(page.getByRole('region', { name: /Alerts/ })).toContainText('Filled: buy 1 SPUS');
});

test('an order is approved with the exact order and its hash, and the ticket labels delayed prices', async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto('/#finance-ibkr');
  const ticket = page.getByRole('region', { name: /Order ticket/ });
  await ticket.getByRole('button', { name: 'Get IBKR price' }).click();
  await expect(ticket).toContainText('Delayed (about 15 minutes old');
  await expect(ticket.getByLabel('Limit price (USD)')).toHaveValue('9.30');
  await ticket.getByRole('button', { name: 'Make order for approval' }).click();
  await expect.poll(() => calls.some((c) => c.method === 'POST' && c.path.endsWith('/orders'))).toBe(true);
  expect(calls.find((c) => c.method === 'POST' && c.path.endsWith('/orders'))?.body).toEqual({ symbol: 'SPUS', side: 'buy', quantity: '1', limit_price: '9.30' });
  const approvals = page.getByRole('region', { name: /Waiting for your approval/ });
  await approvals.getByRole('button', { name: 'Review' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Approve this paper order?' });
  await expect(dialog).toContainText('Limit, one day, regular hours');
  await expect(dialog).toContainText('SPUS');
  await dialog.getByRole('button', { name: 'Approve and send' }).click();
  await expect.poll(() => calls.some((c) => c.path.endsWith('/approve'))).toBe(true);
  expect(calls.find((c) => c.path.endsWith('/approve'))?.body).toEqual({ payload_hash: HASH });
  await expect(page.getByText('Approved and sent to IBKR.')).toBeVisible();
});

test('the kill switch asks first, then cancels at IBKR and reports it', async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto('/#finance-ibkr');
  await panel(page).getByRole('button', { name: 'Stop everything' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Stop all trading?' });
  await expect(dialog).toContainText('Nothing you already own is sold');
  await dialog.getByLabel('Reason (optional)').fill('Testing the stop');
  await dialog.getByRole('button', { name: 'Stop everything' }).click();
  await expect(page.getByText('Stopped. 1 open orders at IBKR were cancelled and confirmed.')).toBeVisible();
  expect(calls.find((c) => c.path.endsWith('/kill-switch'))?.body).toEqual({ reason: 'Testing the stop' });
  await expect(panel(page).getByRole('button', { name: 'Allow orders again' })).toBeVisible();
});

test('an ended IBKR session says log in again; a mismatch shows why and asks for an explanation', async ({ page }) => {
  await mockApi(page, { expired: true, mismatch: true });
  await page.goto('/#finance-ibkr');
  await expect(page.getByRole('alert').filter({ hasText: 'Log in to IBKR again.' })).toBeVisible();
  const recon = page.getByRole('region', { name: /Records match/ });
  await expect(recon).toContainText('Do not match');
  await expect(recon).toContainText('did not send');
  await expect(recon.getByRole('button', { name: /Explain and accept/ })).toBeDisabled();
});

test('not connected: the paper connect form never asks for a password and sends the pinned fingerprint', async ({ page }) => {
  const { calls } = await mockApi(page, { connected: false });
  await page.goto('/#finance-ibkr');
  await expect(page.getByText('Connect your IBKR paper account first')).toBeVisible();
  await page.getByRole('region', { name: 'Your IBKR connection' }).getByRole('button', { name: 'Connect' }).click();
  const dialog = page.getByRole('dialog', { name: 'Connect your IBKR paper account' });
  await expect(dialog.getByLabel(/password/i)).toHaveCount(0);
  await dialog.getByLabel(/certificate fingerprint/).fill(`${'AB:'.repeat(31)}AB`);
  await dialog.getByLabel(/Paper account id/).fill('du1234567');
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect(dialog.getByRole('alert')).toContainText('cash account');
  await dialog.getByLabel(/This is a cash account/).check();
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect.poll(() => calls.some((c) => c.method === 'POST' && c.path.endsWith('/connections'))).toBe(true);
  const sent = calls.find((c) => c.method === 'POST' && c.path.endsWith('/connections'))?.body as Record<string, unknown>;
  expect(sent).toMatchObject({ environment: 'paper', tls_fingerprint: 'ab'.repeat(32), account_id: 'DU1234567', cash_account_confirmed: true });
});

test('load error retries, and permission denied explains who can', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#finance-ibkr');
  await expect(page.getByRole('alert')).toContainText('The database is not reachable');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(panel(page)).toContainText('PAPER');
});

test('permission denied', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-ibkr');
  await expect(page.locator('#fin-denied')).toBeVisible();
});

test('Arabic: right to left with the same badges and the daily loss explanation', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-ibkr');
  await expect(page.getByRole('region', { name: /إنتراكتيف بروكرز/ })).toContainText('تجريبي');
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(page.getByText(/لا يبيع أبدا شيئا تملكه/)).toBeVisible();
  const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
  expect(serious).toEqual([]);
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: the IBKR tab fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await mockApi(page);
      await page.goto('/#finance-ibkr');
      const names = locale === 'ar'
        ? { panel: /إنتراكتيف بروكرز/, stop: 'أوقف كل شيء', make: 'إنشاء أمر للموافقة', price: 'جلب سعر IBKR' }
        : { panel: /Interactive Brokers/, stop: 'Stop everything', make: 'Make order for approval', price: 'Get IBKR price' };
      const header = page.getByRole('region', { name: names.panel });
      await expect(header.getByRole('button', { name: names.stop })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      for (const control of [header.getByRole('button', { name: names.stop }), page.getByRole('button', { name: names.make }), page.getByRole('button', { name: names.price })]) {
        await control.scrollIntoViewIfNeeded();
        const box = await control.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
      }
      const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`ibkr-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
