// Mocked /api/v1/finance answers (docs/finance.md) shared by finance.spec.ts and the workspace mock
// (accessibility, localization and page-layout specs). Synthetic values only: example merchants and
// round amounts, never real account numbers.
const now = new Date();
const pad = (n: number) => String(n).padStart(2, '0');
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = (offset: number) => { const d = new Date(now); d.setDate(d.getDate() + offset); return isoDay(d); };
const month = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;
const stamp = now.toISOString();

export interface FinanceState {
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
}

export const NO_RULES = { allowed_currencies: [], max_single: [], max_daily_total: [], category_caps: [], blocked_categories: [], blocked_merchants: [], approval_above: [], quiet_hours: null, cooling_off: null };

export function financeState({ empty = false, denied = false, failSummary = false, failSafe = false, agentProposal = false }: { empty?: boolean; denied?: boolean; failSummary?: boolean; failSafe?: boolean; agentProposal?: boolean } = {}): FinanceState {
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

function financeStagesReply(state: FinanceState, method: string, sub: string, body: unknown, url: URL): { status: number; body: unknown } | null {
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
