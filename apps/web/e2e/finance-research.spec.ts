import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Fund research (docs/finance-research.md): every figure with its source and date, "Not available" with
// the reason, Shariah labels, the AI summary (shown, and withheld for a number not in the data), a backtest
// with the simply-holding comparison and the warning, the paper run, loading, error with retry, permission
// denied, Arabic and the responsive gate. The API is mocked at http://localhost:8000/**; nothing real is called.
const stamp = '2026-11-04T15:00:00Z';
const fact = (value: string | null, source: string | null, asOf: string | null, unit: string | null, unavailable: string | null = null) =>
  ({ value, source, as_of: asOf, unavailable, unit });
const missing = (reason: string) => fact(null, null, null, null, reason);
const shariah = { screened: true, label: 'Shariah-screened', screen: 'S&P 500 Shariah Industry Exclusions index (S&P Dow Jones Indices) and the issuer\'s Shariah adviser',
  issuer: 'SP Funds', source: 'Fund name and issuer as published', note: 'Screened by the fund\'s index provider and the issuer\'s own Shariah adviser. ANUM does not certify any fund.' };

function symbols(mode = 'paper') {
  return {
    mode, etfs: { SPUS: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', SPY: 'SPDR S&P 500 ETF Trust' }, shariah_etfs: ['SPUS'],
    limits: { max_order: '10', max_capital: '50' }, summary: { daily_limit: 10 }, plans: [],
    disclaimer: 'Research from public and broker data, for your own decisions. It is not financial advice and there are no guaranteed returns. Prices go down as well as up.',
    symbols: [
      { symbol: 'SPUS', name: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', allowed: true, shariah },
      { symbol: 'HLAL', name: 'Wahed FTSE USA Shariah ETF', allowed: false, shariah: { ...shariah, issuer: 'Wahed Invest', screen: 'FTSE USA Shariah index (FTSE Russell) and the issuer\'s Shariah board' } },
      { symbol: 'SPY', name: 'SPDR S&P 500 ETF Trust', allowed: true, shariah: { screened: false, label: 'Not Shariah-screened', screen: null, issuer: null, source: null, note: 'This fund is not on the Shariah-screened list. ANUM does not certify any fund.' } },
    ],
  };
}

function report(summary: unknown = null) {
  const pct = (v: string, asOf: string) => fact(v, 'stooq', asOf, 'percent');
  return {
    symbol: 'SPUS', name: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', mode: 'paper', shariah, gathered_at: stamp, expires_at: '2026-11-04T15:30:00Z',
    disclaimer: symbols().disclaimer,
    prices: { source: 'stooq', label: 'Stooq daily closes', adjusted: 'Adjustment for dividends not stated by the source', from: '2019-12-18', to: '2026-10-30', points: 1730,
      fetched_at: stamp, last_close: fact('48.12', 'stooq', '2026-10-30', 'usd'), unavailable: null },
    returns: {
      '1y': { total: pct('14.2', '2025-10-30 to 2026-10-30'), yearly: pct('14.2', '2025-10-30 to 2026-10-30') },
      '3y': { total: pct('52.0', '2023-10-30 to 2026-10-30'), yearly: pct('15.0', '2023-10-30 to 2026-10-30') },
      '5y': { total: pct('81.3', '2021-10-29 to 2026-10-30'), yearly: pct('12.6', '2021-10-29 to 2026-10-30') },
      '10y': { total: missing('The price history starts on 2019-12-18, less than 10 years before 2026-10-30.'), yearly: missing('The price history starts on 2019-12-18, less than 10 years before 2026-10-30.') },
    },
    drawdowns: { source: 'stooq', unavailable: null, items: [
      { peak_date: '2020-02-19', peak: '21.9', trough_date: '2020-03-23', trough: '15.1', fall_percent: '-31.1', recovered_date: '2020-07-20', months_to_recover: 4, months_falling: 1 },
      { peak_date: '2021-12-27', peak: '38.4', trough_date: '2022-10-12', trough: '27.0', fall_percent: '-29.7', recovered_date: null, months_to_recover: null, months_falling: 10 },
    ] },
    volatility: { yearly: pct('17.9', '2019-12-31 to 2026-10-30'), worst_month: pct('-9.4', '2020-03'), best_month: pct('11.2', '2020-11'), months: 82 },
    fund: { source: 'sec_nport', url: 'https://www.sec.gov/Archives/edgar/data/1801234/000180123426000001/primary_doc.xml', filed: '2026-10-19', unavailable: null,
      issuer: fact('SP Funds Trust', 'sec_nport', '2026-09-30', null), holdings_count: fact('218', 'sec_nport', '2026-09-30', 'count'),
      net_assets: fact('1612345678', 'sec_nport', '2026-09-30', 'usd'),
      expense_ratio: missing('Not available: no free machine-readable source for the expense ratio is wired; the fund\'s prospectus states it.'),
      top_holdings: { items: [{ name: 'Example Corp One', percent: '12.25' }, { name: 'Example Corp Two', percent: '7.50' }], source: 'sec_nport', as_of: '2026-09-30' } },
    dividends: { fund_history: missing('Not available: no free source for the fund\'s own dividend history is wired.'), yield: missing('Not available: no free source for the fund\'s own dividend history is wired.'),
      yours: { items: [], source: 'ibkr_flex', as_of: null, unavailable: 'Your IBKR Flex statement is not connected or not read yet.' },
      us_withholding_percent: '30', withholding_note: 'For a Saudi tax resident, US dividends are normally taxed 30 percent at source (no US income tax treaty).' },
    news: { items: [{ title: 'Example headline about Shariah ETFs', url: 'https://news.example.com/a', source: 'news.example.com', published: '2026-10-30' }], source: 'news', unavailable: null, untrusted: true },
    sources: [
      { key: 'ibkr_history', name: 'IBKR historical prices (your connection)', url: null, used: false },
      { key: 'stooq', name: 'Stooq daily prices (free, personal use)', url: 'https://stooq.com', used: true },
      { key: 'sec_nport', name: 'SEC EDGAR N-PORT fund reports', url: 'https://www.sec.gov/edgar/search/', used: true },
    ],
    source_problems: [{ source: 'ibkr_history', reason: 'IBKR is not connected, so its price history was not used.' }],
    data_hash: 'a'.repeat(64), summary,
  };
}

const okSummary = { status: 'ok', reason: null, unknown_numbers: [], current: true, created_at: stamp,
  label: 'AI summary written by a model from the figures on this page only. It may be wrong. It is not financial advice and promises no return.',
  summary: { good: ['Back to its high in 4 months after the -31.1 percent fall.'], bad: ['The expense ratio is not available.'],
    history: 'Up 81.3 percent over 5 years.', range: 'Past results do not predict the future; a fall like -31.1 percent can happen again.', suggestion: 'wait',
    reasons: ['Buy small amounts monthly rather than all at once.'] } };

function backtest() {
  return {
    ok: true, rule: 'monthly', symbols: ['SPUS'], from: '2021-10-29', to: '2026-10-30', warning: 'Past results do not predict the future. This replays a simple rule on past monthly prices; real prices, fills, fees and taxes differ. It is not financial advice and promises no return.',
    result: { final_value: '71.40', put_in: '50.00', cash_left: '3.10', gain: '21.40', total_return_percent: '42.8', worst_fall_percent: '-12.4', worst_fall_from: '2022-01-31',
      worst_fall_to: '2022-09-30', months: 61, months_below_put_in: 3, fees: '1.10', dividends_net: '0.00', tax_withheld: '0.00', orders: 4, skipped_buys: 1, shares: { SPUS: 2 },
      timeline: [{ month: '2021-10', value: '10.00', put_in: '10.00' }, { month: '2022-10', value: '45.00', put_in: '50.00' }, { month: '2026-10', value: '71.40', put_in: '50.00' }] },
    simply_holding: { final_value: '76.00', put_in: '50.00', total_return_percent: '52.0', worst_fall_percent: '-25.1', months_below_put_in: 12, fees: '1.00' },
    dividends_included: false, dividends_note: 'Dividends are not included: the price sources give no dividend history.',
    assumptions: { whole_shares_only: true, price: 'Each trade at the month\'s last close.', commission: { per_share: '0.005', minimum: '1.00', max_percent: '1' }, withholding_percent: '30', per_order_limit: '10', total_limit: '50' },
    shariah: { SPUS: true }, paper_run: { available: true, reason: null },
  };
}

interface Options { denied?: boolean; failFirst?: boolean; withheld?: boolean; live?: boolean }

async function mockApi(page: Page, o: Options = {}) {
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
    if (!path.startsWith('/api/v1/finance/research')) {
      const reply = financeReply(finance, request.method(), url, body);
      if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
      return json({}, 404);
    }
    calls.push({ method: request.method(), path: path + url.search, body });
    if (o.denied) return json({ error: { code: 'forbidden', message: 'Your role is viewer. Ask a workspace owner to change your role to member to use finance.', correlation_id: 'c', details: [] } }, 403);
    if (path.endsWith('/symbols')) {
      if (failures > 0) { failures -= 1; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      return json(symbols(o.live ? 'live' : 'paper'));
    }
    if (path.endsWith('/portfolio')) return json({ mode: 'paper', connected: true, synced_at: stamp, net_liquidation: '49.80', daily_pnl: '-0.42', unrealized_pnl: '-0.70', realized_pnl: '0.00', positions: [] });
    if (path.endsWith('/summary')) {
      return json(o.withheld ? { status: 'withheld', reason: 'number_not_in_data', unknown_numbers: ['87.3'], summary: null, current: true, created_at: stamp, label: okSummary.label } : okSummary);
    }
    if (path.endsWith('/backtests')) return json(backtest());
    if (path.endsWith('/paper-run')) return json({ plan: { id: 'fin_plan_9', symbol: 'SPUS', amount: '10', day_of_month: 5, active: true, last_proposed: null, paper_only: true, origin: 'research_backtest' }, mode: 'paper' }, 201);
    if (path.includes('/symbols/')) return json(report());
    return json({});
  });
  return { calls };
}

const main = (page: Page) => page.getByRole('region', { name: /Fund research/ });

test('every figure shows its source and date, missing ones say why, and every fund carries its Shariah label', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-research');
  await expect(main(page)).toContainText('PAPER');
  await expect(main(page)).toContainText('not financial advice and there are no guaranteed returns');
  await expect(main(page).getByRole('button', { name: /SPUS/ })).toContainText('Shariah-screened');
  await expect(main(page).getByRole('button', { name: /SPY/ })).toContainText('Not Shariah-screened');
  const prices = page.getByRole('region', { name: 'Price history' });
  await expect(prices).toContainText('USD 48.12');
  await expect(prices).toContainText('Source: Stooq daily prices (free, personal use), 2026-10-30');
  await expect(prices).toContainText('less than 10 years');
  const falls = page.getByRole('region', { name: 'Worst falls' });
  await expect(falls).toContainText('-31.1%');
  await expect(falls).toContainText('Not back yet');
  const facts = page.getByRole('region', { name: 'Fund facts' });
  await expect(facts).toContainText('218');
  await expect(facts).toContainText('USD 1,612,345,678');
  await expect(facts).toContainText('Not available');
  await expect(page.getByRole('region', { name: 'Dividends' })).toContainText('taxed 30 percent at source');
  await expect(page.getByRole('region', { name: 'Recent news' })).toContainText('ANUM has not checked them');
  await expect(page.getByRole('region', { name: 'Sources' })).toContainText('IBKR is not connected');
});

