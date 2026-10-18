// Mocked /api/v1/finance answers (docs/finance.md) shared by finance.spec.ts and the workspace mock
// (accessibility, localization and page-layout specs). Synthetic values only: example merchants and
// round amounts, never real account numbers.
import { equityBody } from './financeEquity';
import { reportDetail, reportList } from './financeReports';
const now = new Date();
const pad = (n: number) => String(n).padStart(2, '0');
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = (offset: number) => { const d = new Date(now); d.setDate(d.getDate() + offset); return isoDay(d); };
const month = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
const stamp = now.toISOString();

export interface FinanceState {
  /** How many post-close daily reports the mocked API has (none unless a spec sets it). */
  reports?: number;
  settings: { base_currency: string; timezone: string; category_rules: { match: string; category: string }[]; updated_at: string | null };
  accounts: Record<string, unknown>[];
  transactions: Record<string, unknown>[];
  imports: Record<string, unknown>[];
  bills: Record<string, unknown>[];
  debts: Record<string, unknown>[];
  goals: Record<string, unknown>[];
  holdings: Record<string, unknown>[];
  denied: boolean;
  failSummary: boolean;
  // Stages 3 to 5.
  plan: { buffers: { currency: string; amount: string }[]; combine_currencies: boolean; fx_max_age_days: number; updated_at: string | null };
  guardian: { version: number; rules: Record<string, unknown>; confirmed_at: string | null };
  changes: Record<string, unknown>[];
  versions: Record<string, unknown>[];
  connections: Record<string, unknown>[];
  rates: Record<string, unknown>[];
  failSafe: boolean;
  // Stages 6 to 8: SIMULATED paper and shadow trading, the gateway, limited live (off).
  trading: TradingState;
}

export interface TradingState {
  portfolios: Record<string, unknown>[];
  orders: Record<string, unknown>[];
  prices: Record<string, unknown>[];
  intents: Record<string, unknown>[];
  personSwitch: boolean;
  globalSwitch: boolean;
  role: 'owner' | 'member' | 'viewer';
  live: { limits: Record<string, unknown>; version: number; opt_in: boolean };
  failTrading: boolean;
  widerFrozen: boolean;
}

export const NO_RULES = { allowed_currencies: [], max_single: [], max_daily_total: [], category_caps: [], blocked_categories: [], blocked_merchants: [], approval_above: [], quiet_hours: null, cooling_off: null };

const ASSUMPTIONS = { fee_bps: '10.00', fee_min: '1.00', slippage_bps: '5.00', max_price_age_days: 3 };
const NO_LIMITS = { max_per_action: [], max_per_day: [], max_per_month: [], instruments: [], accounts: [], currencies: [], cooling_off_hours: 24 };

export function tradingState({ empty = false, agentProposal = false, failTrading = false, role = 'member' }: { empty?: boolean; agentProposal?: boolean; failTrading?: boolean; role?: 'owner' | 'member' | 'viewer' } = {}): TradingState {
  const portfolio = { id: 'fin_port_practice', name: 'Practice', mode: 'paper', epoch: 1, assumptions: ASSUMPTIONS, starting_cash: [{ currency: 'USD', amount: '10000.00' }], created_at: stamp, reset_at: null };
  const filled = {
    id: 'fin_pord_1', portfolio_id: portfolio.id, intent_id: 'fin_int_done', epoch: 1, mode: 'paper', instrument_kind: 'security', symbol: 'EXMPL', currency: 'USD', side: 'buy',
    order_type: 'market', quantity: '10.00000000', limit_price: null, status: 'filled', reason_code: null, reason: null, requested_by: 'person', market_price: '100.0000000000',
    fill_price: '100.0500000000', price_as_of: day(0), price_source: 'manual', notional: '1000.50', fee: '1.00', slippage: '0.50', guardian_outcome: 'allow', guardian_codes: ['simulated_only'],
    created_at: stamp, filled_at: stamp,
  };
  return {
    portfolios: empty ? [] : [portfolio],
    orders: empty ? [] : [filled],
    prices: empty ? [] : [{ id: 'fin_price_1', symbol: 'EXMPL', currency: 'USD', price: '100.0000000000', as_of: day(0), source: 'manual', recorded_at: stamp }],
    intents: agentProposal && !empty ? [intentFor({ portfolio_id: portfolio.id, side: 'buy', quantity: '3', requested_by: 'agent', id: 'fin_int_agent' })] : [],
    personSwitch: false, globalSwitch: false, role, live: { limits: NO_LIMITS, version: 0, opt_in: false }, failTrading, widerFrozen: false,
  };
}

let intentCount = 0;
function intentFor(input: Record<string, unknown>): Record<string, unknown> {
  intentCount += 1;
  const quantity = Number(input.quantity ?? 1);
  const blocked = String(input.symbol ?? 'EXMPL').toUpperCase() === 'BLOCKED';
  const noPrice = String(input.symbol ?? 'EXMPL').toUpperCase() === 'NOPRICE' && input.order_type !== 'limit';
  const reasons = blocked
    ? [{ code: 'merchant_blocked', outcome: 'deny', message: 'You blocked the merchant blocked.', rule: 'blocked_merchants', params: { merchant: 'blocked' } }]
    : [...(input.requested_by === 'agent' ? [{ code: 'agent_request', outcome: 'needs_approval', message: '', rule: null, params: {} }] : []), { code: 'simulated_only', outcome: 'allow', message: '', rule: null, params: {} }];
  return {
    id: input.id ?? `fin_int_${intentCount}`, mode: input.mode ?? 'paper', kind: 'trade', portfolio_id: input.portfolio_id, portfolio_name: 'Practice', instrument_kind: input.instrument_kind ?? 'security',
    symbol: String(input.symbol ?? 'EXMPL').toUpperCase(), currency: input.currency ?? 'USD', side: input.side ?? 'buy', order_type: input.order_type ?? 'market', quantity: String(input.quantity ?? '1'),
    limit_price: input.limit_price ?? null, amount: null, account_id: null, live_profile: null, requested_by: input.requested_by ?? 'person', reason: null,
    payload_hash: `${intentCount}`.padEnd(64, 'd'), status: noPrice ? 'refused' : blocked ? 'denied' : 'pending_approval',
    decision: noPrice ? {} : { outcome: blocked ? 'deny' : input.requested_by === 'agent' ? 'needs_approval' : 'allow', reasons }, guardian_version: 1,
    estimate: noPrice ? null : { market_price: '100.0000000000', fill_price: '100.0500000000', notional: (quantity * 100.05).toFixed(2), fee: '1.00', slippage: (quantity * 0.05).toFixed(2), price_as_of: day(0), price_source: 'manual', fee_bps: '10.00', fee_min: '1.00', slippage_bps: '5.00', limit_met: true, simulated: true, note: 'SIMULATED.' },
    refusal_code: noPrice ? 'no_price' : null, refusal: noPrice ? 'No price for NOPRICE in USD is recorded in the last 3 days.' : null,
    created_at: stamp, expires_at: new Date(now.getTime() + 30 * 60_000).toISOString(), approved_at: null, executed_at: null, result: {}, simulated: true,
  };
}

