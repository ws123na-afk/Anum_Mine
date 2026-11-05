// Mocked Finance Holdings data (docs/finance.md#holdings) for finance-holdings.spec.ts: an investment account,
// manual holdings and recorded prices on top of the shared finance mock. Synthetic values only.
import type { Page } from '@playwright/test';
import { financeReply, financeState } from './financeApi';

const pad = (n: number) => String(n).padStart(2, '0');
const day = (offset: number) => { const d = new Date(); d.setDate(d.getDate() + offset); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const stamp = (offset: number) => `${day(offset)}T08:00:00Z`;

export interface HoldingsOptions { empty?: boolean; noAccount?: boolean; fail?: boolean; denied?: boolean; slow?: boolean; many?: boolean }

export function seedHoldings(o: HoldingsOptions) {
  const state = financeState({ denied: o.denied, role: o.denied ? 'viewer' : 'member' });
  const broker = { id: 'fin_acct_broker', name: 'Example Brokerage', kind: 'investment', currency: 'SAR', institution: 'Example Broker', last4: null, opening_balance: '0.00', balance: '9620.00', archived: false, transaction_count: 3, created_at: stamp(-90), updated_at: stamp(-1) };
  if (!o.noAccount) state.accounts.push(broker);
  const holding = (id: string, symbol: string, name: string, quantity: string, cost: string, created: number) => ({ id, account_id: broker.id, symbol, name, quantity, cost_basis: cost, currency: 'SAR', notes: null, created_at: stamp(created), updated_at: stamp(created) });
  state.holdings = o.empty || o.noAccount ? [] : [
    holding('fin_h_etf', 'BRDETF', 'Broad market ETF', '120.00000000', '11400.00', -60),
    holding('fin_h_fix', 'FIXINC', 'Fixed income fund', '77.20000000', '7334.00', -50),
    holding('fin_h_gold', 'GOLDX', 'Gold holding', '42.00000000', '3990.00', -40),
    holding('fin_h_new', 'NEWFND', 'New fund', '10.00000000', '1000.00', -2),
  ];
  if (o.many) for (let i = 0; i < 30; i++) state.holdings.push(holding(`fin_h_m${i}`, `SYM${i}`, `Example fund ${i}`, '1.00000000', '10.00', -30));
  const price = (id: string, symbol: string, value: string, offset: number) => ({ id, symbol, currency: 'SAR', price: value, as_of: day(offset), source: 'manual', recorded_at: stamp(offset) });
  state.trading.prices = [
    price('p1', 'BRDETF', '95.0000000000', -60), price('p2', 'BRDETF', '98.0000000000', -30), price('p3', 'BRDETF', '100.0000000000', 0),
    price('p4', 'FIXINC', '99.0000000000', -30), price('p5', 'FIXINC', '100.0000000000', 0),
    price('p6', 'GOLDX', '97.0000000000', -30), price('p7', 'GOLDX', '100.0000000000', -10),
  ];
  return state;
}

export async function mockHoldingsApi(page: Page, o: HoldingsOptions = {}) {
  const state = seedHoldings(o);
  const role = o.denied ? 'viewer' : 'member';
  const sent: { method: string; path: string; body: unknown }[] = [];
  let failures = o.fail ? 1 : 0;
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
    if (path === '/api/v1/finance/holdings' && request.method() === 'GET') {
      if (o.slow) await new Promise((r) => setTimeout(r, 600));
      if (failures > 0) { failures -= 1; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
    }
    if (path === '/api/v1/finance/prices' && request.method() === 'POST') {
      const input = body as { symbol: string; currency: string; price: string; as_of: string };
      const saved = { id: `p_new_${state.trading.prices.length}`, ...input, source: 'manual', recorded_at: new Date().toISOString() };
      state.trading.prices.unshift(saved);
      return json({ price: saved, orders_changed: 0 }, 201);
    }
    if (path === '/api/v1/finance/holdings' && request.method() === 'POST') {
      const created = { id: 'fin_h_added', currency: 'SAR', notes: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...(body as object) };
      state.holdings.push(created);
      return json(created, 201);
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