test('the AI summary shows the good, the bad and a suggestion, and a summary with an unknown number is withheld', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-research');
  const ai = page.getByRole('region', { name: 'AI summary' });
  await ai.getByRole('button', { name: 'Write AI summary' }).click();
  await expect(ai).toContainText('Wait');
  await expect(ai).toContainText('Past results do not predict the future');
  await expect(ai).toContainText('It is not financial advice');
});

test('a withheld summary says which numbers were not in the data', async ({ page }) => {
  await mockApi(page, { withheld: true });
  await page.goto('/#finance-research');
  const ai = page.getByRole('region', { name: 'AI summary' });
  await ai.getByRole('button', { name: 'Write AI summary' }).click();
  await expect(ai).toContainText('numbers that are not in the data (87.3)');
});

test('a backtest shows the result against simply holding with the warning, and makes a paper-only plan', async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto('/#finance-research');
  const bt = page.getByRole('region', { name: 'Test a rule on past prices' });
  await bt.getByLabel('Most per order (USD)').fill('20');
  await bt.getByRole('button', { name: 'Run test' }).click();
  await expect(bt.getByRole('alert')).toContainText('USD 10');
  await bt.getByLabel('Most per order (USD)').fill('10');
  await bt.getByRole('button', { name: 'Run test' }).click();
  await expect(bt).toContainText('Past results do not predict the future');
  await expect(bt).toContainText('+42.8%');
  await expect(bt).toContainText('+52.0%');
  await expect(bt).toContainText('3 of 61');
  expect(calls.find((c) => c.path.endsWith('/backtests'))?.body).toMatchObject({ rule: 'monthly', symbols: ['SPUS'], per_order_limit: '10', total_limit: '50' });
  await bt.getByRole('button', { name: 'Create paper plan' }).click();
  await expect(bt).toContainText('Paper plan created');
  expect(calls.find((c) => c.path.endsWith('/paper-run'))?.body).toEqual({ symbol: 'SPUS', amount: '10', day_of_month: 5 });
});

test('load error retries, and permission denied explains who can', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#finance-research');
  await expect(page.getByRole('alert')).toContainText('The database is not reachable');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(main(page)).toContainText('PAPER');
});

test('permission denied', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-research');
  await expect(page.locator('#fin-denied')).toBeVisible();
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: the research tab fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await mockApi(page);
      await page.goto('/#finance-research');
      const names = locale === 'ar'
        ? { panel: /بحث الصناديق/, run: 'شغل الاختبار', write: 'اكتب ملخصا', refresh: 'تحديث' }
        : { panel: /Fund research/, run: 'Run test', write: 'Write AI summary', refresh: 'Refresh' };
      await expect(page.getByRole('region', { name: names.panel })).toBeVisible();
      if (locale === 'ar') expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
      await page.getByRole('button', { name: names.run }).click();
      await expect(page.locator('.fresResult')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      for (const control of [page.getByRole('button', { name: names.run }), page.getByRole('button', { name: names.write }), page.locator('section[aria-labelledby="fres-fund"]').getByRole('button', { name: names.refresh })]) {
        await control.scrollIntoViewIfNeeded();
        const box = await control.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
      }
      const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`research-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