export function financeState({ empty = false, denied = false, failSummary = false, failSafe = false, agentProposal = false, failTrading = false, role = 'member' }: { empty?: boolean; denied?: boolean; failSummary?: boolean; failSafe?: boolean; agentProposal?: boolean; failTrading?: boolean; role?: 'owner' | 'member' | 'viewer' } = {}): FinanceState {
  const account = { id: 'fin_acct_current', name: 'Everyday account', kind: 'bank', currency: 'AED', institution: 'Example Bank', last4: '1234', opening_balance: '1000.00', balance: '15349.50', archived: false, transaction_count: 64, created_at: stamp, updated_at: stamp };
  const card = { ...account, id: 'fin_acct_card', name: 'Rewards card', kind: 'card', last4: '9876', opening_balance: '0.00', balance: '-2450.00', transaction_count: 12 };
  const merchants = [
    ['CARREFOUR CITY CENTRE', 'groceries', '-120.50'], ['DEWA UTILITY', 'utilities', '-450.00'], ['TALABAT ORDER', 'dining', '-35.00'],
    ['ENOC STATION', 'fuel', '-90.00'], ['SALARY OCTOBER', 'income', '15000.00'], ['NETFLIX.COM', 'subscriptions', '-39.00'],
  ] as const;
  const transactions = empty ? [] : Array.from({ length: 64 }, (_, i) => {
    const [description, category, amount] = merchants[i % merchants.length];
    return {
      id: `fin_txn_${i}`, account_id: account.id, posted_on: day(-(i % 28)), amount, currency: 'AED', description: i === 0 ? '=HYPERLINK(example)' : description,
      category, category_source: 'rule', notes: null, import_id: 'fin_imp_1', created_at: stamp, updated_at: stamp,
    };
  });
  return {
    settings: { base_currency: 'AED', timezone: 'Asia/Dubai', category_rules: [], updated_at: null },
    accounts: empty ? [] : [account, card],
    transactions,
    imports: empty ? [] : [{ id: 'fin_imp_1', account_id: account.id, filename: 'october.csv', preset: 'emirates_nbd', date_format: '%d/%m/%Y', row_count: 65, imported_count: 64, duplicate_count: 0, error_count: 1, created_at: stamp }],
    bills: empty ? [] : [
      { id: 'fin_bill_rent', name: 'Rent', amount: '6500.00', currency: 'AED', cadence: 'monthly', next_due_on: day(3), category: 'housing', account_id: account.id, source: 'manual', active: true, notes: null, created_at: stamp, updated_at: stamp },
      { id: 'fin_bill_dewa', name: 'DEWA', amount: '450.00', currency: 'AED', cadence: 'monthly', next_due_on: day(12), category: 'utilities', account_id: null, source: 'detected', active: true, notes: null, created_at: stamp, updated_at: stamp },
    ],
    debts: empty ? [] : [{ id: 'fin_debt_card', name: 'Rewards card', kind: 'card', balance: '2450.00', currency: 'AED', apr_percent: '39.9900', minimum_payment: '250.00', due_day: 25, account_id: card.id, notes: null, created_at: stamp, updated_at: stamp }],
    goals: empty ? [] : [{ id: 'fin_goal_fund', name: 'Emergency fund', target_amount: '30000.00', saved_amount: '7500.00', currency: 'AED', target_on: null, account_id: null, notes: null, created_at: stamp, updated_at: stamp }],
    holdings: [],
    denied,
    failSummary,
    failSafe,
    plan: { buffers: empty ? [] : [{ currency: 'AED', amount: '1000.00' }], combine_currencies: false, fx_max_age_days: 7, updated_at: null },
    guardian: { version: empty ? 0 : 1, rules: empty ? NO_RULES : { ...NO_RULES, category_caps: [{ category: 'dining', currency: 'AED', amount: '400.00' }], blocked_merchants: ['casino'] }, confirmed_at: empty ? null : stamp },
    changes: agentProposal ? [{
      id: 'fin_gchg_agent', base_version: 1, rules: { ...NO_RULES, category_caps: [{ category: 'dining', currency: 'AED', amount: '300.00' }], blocked_merchants: ['casino'] },
      proposed_by: 'agent', proposer: 'assistant', reason: 'You asked to cut dining', payload_hash: 'a1b2'.repeat(16), loosens: [], status: 'pending',
      created_at: stamp, expires_at: new Date(now.getTime() + 23 * 3_600_000).toISOString(), decided_at: null,
    }] : [],
    versions: empty ? [] : [{ id: 'fin_gpol_1', version: 1, rules: NO_RULES, change_id: 'c0', confirmed_by: 'user_local', created_at: stamp }],
    connections: [],
    rates: [],
    trading: tradingState({ empty, agentProposal, failTrading, role }),
  };
}

