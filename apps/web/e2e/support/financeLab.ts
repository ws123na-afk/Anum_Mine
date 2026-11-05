// Mocked answers for Finance, Paper trading and portfolio lab (docs/finance.md#paper-trading-lab) on top of the
// shared finance mock: the trading intelligence overview, strategies and a run, the IBKR overview and a research
// report. Synthetic values only; every figure is SIMULATED.
import type { Page } from '@playwright/test';
import { NO_RULES, financeReply, financeState } from './financeApi';

const stamp = '2026-11-04T15:00:00Z';
export interface LabOptions { connected?: boolean; empty?: boolean; failIntel?: boolean; denied?: boolean; failTrading?: boolean }

const strategy = (id: string, name: string, state: string) => ({
  id, lineage_id: `lin_${id}`, key: 'trend_sma', code_version: '1.0.0', name, version: 1, state, symbols: ['EXMPL'], params: { fast: 20, slow: 50 }, reason: 'First idea.',
  portfolio_id: null, parent_id: null, evidence_run_id: null, created_by: 'person', state_changed_by: 'person', state_changed_at: stamp, created_at: stamp, updated_at: stamp, simulated: true,
});

function ibkrOverview(connected: boolean) {
  const check = (code: string, ok: boolean, message: string) => ({ code, ok, message });
  return {
    mode: 'paper', deployment_live_enabled: false, settings: {}, connections: connected ? [{
      id: 'fin_ibkr_1', environment: 'paper', gateway_host: 'ibkr-gateway', hint: 'DU...567', status: 'active', session: 'expired', session_message: 'login_needed',
      session_checked_at: stamp, verified_at: stamp, trading_type: 'STKCASH', last_sync_at: stamp, last_error: null, statements_connected: false, created_at: stamp,
    }] : [], snapshots: {}, statements: {},
    live_gates: [check('deployment_flag', false, 'Off.'), check('live_broker', false, 'Not named.'), check('live_connection', false, 'Not connected.'), check('paper_proof', false, 'Not complete.'),
      check('limits', true, 'Set.'), check('reconciliation', true, 'Match.'), check('kill_switches', true, 'Off.'), check('owner_switch', false, 'Not on.'), check('cooling_off', false, 'Not started.')],
    switch_gates: [], paper_proof: [], market: { open: false, message: 'Closed.', closes_at: null }, pending: [], unread_alerts: 0, gateway_hosts: ['ibkr-gateway'], etfs: {}, shariah_etfs: [],
    approval_minutes: 5, paper_min_orders: 20, paper_min_days: 14, risk_note: 'Real money can be lost.',
  };
}

const fact = (value: string | null, source: string | null, asOf: string | null, unit: string | null) => ({ value, unit, source, as_of: asOf, unavailable: null });

function report() {
  return {
    symbol: 'SPUS', name: 'SP Funds S&P 500 Sharia Industry Exclusions ETF', mode: 'paper', gathered_at: stamp, expires_at: stamp, disclaimer: 'Research for your own decisions. It is not financial advice.',
    shariah: { screened: true, label: 'Shariah-screened', screen: null, issuer: null, source: null, note: '' },
    prices: { source: 'stooq', last_close: fact('48.12', 'stooq', '2026-10-30', 'usd'), unavailable: null }, returns: {}, drawdowns: { source: null, items: [], unavailable: null },
    volatility: { yearly: fact(null, null, null, null), worst_month: fact(null, null, null, null), best_month: fact(null, null, null, null), months: 0 },
    fund: { source: null, unavailable: null, issuer: fact(null, null, null, null), holdings_count: fact(null, null, null, null), net_assets: fact(null, null, null, null), expense_ratio: fact(null, null, null, null), top_holdings: { items: [], source: null, as_of: null } },
    dividends: { fund_history: fact(null, null, null, null), yield: fact(null, null, null, null), yours: { items: [], source: 'ibkr_flex', as_of: null, unavailable: 'Not read.' }, us_withholding_percent: '30', withholding_note: '' },
    news: { items: [], source: null, unavailable: null, untrusted: true },
    sources: [{ key: 'stooq', name: 'Stooq daily prices', url: 'https://stooq.com', used: true }, { key: 'ibkr_history', name: 'IBKR historical prices', url: null, used: false }],
    source_problems: [{ source: 'sec_nport', reason: 'Not reachable.' }], data_hash: 'h',
    summary: { status: 'ok', reason: null, unknown_numbers: [], label: 'AI summary', created_at: stamp, current: true,
      summary: { good: [], bad: [], history: 'Over the last year the fund rose 14.2 percent, with a largest fall of 9.4 percent in one month.', range: '', suggestion: 'wait', reasons: [] } },
  };
}