const PROVIDERS = [
  { id: 'ecb_fx', kind: 'fx', name: 'European Central Bank reference rates', summary: 'Daily euro reference rates.', scopes: ['fx_rates:read'], available: true, needs_credentials: false, credential_fields: [], sync_hours: 12, source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml', terms: 'Reuse is free with the source named.', owner_action: null },
  { id: 'uae_open_finance_bank', kind: 'bank', name: 'Bank account (UAE Open Finance)', summary: 'Balances and transactions, read only.', scopes: ['accounts:read', 'balances:read', 'transactions:read'], available: false, needs_credentials: true, credential_fields: [], sync_hours: 12, source_url: null, terms: null, owner_action: 'Needs a licensed provider first.' },
  { id: 'crypto_read_only', kind: 'crypto', name: 'Crypto exchange (read only)', summary: 'Balances, read only.', scopes: ['balances:read'], available: false, needs_credentials: true, credential_fields: [], sync_hours: 12, source_url: null, terms: null, owner_action: 'Create a read-only key first.' },
];

function safeToSpend(state: FinanceState) {
  if (!state.accounts.length) {
    return { month, today: day(0), month_end: day(10), days_left: 11, base_currency: 'AED', timezone: 'Asia/Dubai', currencies: [], excluded: [], combine_currencies: false, combined_status: 'off', combined: null, missing_rates: [], fx_max_age_days: 7, accounts_counted: 0, note: 'An estimate from your own records: not advice and not a forecast.' };
  }
  const buffer = state.plan.buffers.find((b) => b.currency === 'AED')?.amount ?? '0.00';
  const lines = [
    { kind: 'balance', label: 'Everyday account', ref_id: 'fin_acct_current', currency: 'AED', amount: '15349.50', note: 'account_balance', params: {}, detail: '' },
    { kind: 'bill', label: 'Rent', ref_id: 'fin_bill_rent', currency: 'AED', amount: '-6500.00', note: 'bill_due', params: { date: day(3) }, detail: '' },
    { kind: 'minimum_payment', label: 'Rewards card', ref_id: 'fin_debt_card', currency: 'AED', amount: '-250.00', note: 'minimum_due', params: { day: '25' }, detail: '' },
    { kind: 'goal', label: 'Emergency fund', ref_id: 'fin_goal_fund', currency: 'AED', amount: '-1500.00', note: 'goal_derived', params: { left: '22500.00', months: '15' }, detail: '' },
    ...(Number(buffer) > 0 ? [{ kind: 'buffer', label: 'AED', ref_id: null, currency: 'AED', amount: `-${buffer}`, note: 'buffer', params: {}, detail: '' }] : []),
  ];
  const safe = (15349.5 - 6500 - 250 - 1500 - Number(buffer)).toFixed(2);
  const usd = { currency: 'USD', available: '400.00', bills: '0.00', minimum_payments: '0.00', goals: '0.00', buffer: '0.00', safe_to_spend: '400.00', per_day: '36.36', short: false,
    lines: [{ kind: 'balance', label: 'Travel wallet', ref_id: 'fin_acct_usd', currency: 'USD', amount: '400.00', note: 'account_balance', params: {}, detail: '' }] };
  const combined = state.plan.combine_currencies && state.rates.length > 0;
  return {
    month, today: day(0), month_end: day(10), days_left: 11, base_currency: 'AED', timezone: 'Asia/Dubai',
    currencies: [{ currency: 'AED', available: '15349.50', bills: '6500.00', minimum_payments: '250.00', goals: '1500.00', buffer, safe_to_spend: safe, per_day: (Math.floor((Number(safe) / 11) * 100) / 100).toFixed(2), short: false, lines }, usd],
    excluded: [{ kind: 'balance', label: 'Rewards card', ref_id: 'fin_acct_card', currency: 'AED', amount: '-2450.00', note: 'account_card', params: {}, detail: '' }],
    combine_currencies: state.plan.combine_currencies,
    combined_status: !state.plan.combine_currencies ? 'off' : combined ? 'ready' : 'missing_rates',
    combined: combined ? { currency: 'AED', safe_to_spend: (Number(safe) + 1469).toFixed(2), per_day: '600.00', conversions: [
      { from_currency: 'AED', to_currency: 'AED', amount: safe, rate: '1', converted: safe, as_of: day(0), source: 'same currency', via: null },
      { from_currency: 'USD', to_currency: 'AED', amount: '400.00', rate: '3.6725000000', converted: '1469.00', as_of: day(-1), source: 'ECB EUR/USD x CBUAE peg 3.6725 AED per USD', via: 'EUR' },
    ] } : null,
    missing_rates: state.plan.combine_currencies && !combined ? ['USD/AED'] : [], fx_max_age_days: 7, accounts_counted: 1,
    note: 'An estimate from your own records: not advice and not a forecast.',
  };
}

let changeCount = 0;

function portfolioView(state: TradingState, portfolio: Record<string, unknown>) {
  const filled = state.orders.filter((o) => o.portfolio_id === portfolio.id && o.status === 'filled' && o.epoch === portfolio.epoch);
  let cash = 10000, qty = 0, cost = 0;
  for (const o of filled) { const value = Number(o.notional) + Number(o.fee); if (o.side === 'buy') { cash -= value; qty += Number(o.quantity); cost += value; } }
  const shadow = portfolio.mode === 'shadow';
  const holdings = qty ? [{ instrument_kind: 'security', symbol: 'EXMPL', currency: 'USD', quantity: qty.toFixed(8), cost: cost.toFixed(2), average_price: (cost / qty).toFixed(10), price: '100.0000000000', price_as_of: day(0), price_source: 'manual', market_value: (qty * 100).toFixed(2), unrealised: (qty * 100 - cost).toFixed(2) }] : [];
  return {
    ...portfolio, cash: shadow ? [] : [{ currency: 'USD', amount: cash.toFixed(2) }], realised: [], unrealised: qty ? [{ currency: 'USD', amount: (qty * 100 - cost).toFixed(2) }] : [],
    positions: holdings.length, waiting: state.orders.filter((o) => o.portfolio_id === portfolio.id && o.status === 'waiting').length, simulated: true, holdings,
    fees: filled.length ? [{ currency: 'USD', amount: filled.reduce((s, o) => s + Number(o.fee), 0).toFixed(2) }] : [], slippage: filled.length ? [{ currency: 'USD', amount: filled.reduce((s, o) => s + Number(o.slippage), 0).toFixed(2) }] : [],
    unpriced: [], shadow: shadow ? filled.map((o) => ({ order_id: o.id, symbol: o.symbol, currency: o.currency, side: o.side, quantity: o.quantity, fill_price: o.fill_price, filled_at: o.filled_at, fee: o.fee, price: '104.0000000000', price_as_of: day(0), result: '38.50', days: 3 })) : [],
    shadow_totals: shadow && filled.length ? [{ currency: 'USD', amount: (38.5 * filled.length).toFixed(2) }] : [], note: 'SIMULATED.',
  };
}

function liveView(state: TradingState) {
  const limits = state.live.limits as { max_per_action: unknown[]; currencies: unknown[]; instruments: unknown[] };
  const set = limits.max_per_action.length > 0 && limits.currencies.length > 0;
  return {
    enabled: false, opt_in: state.live.opt_in, opt_in_at: null, limits: state.live.limits, version: state.live.version,
    preconditions: [
      { code: 'deployment_flag', met: false, message: '' }, { code: 'live_executor', met: false, message: '' }, { code: 'write_connector', met: false, message: '' },
      { code: 'opt_in', met: state.live.opt_in, message: '' }, { code: 'limits', met: set, message: '' },
      { code: 'kill_switch_global', met: !state.globalSwitch, message: '' }, { code: 'kill_switch_person', met: !state.personSwitch, message: '' },
    ],
    pending: state.intents.filter((i) => i.mode === 'settings' && i.status === 'pending_approval'), owner_steps: ['a', 'b', 'c', 'd', 'e'],
    note: 'Live execution is not enabled.',
  };
}

function executionStatus(state: TradingState) {
  return {
    global_switch: { engaged: state.globalSwitch, changed_at: state.globalSwitch ? stamp : null, changed_by_you: false, reason: null, can_change: state.role === 'owner' },
    person_switch: { engaged: state.personSwitch, changed_at: state.personSwitch ? stamp : null, changed_by_you: true, reason: null, can_change: true },
    executors: [
      { mode: 'paper', available: true, simulated: true, note: '' }, { mode: 'shadow', available: true, simulated: true, note: '' },
      { mode: 'live', available: false, simulated: false, note: 'Live execution is not enabled.' },
    ],
    live_enabled: false, approval_minutes: 30, pending: state.intents.filter((i) => i.status === 'pending_approval').length,
  };
}

function tradingReply(state: TradingState, method: string, sub: string, body: unknown, url: URL): { status: number; body: unknown } | null {
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const error = (status: number, code: string, message: string) => ({ status, body: { error: { code, message, correlation_id: 'c', details: [] } } });
  if (!sub.startsWith('/paper') && !sub.startsWith('/prices') && !sub.startsWith('/execution')) return null;
  if (state.failTrading && method === 'GET' && sub === '/paper/portfolios') return error(503, 'internal_error', 'The database is not reachable.');
  if (sub === '/prices' && method === 'GET') { const symbol = url.searchParams.get('symbol'); return ok(state.prices.filter((p) => !symbol || p.symbol === symbol)); }
  if (sub === '/prices' && method === 'POST') {
    const input = body as Record<string, string>;
    const price = { id: `fin_price_${state.prices.length + 1}`, ...input, symbol: input.symbol.toUpperCase(), source: 'manual', recorded_at: stamp };
    state.prices.unshift(price);
    let changed = 0;
    for (const o of state.orders) if (o.status === 'waiting' && Number(input.price) <= Number(o.limit_price)) { Object.assign(o, { status: 'filled', fill_price: input.price, notional: (Number(o.quantity) * Number(input.price)).toFixed(2), fee: '1.00', slippage: '0.00', filled_at: stamp, reason_code: null, reason: null }); changed += 1; }
    return ok({ price, orders_changed: changed }, 201);
  }
  if (sub.startsWith('/prices/') && method === 'DELETE') { state.prices = state.prices.filter((p) => p.id !== decodeURIComponent(sub.split('/')[2])); return ok(null, 204); }
  if (sub === '/paper/portfolios' && method === 'GET') return ok(state.portfolios.map((p) => portfolioView(state, p)));
  if (sub === '/paper/portfolios' && method === 'POST') {
    const input = body as Record<string, unknown>;
    const created = { id: `fin_port_${state.portfolios.length + 1}`, name: input.name, mode: input.mode, epoch: 1, assumptions: { fee_bps: Number(input.fee_bps).toFixed(2), fee_min: Number(input.fee_min).toFixed(2), slippage_bps: Number(input.slippage_bps).toFixed(2), max_price_age_days: input.max_price_age_days }, starting_cash: input.starting_cash, created_at: stamp, reset_at: null };
    state.portfolios.push(created);
    return ok(portfolioView(state, created), 201);
  }
  const port = /^\/paper\/portfolios\/([^/]+)(?:\/(orders|reset|check))?$/.exec(sub);
  if (port) {
    const found = state.portfolios.find((p) => p.id === decodeURIComponent(port[1]));
    if (!found) return error(404, 'not_found', 'Portfolio not found.');
    if (method === 'DELETE') { state.portfolios = state.portfolios.filter((p) => p !== found); state.orders = state.orders.filter((o) => o.portfolio_id !== found.id); return ok(null, 204); }
    if (port[2] === 'orders') {
      const status = url.searchParams.get('status'), all = url.searchParams.get('run') === 'all';
      const limit = Number(url.searchParams.get('limit') ?? 20), offset = Number(url.searchParams.get('offset') ?? 0);
      const items = state.orders.filter((o) => o.portfolio_id === found.id && (!status || o.status === status) && (all || o.epoch === found.epoch)).map((o) => ({ ...o, simulated: true }));
      return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
    }
    if (port[2] === 'reset') {
      found.epoch = Number(found.epoch) + 1; found.reset_at = stamp;
      for (const o of state.orders) if (o.portfolio_id === found.id && o.status === 'waiting') Object.assign(o, { status: 'cancelled', reason_code: 'reset' });
      return ok(portfolioView(state, found));
    }
    if (port[2] === 'check') return ok(portfolioView(state, found));
    if (method === 'PATCH') { const input = body as Record<string, unknown>; Object.assign(found, { name: input.name ?? found.name, assumptions: { ...(found.assumptions as object), fee_bps: input.fee_bps, slippage_bps: input.slippage_bps } }); }
    return ok(portfolioView(state, found));
  }
  const cancel = /^\/paper\/orders\/([^/]+)\/cancel$/.exec(sub);
  if (cancel) {
    const order = state.orders.find((o) => o.id === decodeURIComponent(cancel[1]));
    if (!order) return error(404, 'not_found', 'Order not found.');
    if (order.status !== 'waiting') return error(409, 'conflict', 'Only a waiting order can be cancelled.');
    Object.assign(order, { status: 'cancelled', reason_code: 'cancelled', reason: 'Cancelled by you.' });
    return ok({ ...order, simulated: true });
  }
  if (sub === '/execution/status') return ok(executionStatus(state));
  if (sub === '/execution/kill-switch' && method === 'PUT') {
    const input = body as { scope: 'person' | 'global'; engaged: boolean };
    if (input.scope === 'global' && state.role !== 'owner') return error(403, 'forbidden', 'Only workspace owners and organisation administrators can change the organisation\'s kill switch.');
    if (input.scope === 'global') state.globalSwitch = input.engaged; else state.personSwitch = input.engaged;
    return ok(executionStatus(state));
  }
  if (sub === '/execution/live' && method === 'GET') return ok(liveView(state));
  if (sub === '/execution/live/changes' && method === 'POST') {
    const input = body as { limits: Record<string, unknown>; opt_in?: boolean };
    if (input.opt_in) return error(409, 'conflict', 'Live execution is not enabled on this deployment, so you cannot opt in. You can set your limits now.');
    intentCount += 1;
    const change = { ...intentFor({ portfolio_id: null }), id: `fin_int_live_${intentCount}`, mode: 'settings', kind: 'live_profile', portfolio_name: null, symbol: null, side: null, quantity: null, order_type: null, currency: null, instrument_kind: null, estimate: null, decision: {}, simulated: false, live_profile: { base_version: state.live.version, limits: input.limits, opt_in: false } };
    state.intents.unshift(change);
    return ok(change, 201);
  }
  if (sub === '/execution/wider' || sub === '/execution/wider/review') {
    if (sub === '/execution/wider/review') state.widerFrozen = false;
    return ok({
      enabled: false, live_enabled: false, tier: 0, kinds: ['trade'], limits: state.live.limits, frozen: state.widerFrozen, frozen_at: state.widerFrozen ? stamp : null,
      frozen_reason: state.widerFrozen ? 'kill_switch_person' : null, graduated: false,
      criteria: [
        { code: 'stage8_ready', met: false, value: 'not met', needed: 'every stage 8 precondition' }, { code: 'days_live', met: false, value: '0', needed: '30' },
        { code: 'executed_actions', met: false, value: '0', needed: '20' }, { code: 'no_denials_overridden', met: true, value: '0', needed: '0' },
        { code: 'no_failed_executions', met: true, value: '0', needed: '0' }, { code: 'no_kill_switch_trips', met: !state.widerFrozen, value: state.widerFrozen ? '1' : '0', needed: '0' },
        { code: 'not_frozen', met: !state.widerFrozen, value: state.widerFrozen ? 'frozen' : 'open', needed: 'open' },
      ],
      ceilings: { max_tier: 3, step_percent: 50, cooling_hours: 72, window_days: 30 }, widenings: [],
      incidents: state.widerFrozen || sub === '/execution/wider/review' ? [{ id: 'fin_inc_1', code: 'kill_switch_person', tier_before: 0, tier_after: 0, created_at: stamp, reviewed_at: sub === '/execution/wider/review' ? stamp : null }] : [],
      note: 'Wider live use is not enabled.',
    });
  }
  if (sub === '/execution/wider/widenings' && method === 'POST') return error(409, 'conflict', 'Wider live use is not enabled. Live execution is not enabled either: nothing real runs, whatever the tier.');
  if (sub === '/execution/live/opt-out') { state.live.opt_in = false; return ok(liveView(state)); }
  if (sub === '/execution/intents' && method === 'GET') {
    const status = url.searchParams.get('status'), mode = url.searchParams.get('mode');
    const limit = Number(url.searchParams.get('limit') ?? 20), offset = Number(url.searchParams.get('offset') ?? 0);
    const items = state.intents.filter((i) => (!status || i.status === status) && (!mode || i.mode === mode));
    return ok({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
  }
  if (sub === '/execution/intents' && method === 'POST') {
    const input = body as Record<string, unknown>;
    if (input.mode === 'live') return error(409, 'conflict', 'Live execution is not enabled. ANUM places no real order, payment or transfer; nothing was sent.');
    if (state.personSwitch || state.globalSwitch) return error(409, 'conflict', 'Your kill switch is on: nothing runs until you turn it off under Finance, Paper trading.');
    const created = intentFor(input);
    state.intents.unshift(created);
    return ok(created, 201);
  }
  const act = /^\/execution\/intents\/([^/]+)\/(approve|reject)$/.exec(sub);
  if (act) {
    const intent = state.intents.find((i) => i.id === decodeURIComponent(act[1]));
    if (!intent) return error(404, 'not_found', 'Request not found.');
    if (intent.status !== 'pending_approval') return error(409, 'conflict', 'This request cannot be approved. Make a new one.');
    if (act[2] === 'reject') { intent.status = 'rejected'; return ok(intent); }
    if ((body as { payload_hash: string }).payload_hash !== intent.payload_hash) return error(409, 'conflict', 'The approval does not match the request shown. Reload it and approve again.');
    if (state.personSwitch || state.globalSwitch) return error(409, 'conflict', 'Your kill switch is on: nothing runs until you turn it off under Finance, Paper trading.');
    if (intent.kind === 'live_profile') {
      state.live = { limits: (intent.live_profile as { limits: Record<string, unknown> }).limits, version: state.live.version + 1, opt_in: false };
      Object.assign(intent, { status: 'executed', approved_at: stamp, executed_at: stamp, result: { live_version: state.live.version } });
      return ok(intent);
    }
    const limitWaits = intent.order_type === 'limit' && Number(intent.limit_price) < 100;
    const order = { id: `fin_pord_${state.orders.length + 2}`, portfolio_id: intent.portfolio_id, intent_id: intent.id, epoch: 1, mode: intent.mode, instrument_kind: intent.instrument_kind, symbol: intent.symbol, currency: intent.currency, side: intent.side, order_type: intent.order_type, quantity: Number(intent.quantity).toFixed(8), limit_price: intent.limit_price,
      status: limitWaits ? 'waiting' : 'filled', reason_code: limitWaits ? 'waiting_limit' : null, reason: limitWaits ? 'Waiting.' : null, requested_by: intent.requested_by, market_price: '100.0000000000', fill_price: limitWaits ? null : '100.0500000000', price_as_of: day(0), price_source: 'manual',
      notional: limitWaits ? null : (intent.estimate as { notional: string }).notional, fee: limitWaits ? null : '1.00', slippage: limitWaits ? null : '0.15', guardian_outcome: 'allow', guardian_codes: ['simulated_only'], created_at: stamp, filled_at: limitWaits ? null : stamp };
    state.orders.unshift(order);
    Object.assign(intent, { status: 'executed', approved_at: stamp, executed_at: stamp, result: { order_id: order.id, order_status: order.status, reason_code: order.reason_code, simulated: true } });
    return ok(intent);
  }
  return null;
}

function financeStagesReply(state: FinanceState, method: string, sub: string, body: unknown, url: URL): { status: number; body: unknown } | null {
  const trading = tradingReply(state.trading, method, sub, body, url);
  if (trading) return trading;
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const conflict = (message: string) => ({ status: 409, body: { error: { code: 'conflict', message, correlation_id: 'c', details: [] } } });
  if (sub === '/safe-to-spend') return state.failSafe ? { status: 503, body: { error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } } } : ok(safeToSpend(state));
  if (sub === '/plan') return method === 'PUT' ? ok((state.plan = { ...state.plan, ...(body as object), updated_at: stamp })) : ok(state.plan);
  const guardianView = () => ({ ...state.guardian, confirmed_by: 'user_local', pending: state.changes.filter((c) => c.status === 'pending'), kinds: ['spend', 'bill_payment', 'transfer', 'trade', 'record'] });
  if (sub === '/guardian') return ok(guardianView());
  if (sub === '/guardian/versions') return ok({ items: state.versions, total: state.versions.length, limit: 10, offset: 0 });
  if (sub === '/guardian/alerts') return ok({ month, version: state.guardian.version, alerts: state.guardian.version ? [{ code: 'cap_near', severity: 'near', message: '', rule: 'category_caps', params: { category: 'dining', currency: 'AED', spent: '350.00', limit: '400.00' } }] : [] });
  if (sub === '/guardian/changes' && method === 'POST') {
    const input = body as { rules: Record<string, unknown>; reason: string | null };
    changeCount += 1;
    const caps = (input.rules.category_caps as { amount: string }[] | undefined) ?? [];
    const before = (state.guardian.rules.category_caps as { amount: string }[] | undefined) ?? [];
    const loosens = before.length > caps.length || caps.some((c, i) => before[i] && Number(c.amount) > Number(before[i].amount)) ? ['category_caps'] : [];
    const change = { id: `fin_gchg_${changeCount}`, base_version: state.guardian.version, rules: input.rules, proposed_by: 'person', proposer: 'user_local', reason: input.reason, payload_hash: `${changeCount}`.padEnd(64, 'c'), loosens, status: 'pending', created_at: stamp, expires_at: new Date(now.getTime() + 24 * 3_600_000).toISOString(), decided_at: null };
    state.changes.unshift(change);
    return ok(change, 201);
  }
  const action = /^\/guardian\/changes\/([^/]+)\/(confirm|reject)$/.exec(sub);
  if (action && method === 'POST') {
    const change = state.changes.find((c) => c.id === decodeURIComponent(action[1]));
    if (!change) return { status: 404, body: { error: { code: 'not_found', message: 'Change not found.', correlation_id: 'c', details: [] } } };
    if (action[2] === 'reject') { change.status = 'rejected'; return ok(change); }
    if ((body as { payload_hash: string }).payload_hash !== change.payload_hash) return conflict('The confirmation does not match the change shown.');
    state.guardian = { version: state.guardian.version + 1, rules: change.rules as Record<string, unknown>, confirmed_at: stamp };
    state.versions.unshift({ id: `fin_gpol_${state.guardian.version}`, version: state.guardian.version, rules: change.rules, change_id: change.id, confirmed_by: 'user_local', created_at: stamp });
    for (const other of state.changes) if (other.status === 'pending') other.status = other === change ? 'confirmed' : 'superseded';
    change.status = 'confirmed';
    return ok(guardianView());
  }
  if (sub === '/guardian/evaluate') {
    const input = body as { amount: string; category?: string | null; merchant?: string | null };
    const deny = input.category === 'dining' && Number(input.amount) > 50;
    const rulesDecision = deny
      ? { outcome: 'deny', reasons: [{ code: 'over_category_cap', outcome: 'deny', message: '', rule: 'category_caps', params: { currency: 'AED', category: 'dining', spent: '350.00', limit: '400.00' } }] }
      : { outcome: 'allow', reasons: [] };
    return ok({ version: state.guardian.version, action: { kind: 'spend', amount: input.amount, currency: 'AED', category: input.category ?? null, merchant: input.merchant ?? null, at: stamp }, rules_decision: rulesDecision,
      decision: { outcome: 'deny', reasons: [...rulesDecision.reasons, { code: 'execution_not_available', outcome: 'deny', message: '', rule: null, params: {} }] }, note: 'A check only.' });
  }
  if (sub === '/connectors') return ok(PROVIDERS);
  if (sub === '/connections' && method === 'GET') return ok(state.connections);
  if (sub === '/connections' && method === 'POST') {
    const provider = PROVIDERS.find((p) => p.id === (body as { provider: string }).provider);
    if (!provider || !provider.available) return conflict('This source is not available yet.');
    const created = { id: `fin_conn_${state.connections.length + 1}`, provider: provider.id, provider_name: provider.name, kind: provider.kind, label: provider.name, status: 'active', scopes: provider.scopes, has_credentials: false, credential_hint: null, enabled: true, next_sync_at: stamp, last_sync_at: null, last_success_at: null, last_error: null, last_counts: {}, created_at: stamp, updated_at: stamp };
    state.connections.push(created);
    return ok(created, 201);
  }
  const conn = /^\/connections\/([^/]+)(?:\/(sync|disconnect))?$/.exec(sub);
  if (conn) {
    const found = state.connections.find((c) => c.id === decodeURIComponent(conn[1]));
    if (!found) return { status: 404, body: { error: { code: 'not_found', message: 'Connection not found.', correlation_id: 'c', details: [] } } };
    if (method === 'DELETE') { state.connections = state.connections.filter((c) => c !== found); state.rates = []; return ok(null, 204); }
    if (conn[2] === 'sync') {
      state.rates = [
        { id: 'fx_usd', base: 'EUR', quote: 'USD', rate: '1.1000000000', as_of: day(-1), source: 'European Central Bank euro reference rates', source_url: null, connection_id: found.id, fetched_at: stamp },
        { id: 'fx_aed', base: 'EUR', quote: 'AED', rate: '4.0397500000', as_of: day(-1), source: 'ECB EUR/USD x CBUAE peg 3.6725 AED per USD', source_url: null, connection_id: found.id, fetched_at: stamp },
        ...state.rates.filter((r) => r.source === 'manual'),
      ];
      Object.assign(found, { last_sync_at: stamp, last_success_at: stamp, last_counts: { rates_new: 2 } });
      return ok({ connection_id: found.id, status: 'ok', message: 'Synced.', counts: { rates_new: 2, rates_seen: 2, transactions_new: 0 } });
    }
    if (conn[2] === 'disconnect') { Object.assign(found, { status: 'disconnected', enabled: false, has_credentials: false }); return ok(found); }
    return ok(found);
  }
  if (sub === '/fx-rates' && method === 'GET') return ok(state.rates.slice(0, Number(url.searchParams.get('limit') ?? 100)));
  if (sub === '/fx-rates' && method === 'POST') {
    const input = body as { base: string; quote: string; rate: string; as_of: string };
    const created = { id: `fx_manual_${state.rates.length}`, ...input, source: 'manual', source_url: null, connection_id: null, fetched_at: stamp };
    state.rates.unshift(created);
    return ok(created, 201);
  }
  if (sub.startsWith('/fx-rates/') && method === 'DELETE') { state.rates = state.rates.filter((r) => r.id !== decodeURIComponent(sub.split('/')[2])); return ok(null, 204); }
  return null;
}

function summary(state: FinanceState, requested: string | null) {
  const label = requested ?? month;
  const rows = state.transactions.filter((t) => String(t.posted_on).startsWith(label));
  const spend: Record<string, { amount: number; count: number }> = {};
  let income = 0, out = 0;
  for (const row of rows) {
    const amount = Number(row.amount);
    if (amount > 0) income += amount;
    else { out -= amount; const key = String(row.category); spend[key] = { amount: (spend[key]?.amount ?? 0) - amount, count: (spend[key]?.count ?? 0) + 1 }; }
  }
  const money = (n: number) => n.toFixed(2);
  const upcoming = state.bills.map((b) => ({ id: b.id, name: b.name, amount: b.amount, currency: b.currency, cadence: b.cadence, due_on: b.next_due_on, days_until: Math.round((new Date(String(b.next_due_on)).getTime() - new Date(day(0)).getTime()) / 86_400_000), category: b.category }));
  return {
    month: label, start: `${label}-01`, end: `${label}-28`, today: day(0), base_currency: state.settings.base_currency, timezone: state.settings.timezone,
    accounts: state.accounts.map((a) => ({ id: a.id, name: a.name, kind: a.kind, currency: a.currency, institution: a.institution, last4: a.last4, balance: a.balance, archived: a.archived })),
    balances: state.accounts.length ? [{ currency: 'AED', amount: money(state.accounts.reduce((sum, a) => sum + Number(a.balance), 0)) }] : [],
    spend_by_category: Object.entries(spend).map(([category, v]) => ({ category, currency: 'AED', amount: money(v.amount), count: v.count })).sort((a, b) => Number(b.amount) - Number(a.amount)),
    income: income ? [{ currency: 'AED', amount: money(income) }] : [], spend: out ? [{ currency: 'AED', amount: money(out) }] : [],
    net: income || out ? [{ currency: 'AED', amount: money(income - out) }] : [], transaction_count: rows.length,
    upcoming_bills: upcoming, bills_due: upcoming.length ? [{ currency: 'AED', amount: money(upcoming.reduce((s, b) => s + Number(b.amount), 0)) }] : [],
    debts: state.debts.length ? [{ currency: 'AED', amount: '2450.00' }] : [], minimum_payments: state.debts.length ? [{ currency: 'AED', amount: '250.00' }] : [], debt_count: state.debts.length,
    goals: state.goals.map((g) => ({ id: g.id, name: g.name, currency: g.currency, target_amount: g.target_amount, saved_amount: g.saved_amount, percent: 25, target_on: g.target_on })),
  };
}

export const PREVIEW = {
  account_id: 'fin_acct_current', currency: 'AED', filename: 'november.csv', preset: 'emirates_nbd', headers: ['Transaction Date', 'Narration', 'Debit', 'Credit'],
  mapping: { date: 'Transaction Date', description: 'Narration', amount: null, debit: 'Debit', credit: 'Credit' }, date_format: '%d/%m/%Y', date_formats: ['%d/%m/%Y', '%m/%d/%Y'],
  date_format_ambiguous: true, row_count: 4, valid_count: 3, new_count: 2, duplicate_count: 1, error_count: 1,
  rows: [
    { line: 2, posted_on: day(0), amount: '-120.50', currency: 'AED', description: 'CARREFOUR CITY CENTRE', category: 'groceries', duplicate: true },
    { line: 3, posted_on: day(0), amount: '-60.00', currency: 'AED', description: 'SPINNEYS', category: 'groceries', duplicate: false },
    { line: 4, posted_on: day(0), amount: '15000.00', currency: 'AED', description: 'SALARY NOVEMBER', category: 'income', duplicate: false },
  ],
  errors: [{ line: 5, message: 'The date could not be read with the chosen date format.' }],
};

const denied = { status: 403, body: { error: { code: 'forbidden', message: 'Finance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.', correlation_id: 'c', details: [] } } };

/** The reply for one request, or null when the path is not a finance path. */
export function financeReply(state: FinanceState, method: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const path = url.pathname;
  if (!path.startsWith('/api/v1/finance')) return null;
  if (state.denied) return denied;
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const sub = path.slice('/api/v1/finance'.length);
  const stages = financeStagesReply(state, method, sub, body, url);
  if (stages) return stages;
  const equity = /^\/intel\/portfolios\/([^/]+)\/equity$/.exec(sub);
  if (equity && method === 'GET') return ok(equityBody(decodeURIComponent(equity[1]), Number(url.searchParams.get('days') ?? 90)));
  if (sub === '/intel/daily-reports' && method === 'GET') {
    const all = reportList(state.reports ?? 0), limit = Number(url.searchParams.get('limit') ?? 20), offset = Number(url.searchParams.get('offset') ?? 0);
    return ok({ items: all.slice(offset, offset + limit), total: all.length, limit, offset });
  }
  const report = /^\/intel\/daily-reports\/([^/]+)$/.exec(sub);
  if (report && method === 'GET') {
    const id = decodeURIComponent(report[1]);
    return reportList(state.reports ?? 0).some((r) => r.id === id) ? ok(reportDetail(id)) : ok({ error: { code: 'not_found', message: 'Report not found.', correlation_id: 'c', details: [] } }, 404);
  }
  if (sub === '/summary') return state.failSummary ? { status: 503, body: { error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } } } : ok(summary(state, url.searchParams.get('month')));
  if (sub === '/settings') return method === 'PUT' ? ok((state.settings = { ...state.settings, ...(body as object), updated_at: stamp })) : ok(state.settings);
  if (sub === '/formats') return ok({ presets: [{ id: 'emirates_nbd', label: 'Emirates NBD' }, { id: 'generic', label: 'Generic (date, description, amount)' }], date_formats: ['%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d'], categories: [], account_kinds: [], limits: { max_bytes: 2_000_000, max_rows: 5000, max_page: 200 } });
  if (sub === '/accounts' && method === 'GET') return ok(state.accounts);
  if (sub === '/accounts' && method === 'POST') {
    const input = body as Record<string, unknown>;
    const created = { id: `fin_acct_${state.accounts.length + 1}`, currency: 'AED', institution: null, last4: null, opening_balance: '0.00', archived: false, transaction_count: 0, created_at: stamp, updated_at: stamp, ...input, balance: input.opening_balance ?? '0.00' };
    state.accounts.push(created);
    return ok(created, 201);
  }
  if (sub.startsWith('/accounts/')) {
    const id = decodeURIComponent(sub.split('/')[2]);
    const found = state.accounts.find((a) => a.id === id);
    if (!found) return ok({ error: { code: 'not_found', message: 'Account not found.', correlation_id: 'c', details: [] } }, 404);
    if (method === 'DELETE') { state.accounts = state.accounts.filter((a) => a.id !== id); return ok(null, 204); }
    if (method === 'PATCH') { Object.assign(found, body); return ok(found); }
    return ok(found);
  }
  if (sub === '/transactions' && method === 'GET') {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const category = url.searchParams.get('category');
    const limit = Number(url.searchParams.get('limit') ?? 50), offset = Number(url.searchParams.get('offset') ?? 0);
    const found = state.transactions.filter((t) => (!q || String(t.description).toLowerCase().includes(q)) && (!category || t.category === category));
    return ok({ items: found.slice(offset, offset + limit), total: found.length, limit, offset });
  }
  if (sub === '/transactions' && method === 'POST') {
    const input = body as Record<string, unknown>;
    const created = { id: `fin_txn_new_${state.transactions.length}`, currency: 'AED', category: input.category ?? 'other', category_source: input.category ? 'manual' : 'rule', import_id: null, created_at: stamp, updated_at: stamp, ...input };
    state.transactions.unshift(created);
    return ok(created, 201);
  }
  if (sub.startsWith('/transactions/') && !sub.endsWith('.csv')) {
    const id = decodeURIComponent(sub.split('/')[2]);
    const found = state.transactions.find((t) => t.id === id);
    if (!found) return ok({ error: { code: 'not_found', message: 'Transaction not found.', correlation_id: 'c', details: [] } }, 404);
    if (method === 'DELETE') { state.transactions = state.transactions.filter((t) => t.id !== id); return ok(null, 204); }
    if (method === 'PATCH') { Object.assign(found, body, (body as { category?: string }).category ? { category_source: 'manual' } : {}); return ok(found); }
    return ok(found);
  }
  if (sub === '/transactions/export.csv') return { status: 200, body: 'date,account,description\n' };
  if (sub === '/imports/preview') return ok({ ...PREVIEW, filename: (body as { filename?: string }).filename ?? PREVIEW.filename });
  if (sub === '/imports' && method === 'POST') {
    const created = { id: `fin_imp_${state.imports.length + 1}`, account_id: 'fin_acct_current', filename: (body as { filename: string }).filename, preset: 'emirates_nbd', date_format: '%d/%m/%Y', row_count: 4, imported_count: 2, duplicate_count: 1, error_count: 1, created_at: stamp };
    state.imports.unshift(created);
    return ok(created, 201);
  }
  if (sub === '/imports') return ok({ items: state.imports, total: state.imports.length, limit: 20, offset: 0 });
  if (sub.startsWith('/imports/') && method === 'DELETE') { state.imports = state.imports.slice(1); return ok(null, 204); }
  if (sub === '/bills/upcoming') return ok(summary(state, null).upcoming_bills);
  if (sub === '/bills/suggestions') return ok(state.transactions.length ? [{ name: 'NETFLIX.COM', amount: '39.00', currency: 'AED', cadence: 'monthly', next_due_on: day(20), category: 'subscriptions', account_id: 'fin_acct_current', occurrences: 6 }] : []);
  for (const kind of ['bills', 'debts', 'goals', 'holdings'] as const) {
    if (sub === `/${kind}` && method === 'GET') return ok(state[kind]);
    if (sub === `/${kind}` && method === 'POST') { const created = { id: `fin_${kind}_${state[kind].length + 1}`, currency: 'AED', created_at: stamp, updated_at: stamp, notes: null, account_id: null, ...(body as object) }; state[kind].push(created); return ok(created, 201); }
    if (sub.startsWith(`/${kind}/`)) {
      const id = decodeURIComponent(sub.split('/')[2]);
      if (method === 'DELETE') { state[kind] = state[kind].filter((item) => item.id !== id); return ok(null, 204); }
      const found = state[kind].find((item) => item.id === id);
      if (found && method === 'PATCH') Object.assign(found, body);
      return found ? ok(found) : ok({ error: { code: 'not_found', message: 'Not found.', correlation_id: 'c', details: [] } }, 404);
    }
  }
  return null;
}