export async function mockLabApi(page: Page, o: LabOptions = {}) {
  const state = financeState({ empty: o.empty, failTrading: o.failTrading, denied: o.denied, role: o.denied ? 'viewer' : 'member' });
  const role = o.denied ? 'viewer' : 'member';
  const sent: { method: string; path: string; body: unknown }[] = [];
  const strategies = o.empty ? [] : [strategy('s1', 'Trend filter', 'PAPER_ACTIVE'), strategy('s2', 'Rebalancing rules', 'DRAFT'), strategy('s3', 'Momentum', 'PAPER_SUSPENDED')];
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
    const denied = () => json({ error: { code: 'permission_denied', message: 'You need finance:read.', correlation_id: 'c', details: [] } }, 403);
    if (path === '/api/v1/finance/intel/overview') {
      if (o.failIntel) return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503);
      return json({
        simulation: { simulation_only: true, live_enabled: false, wider_live_enabled: false, note: '' }, switches: { person_engaged: false, global_engaged: false, can_change_global: false },
        venues: [], portfolios: [], strategies: [{ state: 'PAPER_ACTIVE', count: 1 }, { state: 'PAPER_SUSPENDED', count: 1 }, { state: 'DRAFT', count: 1 }],
        risk: { version: 1, rules: { ...NO_RULES, max_single: [{ currency: 'USD', amount: '500.00' }], max_daily_total: [{ currency: 'USD', amount: '5000.00' }] }, paper_always_needs_approval: true, read_only: true, note: '' },
        pending_requests: 0, agent_requests: 0, note: '', generated_at: stamp,
      });
    }
    if (path === '/api/v1/finance/intel/strategies') return json({ items: strategies, total: strategies.length, limit: 5, offset: 0 });
    if (/\/intel\/strategies\/[^/]+\/runs$/.test(path)) {
      const items = path.includes('/s1/') ? [{ id: 'run_1', strategy_id: 's1', kind: 'backtest', created_at: stamp, data_source: 'csv_import', data_hash: 'h', code_version: '1.0.0', symbols: ['EXMPL'], costs: {},
        metrics: { total_return: 0.342, max_drawdown: -0.08, sharpe: 1.1, trades: 14 }, benchmark: { total_return: 0.216 }, verdict: 'completed', reasons: [], warnings: [], folds: [], reproduced: true, requested_by: 'person', simulated: true }] : [];
      return json({ items, total: items.length, limit: 1, offset: 0 });
    }
    if (path === '/api/v1/finance/ibkr/overview') return o.denied ? denied() : json(ibkrOverview(o.connected !== false));
    if (path === '/api/v1/finance/ibkr/connections/fin_ibkr_1/check') return json({ ...ibkrOverview(true).connections[0], session: 'authenticated' });
    if (path === '/api/v1/finance/research/symbols') return o.denied ? denied() : json({ mode: 'paper', symbols: o.connected === false ? [] : [{ symbol: 'SPUS', name: 'SP Funds', allowed: true, shariah: report().shariah }], etfs: {}, shariah_etfs: [], limits: { max_order: '10', max_capital: '50' }, summary: { daily_limit: 10 }, disclaimer: 'Research for your own decisions. It is not financial advice.', plans: [] });
    if (path === '/api/v1/finance/research/symbols/SPUS') return json(report());
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}
