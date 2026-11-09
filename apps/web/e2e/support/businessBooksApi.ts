import type { Page, Route } from '@playwright/test';
import { groupBalanceSheet } from './businessAssetsApi';
import { mockBusiness } from './businessApi';

// A stateful mock of /api/v1/business/books (docs/business-books.md#api) for the Books tab specs and the UI gates.
// Every figure is SYNTHETIC. The P&L is fixed (the server computes it; the page only shows it); bills, payments,
// vendors, tags, budgets, adjustments, intercompany entries and rates change the state like the API does, with
// its refusals (403 for a member's owner-only action, 409 for an allocation already paying a bill).
type Json = Record<string, unknown>;
type Role = 'owner' | 'member' | 'viewer';
export interface BooksOptions { role?: Role; setUp?: boolean; empty?: boolean; failReport?: number; bills?: number; group?: 'two' | 'one' | 'fail' | 'many'; feedFails?: boolean;
  /** The journal: ``issues`` adds an item waiting for an exchange rate; ``accountant`` makes the member an accountant. */
  ledger?: 'full' | 'empty' | 'issues'; failStatements?: number; accountant?: boolean }
export interface BooksMock { calls: { method: string; path: string; body: unknown; key: string | null }[]; state: BooksState }

const at = '2026-11-01T09:00:00Z';
const scope = { created_at: at };

export interface BooksState {
  role: Role;
  settings: Json | null;
  vendors: Json[];
  bills: Json[];
  payments: Json[];
  allocations: Json[];
  budgets: Json[];
  adjustments: Json[];
  intercompany: Json[];
  rates: Json[];
  centres: Json[];
  failReport: number;
  empty: boolean;
  counter: number;
  templates: Json[];
  feed: Json;
  group: 'two' | 'one' | 'fail' | 'many';
  feedFails: boolean;
  ledger: { accounts: Json[]; entries: Json[]; closes: Json[]; accountants: Json[]; issues: Json[]; pending: number; failStatements: number };
}

const CHART: [string, string, string, string, string][] = [
  ['bank', '1000', 'Bank and cash', 'bank', 'asset'], ['ar', '1100', 'Accounts receivable', 'receivable', 'asset'],
  ['tax_receivable', '1200', 'VAT receivable (input tax)', 'tax_receivable', 'asset'], ['fixed_assets', '1500', 'Fixed assets at cost', 'fixed_asset', 'asset'],
  ['ap', '2000', 'Accounts payable', 'payable', 'liability'], ['tax_payable', '2100', 'VAT payable (output tax)', 'tax_payable', 'liability'],
  ['loans', '2300', 'Loans', 'loan', 'liability'], ['capital', '3000', 'Owner capital', 'capital', 'equity'],
  ['retained_earnings', '3200', 'Retained earnings', 'retained_earnings', 'equity'], ['revenue_service', '4000', 'Revenue: services', 'revenue', 'revenue'],
  ['cost_of_sales', '5000', 'Cost of sales', 'cost_of_sales', 'expense'], ['operating', '6000', 'Operating expenses', 'operating_expense', 'expense'],
  ['fx', '7000', 'Exchange gains and losses', 'fx', 'expense'],
];

function ledgerState(options: BooksOptions): BooksState['ledger'] {
  const ledger: BooksState['ledger'] = { accounts: [], entries: [], closes: [], accountants: options.accountant ? [{ user_id: 'member_local', added_by: 'user_local', added_at: at }] : [],
    issues: [], pending: 0, failStatements: options.failStatements ?? 0 };
  if (options.ledger === 'empty' || options.setUp === false) return ledger;
  ledger.accounts = CHART.map(([key, code, name, subtype, type]) => ({ id: `bacc_${key}`, code, name, subtype, type, system_key: key, category: null, archived: false, version: 1 }));
  const line = (entry: string, no: number, account: string, debit: string, credit: string, extra: Json = {}) => ({ id: `${entry}_l${no}`, entry_id: entry, line_no: no,
    entry_date: '2026-11-01', source_kind: 'invoice', account_id: `bacc_${account}`, account_code: CHART.find((c) => c[0] === account)?.[1],
    account_name: CHART.find((c) => c[0] === account)?.[2], account_key: account, debit, credit, currency: 'SAR', amount: debit !== '0.00' ? debit : `-${credit}`,
    rate: null, rate_date: null, rate_source: null, category: null, project_id: null, cost_centre_id: null, flow: null, counterparty_workspace_id: null, pair_id: null, ...extra });
  const entry = (id: string, kind: string, description: string, lines: Json[], extra: Json = {}) => ({ id, entry_date: '2026-11-01', event_date: '2026-11-01', source_kind: kind,
    source_id: `${kind}_1`, description, status: 'posted', reverses_id: null, reversed_by_id: null, late: false, created_by: 'system:books', created_at: at,
    can_reverse: kind === 'manual', lines: lines.map((item) => ({ ...item, source_kind: kind })), total: lines.reduce((sum, item) => sum + Number(item.debit), 0).toFixed(2), ...extra });
  ledger.entries.push(
    entry('bje_inv', 'invoice', 'Invoice INV-2026-0007 issued', [line('bje_inv', 1, 'ar', '1150.00', '0.00'), line('bje_inv', 2, 'revenue_service', '0.00', '1000.00'),
      line('bje_inv', 3, 'tax_payable', '0.00', '150.00')]),
    entry('bje_pay', 'invoice_payment', 'Payment for invoice INV-2026-0007', [line('bje_pay', 1, 'bank', '375.00', '0.00', { currency: 'USD', amount: '100.00', rate: '3.75',
      rate_date: '2026-11-01', rate_source: 'Synthetic central bank reference', flow: 'customers_paid' }), line('bje_pay', 2, 'ar', '0.00', '370.00'), line('bje_pay', 3, 'fx', '0.00', '5.00')]),
    entry('bje_man', 'manual', 'Opening balance', [line('bje_man', 1, 'bank', '2000.00', '0.00'), line('bje_man', 2, 'capital', '0.00', '2000.00')]),
  );
  if (options.ledger === 'issues') {
    ledger.issues.push({ event_key: 'bill:bill_usd', source_kind: 'bill', source_id: 'bill_usd', event_date: '2026-10-20', reason: 'missing_rate', currency: 'USD',
      rate_date: '2026-10-20', description: 'Bill HOST-9 from Example Cloud Host' });
    ledger.pending = 1;
  }
  ledger.closes.push({ id: 'bclose_9', period_start: '2026-09-01', period_end: '2026-09-30', kind: 'month', status: 'closed', closed_by: 'user_local', closed_at: at,
    closing_entry_id: null, reopened_by: null, reopened_at: null, reopen_reason: '', snapshot: {} });
  return ledger;
}

function statements(state: BooksState, compare: boolean): Json {
  const block = (total: string, rows: [string, string, string, string][] = []) => ({ total, rows: rows.map(([key, code, name, amount]) => ({ account_id: `bacc_${key}`, code, name,
    subtype: key, system_key: key, amount })) });
  const pnl = { currency: 'SAR', revenue: block('1000.00', [['revenue_service', '4000', 'Revenue: services', '1000.00']]), cost_of_sales: block('0.00'), gross_profit: '1000.00',
    gross_margin_pct: '100.0', operating_expenses: block('0.00'), operating_profit: '1000.00', other_income: block('0.00'), finance_costs: block('0.00'), fx_differences: '5.00',
    net_profit: '1005.00', net_margin_pct: '100.5', groups: {} };
  const sheet = { currency: 'SAR', as_of: '2026-12-31', assets: { current: block('3155.00', [['bank', '1000', 'Bank and cash', '2375.00'], ['ar', '1100', 'Accounts receivable', '780.00']]),
    non_current: block('0.00'), total: '3155.00' }, liabilities: { current: block('150.00', [['tax_payable', '2100', 'VAT payable (output tax)', '150.00']]), non_current: block('0.00'),
    total: '150.00' }, equity: { total: '3005.00', rows: [{ account_id: 'bacc_capital', code: '3000', name: 'Owner capital', subtype: 'capital', system_key: 'capital', amount: '2000.00' },
    { account_id: null, code: '', name: 'Result not yet closed', subtype: 'current_result', system_key: 'unclosed_result', amount: '1005.00' }] },
    liabilities_and_equity: '3155.00', balanced: true, difference: '0.00', complete: state.ledger.issues.length === 0 };
  const tb = (key: string, code: string, name: string, subtype: string, type: string, debit: string, credit: string) => ({ account_id: `bacc_${key}`, code, name, subtype, type,
    system_key: key, opening_debit: '0.00', opening_credit: '0.00', debit, credit, closing_debit: Number(debit) > Number(credit) ? (Number(debit) - Number(credit)).toFixed(2) : '0.00',
    closing_credit: Number(credit) > Number(debit) ? (Number(credit) - Number(debit)).toFixed(2) : '0.00' });
  const complete = state.ledger.issues.length === 0 && state.ledger.pending === 0;
  return {
    period: { from: '2026-01-01', to: '2026-12-31' }, base_currency: 'SAR', complete,
    pending: { to_post: state.ledger.pending, to_reverse: 0, issues: state.ledger.issues.length, issue_rows: state.ledger.issues, scheduled: 0, truncated: false, complete },
    notes: complete ? [] : ['not_posted'], late_entries: 0, closed_through: '2026-09-30',
    trial_balance: { rows: [tb('bank', '1000', 'Bank and cash', 'bank', 'asset', '2375.00', '0.00'), tb('ar', '1100', 'Accounts receivable', 'receivable', 'asset', '1150.00', '370.00'),
      tb('tax_payable', '2100', 'VAT payable (output tax)', 'tax_payable', 'liability', '0.00', '150.00'), tb('capital', '3000', 'Owner capital', 'capital', 'equity', '0.00', '2000.00'),
      tb('revenue_service', '4000', 'Revenue: services', 'revenue', 'revenue', '0.00', '1000.00'), tb('fx', '7000', 'Exchange gains and losses', 'fx', 'expense', '0.00', '5.00')],
    totals: { opening_debit: '0.00', opening_credit: '0.00', debit: '3525.00', credit: '3525.00', closing_debit: '3155.00', closing_credit: '3155.00' }, balanced: true, difference: '0.00' },
    balance_sheet: sheet, pnl,
    reconciliation: { rows: [{ key: 'revenue', journal: '1000.00', derived: '1000.00', difference: '0.00' }, { key: 'net_profit', journal: '1005.00', derived: '1005.00', difference: '0.00' }],
      agrees: true, to: '2026-11-04' },
    equity: { currency: 'SAR', balanced: true, rows: ['opening', 'contributions', 'drawings', 'result', 'transfer_to_retained_earnings', 'other', 'closing'].map((key) => ({ key,
      capital: key === 'contributions' || key === 'closing' ? '2000.00' : '0.00', drawings: '0.00', retained_earnings: '0.00', result: key === 'result' || key === 'closing' ? '1005.00' : '0.00',
      total: key === 'contributions' ? '2000.00' : key === 'result' ? '1005.00' : key === 'closing' ? '3005.00' : '0.00' })) },
    cash_flow: { base_currency: 'SAR', base: { net: '2375.00' }, by_currency: [
      { currency: 'SAR', opening: '0.00', operating: { lines: [], total: '0.00' }, investing: { lines: [], total: '0.00' }, financing: { lines: [{ key: 'other', amount: '2000.00' }], total: '2000.00' },
        net: '2000.00', closing: '2000.00', reconciles: true },
      { currency: 'USD', opening: '0.00', operating: { lines: [{ key: 'customers_paid', amount: '100.00' }], total: '100.00' }, investing: { lines: [], total: '0.00' },
        financing: { lines: [], total: '0.00' }, net: '100.00', closing: '100.00', reconciles: true }] },
    comparatives: compare ? { previous: { from: '2025-01-01', to: '2025-12-31', pnl: { ...pnl, revenue: block('0.00'), gross_profit: '0.00', operating_profit: '0.00', net_profit: '0.00',
      fx_differences: '0.00' }, balance_sheet: { ...sheet, assets: { ...sheet.assets, total: '0.00' } } },
      last_year: { from: '2025-01-01', to: '2025-12-31', pnl: { ...pnl, revenue: block('0.00'), gross_profit: '0.00', operating_profit: '0.00', net_profit: '0.00', fx_differences: '0.00' },
        balance_sheet: { ...sheet, assets: { ...sheet.assets, total: '0.00' } } } } : {},
    policies: [{ key: 'basis', text: 'x' }, { key: 'functional_currency', text: 'x' }, { key: 'rates', text: 'x' }, { key: 'financial_year', text: 'x' },
      { key: 'depreciation', text: 'x' }, { key: 'revenue', text: 'x' }, { key: 'owner_note', text: 'Synthetic owner policy note.' }],
  };
}

function settings(): Json {
  return { id: 'bset_1', base_currency: 'SAR', country: 'SA', fiscal_year_start_month: 1, basis: 'accrual', tax_label: 'VAT', category_sections: { fuel: 'cost_of_sales' },
    rate_max_age_days: 31, version: 1, updated_by: 'user_local', updated_at: at, ...scope };
}

export function booksState(options: BooksOptions = {}): BooksState {
  const state: BooksState = {
    role: options.role ?? 'owner', settings: options.setUp === false ? null : settings(), vendors: [], bills: [], payments: [], allocations: [], budgets: [], adjustments: [],
    intercompany: [], rates: [], centres: [], failReport: options.failReport ?? 0, empty: Boolean(options.empty), counter: 0, templates: [],
    feed: { enabled: false, currencies: [], last_attempt_at: null, last_success_at: null, last_as_of: null, last_error: '', unavailable: [], version: null },
    group: options.group ?? 'two', feedFails: Boolean(options.feedFails), ledger: ledgerState(options),
  };
  if (options.empty || options.setUp === false) return state;
  state.centres.push({ id: 'bcc_ops', code: 'OPS', name: 'Operations', archived: false, version: 1, created_by: 'user_local', updated_at: at, ...scope });
  state.vendors.push({ id: 'bven_landlord', name: 'Example Landlord', tax_number: '300000000000003', country: 'SA', default_category: 'office_rent', default_section: 'operating_expense',
    notes: '', archived: false, version: 1, created_by: 'user_local', updated_at: at, ...scope });
  state.vendors.push({ id: 'bven_fuel', name: 'Example Fuel Station', tax_number: '', country: 'SA', default_category: 'fuel', default_section: 'cost_of_sales',
    notes: '', archived: false, version: 1, created_by: 'user_local', updated_at: at, ...scope });
  for (let index = 0; index < (options.bills ?? 2); index += 1) {
    const late = index === 0;
    state.bills.push(bill(state, { vendor_id: index % 2 ? 'bven_fuel' : 'bven_landlord', description: late ? 'October office rent' : `Fuel for site visits ${index}`,
      reference: late ? 'RENT-10' : `FUEL-${index}`, subtotal: late ? '1000.00' : '150.00', tax: late ? '150.00' : '0.00', category: late ? 'office_rent' : 'fuel',
      section: late ? 'operating_expense' : 'cost_of_sales', bill_date: late ? '2026-10-01' : '2026-11-01', due_date: late ? '2026-10-15' : '2026-11-30', recurrence: late ? 'monthly' : null }));
  }
  state.allocations.push({ id: 'biz_alloc_fuel', source_ref: '4910c0', posted_on: '2026-11-02', amount: '150.00', currency: 'SAR', category: 'fuel', project_id: null, memo: null,
    treatment: 'owner_contribution', status: 'active', allocated_by: 'user_local', section: 'cost_of_sales', tag: null, paying_bill_id: null });
  state.allocations.push({ id: 'biz_alloc_review', source_ref: '77ab12', posted_on: '2026-10-20', amount: '80.00', currency: 'SAR', category: 'meals', project_id: null, memo: 'Client lunch',
    treatment: 'reimbursable', status: 'needs_review', allocated_by: 'user_local', section: 'operating_expense', tag: null, paying_bill_id: null });
  state.rates.push({ id: 'brate_usd', base: 'USD', quote: 'SAR', rate: '3.75', as_of: '2026-10-30', source: 'Synthetic central bank reference', created_by: 'user_local', ...scope });
  state.budgets.push({ id: 'bbud_rent', name: 'Office rent', period_start: '2026-11-01', months: 1, currency: 'SAR', amount: '1200.00', section: null, category: 'office_rent',
    project_id: null, cost_centre_id: null, version: 1, created_by: 'user_local', updated_at: at, ...scope });
  state.rates.push({ id: 'brate_ecb', base: 'EUR', quote: 'SAR', rate: '4.10', as_of: '2026-11-03', source: 'ECB', created_by: 'system:books', origin: 'ecb', ...scope });
  state.templates.push(template(state, { vendor_id: 'bven_landlord', description: 'Office rent', subtotal: '1000.00', tax: '150.00', category: 'office_rent', frequency: 'monthly',
    start_date: '2026-10-01', next_date: '2026-12-01', created_count: 2, last_period: '2026-11-01' }));
  return state;
}

function bill(state: BooksState, fields: Json): Json {
  const subtotal = Number(fields.subtotal), tax = Number(fields.tax ?? 0);
  return { id: `bill_${++state.counter}`, reference: '', currency: 'SAR', cost_centre_id: null, project_id: null, recurrence: null, useful_life_months: null, status: 'open',
    version: 1, created_by: 'user_local', updated_at: at, voided_by: null, voided_at: null, void_reason: '', ...scope, ...fields,
    subtotal: subtotal.toFixed(2), tax: tax.toFixed(2), total: (subtotal + tax).toFixed(2) };
}

function template(state: BooksState, fields: Json): Json {
  const subtotal = Number(fields.subtotal), tax = Number(fields.tax ?? 0);
  return { id: `btpl_${++state.counter}`, reference: '', currency: 'SAR', section: 'operating_expense', cost_centre_id: null, project_id: null, useful_life_months: null,
    interval: 1, due_days: 10, end_date: null, max_count: null, create_as: 'open', status: 'active', anchor_date: fields.start_date, next_index: 0, next_date: fields.start_date,
    created_count: 0, last_period: null, end_reason: '', ended_at: null, version: 1, created_by: 'user_local', updated_by: 'user_local', updated_at: at, ...scope, ...fields,
    subtotal: subtotal.toFixed(2), tax: tax.toFixed(2), total: (subtotal + tax).toFixed(2) };
}

const plusMonth = (value: string, months = 1) => { const [y, m, d] = value.split('-').map(Number); const index = y * 12 + m - 1 + months; return `${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`; };

function templateView(state: BooksState, item: Json, detail = false): Json {
  const write = state.role !== 'viewer';
  const upcoming = item.status === 'active' && item.next_date ? [item.next_date as string, plusMonth(item.next_date as string), plusMonth(item.next_date as string, 2)] : [];
  const bills = state.bills.filter((b) => b.template_id === item.id).map((b) => billView(state, b));
  return { ...item, vendor_name: state.vendors.find((v) => v.id === item.vendor_id)?.name ?? '', per_month: Number(item.subtotal).toFixed(2), upcoming,
    can: { edit: write && item.status !== 'ended', pause: write && item.status === 'active', resume: write && item.status === 'paused', end: write && item.status !== 'ended' },
    ...(detail ? { bills, bills_total: bills.length } : {}) };
}

function upcomingBills(state: BooksState): Json[] {
  return state.templates.filter((t) => t.status === 'active').flatMap((t) => [t.next_date as string, plusMonth(t.next_date as string), plusMonth(t.next_date as string, 2)]
    .map((date) => ({ template_id: t.id, vendor_name: state.vendors.find((v) => v.id === t.vendor_id)?.name ?? '', description: t.description, bill_date: date,
      due_date: date, currency: t.currency, total: t.total, create_as: t.create_as }))).sort((a, b) => a.bill_date.localeCompare(b.bill_date));
}

function group(state: BooksState, url: URL): Json {
  const businesses = [{ workspace_id: 'ws_anum', name: 'Example ANUM Business', base_currency: 'SAR', notes: [] }, { workspace_id: 'ws_tyre', name: 'Example Tyre Business', base_currency: 'AED', notes: ['not_converted'] }];
  if (state.group === 'one') {
    return { enough: false, businesses: businesses.slice(0, 1), skipped: [{ workspace_id: 'ws_tyre', name: 'Example Tyre Business', reason: 'books_not_set_up' }],
      excluded: [], limit: 20, available: [{ workspace_id: 'ws_anum', name: 'Example ANUM Business' }] };
  }
  const chosen = url.searchParams.getAll('workspace');
  const many = state.group === 'many' && !chosen.length;
  if (state.group === 'many') {
    businesses.push({ workspace_id: 'ws_parts', name: 'Example Parts Business', base_currency: 'SAR', notes: [] });
    if (chosen.length) businesses.splice(0, businesses.length, ...[...businesses, { workspace_id: 'ws_store', name: 'Example Store Business', base_currency: 'SAR', notes: [] }]
      .filter((item) => chosen.includes(item.workspace_id)));
  }
  const row = (key: string, a: string, b: string, elim: string, total: string) => ({ key, businesses: { ws_anum: a, ws_tyre: b }, eliminations: elim, total });
  return {
    enough: true, businesses, skipped: [], limit: state.group === 'many' ? 3 : 20,
    excluded: many ? [{ workspace_id: 'ws_store', name: 'Example Store Business', reason: 'limit' }] : [],
    available: state.group === 'many' ? ['ws_anum', 'ws_tyre', 'ws_parts', 'ws_store'].map((id) => ({ workspace_id: id, name: { ws_anum: 'Example ANUM Business', ws_tyre: 'Example Tyre Business', ws_parts: 'Example Parts Business', ws_store: 'Example Store Business' }[id]! }))
      : businesses.map((item) => ({ workspace_id: item.workspace_id, name: item.name })),
    period: { from: url.searchParams.get('from') ?? '2026-01-01', to: url.searchParams.get('to') ?? '2026-12-31', basis: url.searchParams.get('basis') ?? 'accrual' },
    currency: url.searchParams.get('currency') ?? 'SAR', as_of: '2026-12-31',
    pnl: [row('revenue', '19900.00', '8000.00', '0.00', '27900.00'), row('cost_of_sales', '4500.00', '2000.00', '0.00', '6500.00'),
      row('gross_profit', '15400.00', '6000.00', '0.00', '21400.00'), row('operating_expenses', '11575.00', '4040.00', '-2000.00', '13615.00'),
      row('operating_profit', '3825.00', '1960.00', '2000.00', '7785.00'), row('other_income', '2000.00', '0.00', '-2000.00', '0.00'),
      row('finance_costs', '120.00', '0.00', '0.00', '120.00'), row('fx_differences', '15.00', '0.00', '0.00', '15.00'), row('net_profit', '5720.00', '1960.00', '0.00', '7680.00')],
    balances: [row('receivables', '4235.00', '1020.00', '0.00', '5255.00'), row('payables', '1300.00', '510.00', '0.00', '1810.00'),
      row('owed_to_members', '80.00', '0.00', '0.00', '80.00'), row('debt', '10000.00', '0.00', '0.00', '10000.00'),
      row('intercompany_receivable', '5000.00', '0.00', '-5000.00', '0.00'), row('intercompany_payable', '0.00', '5000.00', '-5000.00', '0.00')],
    cash_flow: [row('customers_paid', '15000.00', '6000.00', '0.00', '21000.00'), row('intercompany_lent', '3000.00', '0.00', '-3000.00', '0.00'),
      row('intercompany_borrowed', '0.00', '3000.00', '-3000.00', '0.00'), row('net', '17000.00', '9000.00', '0.00', '26000.00'), row('paid_by_members', '150.00', '0.00', '0.00', '150.00')],
    not_converted: { pnl: [{ workspace_id: 'ws_tyre', row: 'operating_expenses', currency: 'EUR', amount: '90.00' }, { workspace_id: null, row: 'operating_expenses', currency: 'EUR', amount: '90.00' }],
      balances: [], cash: [] },
    complete: false, eliminated: true,
    conversions: [{ workspace_id: 'ws_tyre', from_currency: 'AED', to_currency: 'SAR', rate: '1.0211', as_of: '2026-11-03', source: 'Synthetic central bank reference', via: null, figures: 6 }],
  };
}

function billView(state: BooksState, item: Json, detail = false): Json {
  const payments = state.payments.filter((p) => p.bill_id === item.id);
  const live = payments.filter((p) => !p.voided_at);
  const paid = live.reduce((sum, p) => sum + Number(p.amount), 0);
  const total = Number(item.total);
  const balance = item.status === 'void' ? 0 : total - paid;
  const payment_state = item.status === 'void' ? 'void' : item.status === 'draft' ? 'draft' : paid <= 0 ? 'unpaid' : paid >= total ? 'paid' : 'partly_paid';
  const open = payment_state === 'unpaid' || payment_state === 'partly_paid';
  const overdue = open && String(item.due_date) < '2026-11-04';
  const write = state.role !== 'viewer', manage = state.role === 'owner';
  return {
    ...item, vendor_name: state.vendors.find((v) => v.id === item.vendor_id)?.name ?? '', amount_paid: paid.toFixed(2), balance: balance.toFixed(2), payment_state, overdue,
    days_overdue: overdue ? 20 : 0, can: { edit: write && item.status !== 'void', edit_money: live.length === 0 && item.status !== 'void', pay: write && open,
      approve: write && item.status === 'draft', void: manage && item.status !== 'void' && live.length === 0 },
    template_id: item.template_id ?? null, period_date: item.period_date ?? null,
    ...(detail ? { payments: payments.map((p) => ({ ...p, counts: !p.voided_at, allocation_removed: false })) } : {}),
  };
}

function report(state: BooksState, basis: string): Json {
  const zero = { lines: [], total: '0.00' };
  if (state.empty) {
    return { period: { from: '2026-01-01', to: '2026-12-31', basis }, base_currency: 'SAR',
      pnl: { currency: 'SAR', revenue: zero, cost_of_sales: zero, gross_profit: '0.00', gross_margin_pct: null, operating_expenses: zero, operating_profit: '0.00', other_income: zero,
        finance_costs: zero, fx_differences: '0.00', net_profit: '0.00', net_margin_pct: null, not_converted: [], complete: true },
      tax: { label: 'VAT', country: 'SA', by_currency: [], allocations_without_tax_split: 0 }, cash_flow: { lines: {}, net: [], paid_by_members: {} },
      balances: { as_of: '2026-12-31', aged_on: '2026-11-04', receivables: [], outstanding_invoices: [], outstanding_invoices_count: 0, payables: [], unpaid_bills: [], unpaid_bills_count: 0,
        owed_to_members: [], debt: [], intercompany: [] }, equity: { contributions: [], drawings: [] }, trend: [], customers: [], projects: [], cost_centres: [], budgets: [],
      recurring: { items: [], per_month: [], upcoming: [] }, allocations_in_review: 0, conversions: [], notes: [], truncated: false };
  }
  const trend = Array.from({ length: 12 }, (_, index) => {
    const month = `2026-${String(index + 1).padStart(2, '0')}`;
    const revenue = index >= 8 ? 4000 + index * 250 : 0, costs = index >= 8 ? 1300 + index * 40 : 0;
    return { month, revenue: revenue.toFixed(2), costs: costs.toFixed(2), profit: (revenue - costs).toFixed(2), unconverted: index === 10 };
  });
  return {
    period: { from: '2026-01-01', to: '2026-12-31', basis }, base_currency: 'SAR',
    pnl: {
      currency: 'SAR',
      revenue: { lines: [{ key: 'service', amount: '18000.00' }, { key: 'labor', amount: '2400.00' }, { key: 'credit_notes', amount: '-500.00' }], total: '19900.00' },
      cost_of_sales: { lines: [{ key: 'fuel', amount: '300.00' }, { key: 'subcontractors', amount: '4200.00' }], total: '4500.00' },
      gross_profit: '15400.00', gross_margin_pct: '77.4',
      operating_expenses: { lines: [{ key: 'office_rent', amount: '11000.00' }, { key: 'depreciation', amount: '200.00' }, { key: 'hosting', amount: '375.00' }], total: '11575.00' },
      operating_profit: '3825.00', other_income: { lines: [{ key: 'intercompany', amount: '2000.00' }], total: '2000.00' },
      finance_costs: { lines: [{ key: 'loan_interest', amount: '120.00' }], total: '120.00' }, fx_differences: '15.00', net_profit: '5720.00', net_margin_pct: '28.7',
      not_converted: [{ currency: 'EUR', groups: { operating_expense: '90.00' } }], complete: false,
    },
    tax: { label: 'VAT', country: 'SA', by_currency: [{ currency: 'SAR', output: '2985.00', input: '172.50', net: '2812.50' }], allocations_without_tax_split: 1 },
    cash_flow: { lines: { customers_paid: [{ currency: 'SAR', amount: '15000.00' }], suppliers_paid: [{ currency: 'SAR', amount: '4800.00' }], loans_received: [{ currency: 'SAR', amount: '10000.00' }] },
      net: [{ currency: 'SAR', amount: '20200.00' }], paid_by_members: { owner_contribution: [{ currency: 'SAR', amount: '150.00' }] } },
    balances: {
      as_of: '2026-12-31', aged_on: '2026-11-04',
      receivables: [{ currency: 'SAR', total: '4235.00', current: '1700.00', days_1_30: '2535.00' }],
      outstanding_invoices: [{ invoice_id: 'inv_1', number: 'INV-2026-0007', account_id: 'acc_1', account_name: 'Example Logistics Co', due_date: '2026-10-20', days_overdue: 15, currency: 'SAR', balance: '2535.00' },
        { invoice_id: 'inv_2', number: 'INV-2026-0009', account_id: 'acc_2', account_name: 'Example Retail', due_date: '2026-11-20', days_overdue: 0, currency: 'SAR', balance: '1700.00' }],
      outstanding_invoices_count: 2, payables: [{ currency: 'SAR', total: '1300.00', current: '150.00', days_1_30: '1150.00' }],
      unpaid_bills: [{ bill_id: 'bill_1', vendor_name: 'Example Landlord', reference: 'RENT-10', due_date: '2026-10-15', days_overdue: 20, currency: 'SAR', balance: '1150.00' }],
      unpaid_bills_count: 2, owed_to_members: [{ currency: 'SAR', amount: '80.00' }], debt: [{ currency: 'SAR', amount: '10000.00' }],
      intercompany: [{ workspace_id: 'ws_tyre', name: 'Example Tyre Business', currency: 'SAR', amount: '2000.00' }],
    },
    equity: { contributions: [{ currency: 'SAR', amount: '150.00' }], drawings: [] },
    trend,
    customers: [{ id: 'acc_1', name: 'Example Logistics Co', revenue: '12000.00', costs: '3000.00', margin: '9000.00', margin_pct: '75.0' },
      { id: 'acc_2', name: 'Example Retail', revenue: '7900.00', costs: '1500.00', margin: '6400.00', margin_pct: '81.0' }],
    projects: [{ id: 'prj_1', name: 'Example fleet dashboard', revenue: '12000.00', costs: '3000.00', margin: '9000.00', margin_pct: '75.0' }],
    cost_centres: [{ id: 'bcc_ops', name: 'OPS Operations', revenue: '0.00', costs: '1150.00', margin: '-1150.00', margin_pct: null }],
    budgets: [{ id: 'bbud_rent', name: 'Office rent', period_start: '2026-11-01', period_end: '2026-11-30', currency: 'SAR', budget: '1200.00', actual: '1000.00', variance: '200.00',
      used_pct: '83.3', comparable: true, unconverted: false }],
    recurring: { items: [{ bill_id: null, template_id: 'btpl_rent', vendor_id: 'bven_landlord', vendor_name: 'Example Landlord', category: 'office_rent', recurrence: 'monthly',
      interval: 1, currency: 'SAR', amount: '1000.00', per_month: '1000.00', last_bill_date: '2026-11-01', next_date: '2026-12-01', status: 'active' }],
    per_month: [{ currency: 'SAR', amount: '1000.00' }], upcoming: upcomingBills(state) },
    allocations_in_review: 1,
    conversions: [{ from_currency: 'USD', to_currency: 'SAR', rate: '3.75', as_of: '2026-10-30', source: 'Synthetic central bank reference', via: null, figures: 1 }],
    notes: ['allocations_in_review', 'not_converted'], truncated: false,
  };
}

const page = (items: Json[], url: URL) => {
  const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 25);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
};

export function booksReply(state: BooksState, method: string, url: URL, body: Json | null): { status: number; body: unknown } | null {
  const path = url.pathname.replace('/api/v1/business/books', '');
  const ok = (value: unknown, status = 200) => ({ status, body: value });
  const refuse = (status: number, code: string, detail: string) => ({ status, body: { detail, code } });
  const owner = state.role === 'owner';
  if (state.role === 'viewer') return refuse(403, 'forbidden', 'The business books are for workspace owners and members. Ask a workspace owner to make you a member.');
  const ownersOnly = () => refuse(403, 'forbidden', 'Only workspace owners can do this (settings, exchange rates, adjustments, voiding and intercompany entries).');
  const accountant = state.ledger.accountants.some((item) => item.user_id === 'member_local');
  const account = owner || accountant;
  const accountantsOnly = () => refuse(403, 'forbidden', 'Only an owner or an accountant of this business can do this. An owner can name accountants in Books, Settings.');
  if (method === 'GET' && path === '/setup') {
    return ok({ settings: state.settings, permissions: { read: true, write: true, manage: owner, accountant, account },
      closed_through: state.ledger.closes.filter((row) => row.status === 'closed').map((row) => row.period_end as string).sort().at(-1) ?? null,
      counts: { vendors: state.vendors.length, bills: state.bills.length, cost_centres: state.centres.length, budgets: state.budgets.length, rates: state.rates.length,
        adjustments: state.adjustments.length, intercompany: state.intercompany.length, allocations: state.allocations.length },
      default_period: state.settings ? { from: '2026-01-01', to: '2026-12-31' } : null, cost_centres: state.centres.filter((c) => !c.archived),
      allocation_categories: [...new Set(state.allocations.map((a) => a.category as string))], sections: ['cost_of_sales', 'operating_expense', 'capital'], adjustment_kinds: [] });
  }
  if (method === 'PUT' && path === '/settings') {
    if (!owner) return ownersOnly();
    state.settings = { ...settings(), ...body, version: ((state.settings?.version as number) ?? 0) + 1 };
    return ok(state.settings);
  }
  if (!state.settings && path !== '/settings') return refuse(409, 'books_not_set_up', 'Set the business\'s base currency and financial year first (Books, Settings).');
  if (method === 'GET' && path === '/report') {
    if (state.failReport > 0) { state.failReport -= 1; return refuse(500, 'error', 'The books could not be read.'); }
    return ok(report(state, url.searchParams.get('basis') ?? 'accrual'));
  }
  if (method === 'GET' && path === '/projects') {
    return ok({ ...page(state.empty ? [] : [{ id: 'prj_1', name: 'Example fleet dashboard', account_id: 'acc_1', status: 'active', currency: 'SAR',
      books: { revenue: '12000.00', costs: '3000.00', margin: '9000.00', margin_pct: '75.0' },
      contract: { revenue: 15000, contract_total: 17250, cash_collected: 9000, outstanding: 8250, gross_margin: 11000, gross_margin_pct: 73.3, currency: 'SAR' } }], url),
    base_currency: 'SAR', period: { from: '2026-01-01', to: '2026-12-31', basis: 'accrual' } });
  }
  if (path === '/cost-centres') {
    if (method === 'GET') return ok(page(state.centres.filter((c) => String(c.archived) === (url.searchParams.get('archived') ?? 'false')), url));
    const centre = { id: `bcc_${++state.counter}`, ...body, archived: false, version: 1, created_by: 'user_local', updated_at: at, ...scope };
    state.centres.push(centre);
    return ok(centre, 201);
  }
  if (path === '/vendors') {
    if (method === 'GET') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      return ok(page(state.vendors.filter((v) => String(v.archived) === (url.searchParams.get('archived') ?? 'false') && String(v.name).toLowerCase().includes(q)), url));
    }
    if (state.vendors.some((v) => String(v.name).toLowerCase() === String(body?.name).toLowerCase())) return refuse(409, 'duplicate', 'A vendor with this name already exists.');
    const vendor = { id: `bven_${++state.counter}`, ...body, archived: false, version: 1, created_by: 'user_local', updated_at: at, ...scope };
    state.vendors.push(vendor);
    return ok(vendor, 201);
  }
  const vendorMatch = path.match(/^\/vendors\/([^/]+)$/);
  if (vendorMatch && method === 'PATCH') {
    const vendor = state.vendors.find((v) => v.id === vendorMatch[1]);
    if (!vendor) return refuse(404, 'vendor_not_found', 'Vendor not found.');
    Object.assign(vendor, body, { version: (vendor.version as number) + 1 });
    return ok(vendor);
  }
  if (path === '/bills' && method === 'GET') {
    const status = url.searchParams.get('status'), q = (url.searchParams.get('q') ?? '').toLowerCase();
    let views = state.bills.map((b) => billView(state, b)).sort((a, b) => String(b.bill_date).localeCompare(String(a.bill_date)));
    if (q) views = views.filter((v) => `${v.reference} ${v.description}`.toLowerCase().includes(q));
    if (status === 'overdue') views = views.filter((v) => v.overdue);
    else if (status === 'open') views = views.filter((v) => v.payment_state === 'unpaid' || v.payment_state === 'partly_paid');
    else if (status) views = views.filter((v) => v.payment_state === status);
    const open = views.filter((v) => v.payment_state === 'unpaid' || v.payment_state === 'partly_paid').reduce((sum, v) => sum + Number(v.balance), 0);
    return ok({ ...page(views, url), summary: { open: open ? [{ currency: 'SAR', amount: open.toFixed(2) }] : [], overdue: [] } });
  }
  if (path === '/bills' && method === 'POST') {
    const created = bill(state, { ...body, category: body?.category ?? 'other', section: body?.section ?? 'operating_expense', due_date: body?.due_date ?? body?.bill_date });
    state.bills.push(created);
    return ok(billView(state, created), 201);
  }
  const billMatch = path.match(/^\/bills\/([^/]+)(\/.*)?$/);
  if (billMatch) {
    const item = state.bills.find((b) => b.id === billMatch[1]);
    if (!item) return refuse(404, 'bill_not_found', 'Bill not found.');
    const rest = billMatch[2] ?? '';
    if (method === 'GET' && !rest) return ok(billView(state, item, true));
    if (method === 'POST' && rest === '/approve') { Object.assign(item, { status: 'open', version: (item.version as number) + 1 }); return ok(billView(state, item, true)); }
    if (method === 'PATCH' && !rest) { Object.assign(item, bill(state, { ...item, ...body }), { id: item.id, version: (item.version as number) + 1 }); return ok(billView(state, item, true)); }
    if (method === 'POST' && rest === '/void') {
      if (!owner) return ownersOnly();
      Object.assign(item, { status: 'void', voided_by: 'user_local', voided_at: at, void_reason: body?.reason });
      return ok(billView(state, item, true));
    }
    if (method === 'POST' && rest === '/payments') {
      const balance = Number(billView(state, item).balance);
      if (body?.kind === 'allocation') {
        const allocation = state.allocations.find((a) => a.id === body.allocation_id);
        if (!allocation) return refuse(404, 'allocation_not_found', 'Allocation not found in this business.');
        if (allocation.paying_bill_id) return refuse(409, 'allocation_used', 'That allocation already pays another bill.');
        allocation.paying_bill_id = item.id;
        state.payments.push({ id: `bpay_${++state.counter}`, bill_id: item.id, kind: 'allocation', allocation_id: allocation.id, amount: allocation.amount, currency: 'SAR', paid_on: allocation.posted_on,
          method: 'allocation', reference: `#${allocation.source_ref}`, note: '', recorded_by: 'user_local', voided_by: null, voided_at: null, void_reason: '', ...scope });
      } else {
        if (Number(body?.amount) > balance) return { status: 409, body: { detail: 'The payment is more than the bill\'s balance.', code: 'over_balance', remaining: balance.toFixed(2) } };
        state.payments.push({ id: `bpay_${++state.counter}`, bill_id: item.id, kind: 'recorded', allocation_id: null, amount: Number(body?.amount).toFixed(2), currency: 'SAR',
          paid_on: body?.paid_on, method: body?.method, reference: body?.reference ?? '', note: body?.note ?? '', recorded_by: 'user_local', voided_by: null, voided_at: null, void_reason: '', ...scope });
      }
      return ok({ ...billView(state, item, true), replayed: false }, 201);
    }
    const voidPay = rest.match(/^\/payments\/([^/]+)\/void$/);
    if (method === 'POST' && voidPay) {
      if (!owner) return ownersOnly();
      const payment = state.payments.find((p) => p.id === voidPay[1]);
      if (payment) Object.assign(payment, { voided_at: at, voided_by: 'user_local', void_reason: body?.reason });
      return ok(billView(state, item, true));
    }
  }
  if (path === '/allocations' && method === 'GET') {
    const status = url.searchParams.get('status');
    let rows = state.allocations;
    if (status === 'unpaid_bill') rows = rows.filter((a) => !a.paying_bill_id && a.status === 'active');
    else if (status === 'untagged') rows = rows.filter((a) => !a.tag);
    else if (status) rows = rows.filter((a) => a.status === status);
    return ok(page(rows, url));
  }
  const tagMatch = path.match(/^\/allocations\/([^/]+)\/tag$/);
  if (tagMatch && method === 'PUT') {
    const allocation = state.allocations.find((a) => a.id === tagMatch[1]);
    if (!allocation) return refuse(404, 'allocation_not_found', 'Allocation not found in this business.');
    const tag = { id: `btag_${++state.counter}`, allocation_id: allocation.id, cost_centre_id: body?.cost_centre_id ?? null, section: body?.section ?? null, note: body?.note ?? '',
      version: ((allocation.tag as Json | null)?.version as number ?? 0) + 1, updated_by: 'user_local', updated_at: at, ...scope };
    allocation.tag = tag;
    if (body?.section) allocation.section = body.section;
    return ok(tag);
  }
  if (path === '/budgets') {
    if (method === 'GET') return ok(page(state.budgets, url));
    const budget = { id: `bbud_${++state.counter}`, ...body, currency: 'SAR', amount: Number(body?.amount).toFixed(2), version: 1, created_by: 'user_local', updated_at: at, ...scope };
    state.budgets.push(budget);
    return ok(budget, 201);
  }
  const budgetMatch = path.match(/^\/budgets\/([^/]+)$/);
  if (budgetMatch && method === 'DELETE') { state.budgets = state.budgets.filter((b) => b.id !== budgetMatch[1]); return { status: 204, body: null }; }
  if (budgetMatch && method === 'PATCH') { const budget = state.budgets.find((b) => b.id === budgetMatch[1]); Object.assign(budget ?? {}, body); return ok(budget); }
  if (path === '/group/balance-sheet' && method === 'GET') {
    if (!owner) return refuse(403, 'forbidden', 'Only workspace owners can do this (settings, exchange rates, adjustments, voiding and intercompany entries).');
    return ok(state.group === 'one' ? { enough: false, as_of: '2026-11-09', skipped: [], available: [] } : groupBalanceSheet());
  }
  if (path === '/group' && method === 'GET') {
    if (!owner) return refuse(403, 'forbidden', 'Only workspace owners can do this (settings, exchange rates, adjustments, voiding and intercompany entries).');
    if (state.group === 'fail') { state.group = 'two'; return refuse(500, 'error', 'The combined books could not be read.'); }
    return ok(group(state, url));
  }
  if (path === '/recurring') {
    if (method === 'GET') {
      const status = url.searchParams.get('status');
      return ok({ ...page(state.templates.filter((t) => !status || t.status === status).map((t) => templateView(state, t)), url), upcoming: upcomingBills(state) });
    }
    const created = template(state, { ...body, category: body?.category ?? 'other', section: body?.section ?? 'operating_expense' });
    state.templates.unshift(created);
    if (String(created.start_date) <= '2026-11-04') {
      state.bills.push(bill(state, { vendor_id: created.vendor_id, description: created.description, subtotal: created.subtotal, tax: created.tax, category: created.category,
        section: created.section, bill_date: created.start_date, due_date: created.start_date, status: created.create_as === 'draft' ? 'draft' : 'open', template_id: created.id,
        period_date: created.start_date }));
      Object.assign(created, { created_count: 1, last_period: created.start_date, next_date: plusMonth(created.start_date as string) });
    }
    return ok(templateView(state, created, true), 201);
  }
  const templateMatch = path.match(/^\/recurring\/([^/]+)(\/(pause|resume|end))?$/);
  if (templateMatch) {
    const item = state.templates.find((t) => t.id === templateMatch[1]);
    if (!item) return refuse(404, 'template_not_found', 'Recurring bill not found.');
    if (method === 'GET') return ok(templateView(state, item, true));
    if (body?.version !== item.version) return refuse(409, 'stale', 'Someone changed this since you opened it. Reload and try again.');
    const action = templateMatch[3];
    if (method === 'PATCH') Object.assign(item, template(state, { ...item, ...body }), { id: item.id, version: (item.version as number) + 1, created_count: item.created_count, next_date: item.next_date });
    else if (action === 'pause') Object.assign(item, { status: 'paused', version: (item.version as number) + 1 });
    else if (action === 'resume') Object.assign(item, { status: 'active', version: (item.version as number) + 1 });
    else if (action === 'end') Object.assign(item, { status: 'ended', next_date: null, end_reason: 'owner', ended_at: at, version: (item.version as number) + 1 });
    return ok(templateView(state, item, true));
  }
  if (path === '/rates/auto') {
    const view = () => ({ ...state.feed, followed: state.feed.enabled ? ['SAR', ...(state.feed.currencies as string[])].filter((code) => code !== 'EUR') : [], source: 'ECB',
      source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml', can_manage: owner });
    if (method === 'GET') return ok(view());
    if (!owner) return ownersOnly();
    state.feed = { ...state.feed, enabled: body?.enabled, currencies: body?.currencies, version: ((state.feed.version as number | null) ?? 0) + 1 };
    return ok(view());
  }
  if (path === '/rates/auto/refresh' && method === 'POST') {
    if (!owner) return ownersOnly();
    const failed = state.feedFails;
    state.feed = { ...state.feed, last_attempt_at: '2026-11-04T08:00:00Z', ...(failed ? { last_error: 'The source answered 503. ANUM will try again later.' }
      : { last_success_at: '2026-11-04T08:00:00Z', last_as_of: '2026-11-03', last_error: '', unavailable: ['SAR'] }) };
    return ok({ ...state.feed, followed: ['USD'], source: 'ECB', source_url: '', can_manage: true, added: failed ? 0 : 1 });
  }
  if (path === '/rates') {
    if (method === 'GET') return ok(page(state.rates.filter((r) => !url.searchParams.get('origin') || (r.origin ?? 'manual') === url.searchParams.get('origin')), url));
    if (!owner) return ownersOnly();
    const rate = { id: `brate_${++state.counter}`, ...body, created_by: 'user_local', ...scope };
    state.rates.push(rate);
    return ok(rate, 201);
  }
  const rateMatch = path.match(/^\/rates\/([^/]+)$/);
  if (rateMatch && method === 'DELETE') { state.rates = state.rates.filter((r) => r.id !== rateMatch[1]); return { status: 204, body: null }; }
  if (path === '/adjustments') {
    if (method === 'GET') return ok(page(url.searchParams.get('include_void') ? state.adjustments : state.adjustments.filter((a) => a.status === 'active'), url));
    if (!owner) return ownersOnly();
    const adjustment = { id: `badj_${++state.counter}`, ...body, amount: Number(body?.amount).toFixed(2), status: 'active', created_by: 'user_local', voided_by: null, voided_at: null, void_reason: '', ...scope };
    state.adjustments.push(adjustment);
    return ok(adjustment, 201);
  }
  const adjMatch = path.match(/^\/adjustments\/([^/]+)\/void$/);
  if (adjMatch && method === 'POST') {
    if (!owner) return ownersOnly();
    const adjustment = state.adjustments.find((a) => a.id === adjMatch[1]);
    Object.assign(adjustment ?? {}, { status: 'void', voided_by: 'user_local', voided_at: at, void_reason: body?.reason });
    return ok(adjustment);
  }
  if (path === '/intercompany/counterparties') return owner ? ok({ items: [{ workspace_id: 'ws_tyre', name: 'Example Tyre Business' }] }) : ownersOnly();
  if (path === '/intercompany') {
    if (method === 'GET') return ok(page(url.searchParams.get('include_void') ? state.intercompany : state.intercompany.filter((e) => e.status === 'active'), url));
    if (!owner) return ownersOnly();
    const entry = { id: `bic_${++state.counter}`, pair_id: `icp_${state.counter}`, counterparty_workspace_id: body?.workspace_id, counterparty_name: 'Example Tyre Business', ...body,
      amount: Number(body?.amount).toFixed(2), status: 'active', created_by: 'user_local', voided_by: null, voided_at: null, void_reason: '', ...scope };
    state.intercompany.push(entry);
    return ok(entry, 201);
  }
  const icMatch = path.match(/^\/intercompany\/([^/]+)\/void$/);
  if (icMatch && method === 'POST') {
    const entry = state.intercompany.find((e) => e.id === icMatch[1]);
    Object.assign(entry ?? {}, { status: 'void', voided_by: 'user_local', voided_at: at, void_reason: body?.reason });
    return ok(entry);
  }
  return ledgerReply(state, method, url, path, body, { owner, account, ok, refuse, accountantsOnly });
}

type Helpers = { owner: boolean; account: boolean; ok: (value: unknown, status?: number) => { status: number; body: unknown };
  refuse: (status: number, code: string, detail: string) => { status: number; body: unknown }; accountantsOnly: () => { status: number; body: unknown } };

function ledgerReply(state: BooksState, method: string, url: URL, path: string, body: Json | null, h: Helpers): { status: number; body: unknown } | null {
  const ledger = state.ledger;
  const locked = () => ledger.closes.filter((row) => row.status === 'closed').map((row) => row.period_end as string).sort().at(-1) ?? null;
  if (path === '/ledger/accounts' && method === 'GET') {
    const all = url.searchParams.get('include_archived') === 'true';
    return h.ok({ items: ledger.accounts.filter((a) => all || !a.archived), subtypes: {}, can_edit: h.account });
  }
  if (path === '/ledger/accounts' && method === 'POST') {
    if (!h.account) return h.accountantsOnly();
    if (ledger.accounts.some((a) => a.code === body?.code)) return h.refuse(409, 'duplicate', 'An account with this code (or bound to this category) already exists.');
    const item = { id: `bacc_${ledger.accounts.length + 1}`, ...body, type: 'expense', system_key: null, archived: false, version: 1 };
    ledger.accounts.push(item);
    return h.ok(item, 201);
  }
  const accountMatch = path.match(/^\/ledger\/accounts\/([^/]+)(\/lines)?$/);
  if (accountMatch && accountMatch[2] && method === 'GET') {
    const account = ledger.accounts.find((a) => a.id === accountMatch[1]);
    if (!account) return h.refuse(404, 'account_not_found', 'Account not found.');
    let running = 0;
    const items = ledger.entries.flatMap((entry) => (entry.lines as Json[]).filter((line) => line.account_id === account.id).map((line) => {
      running += Number(line.debit) - Number(line.credit);
      return { ...line, description: entry.description, source_id: entry.source_id, late: false, reversal: Boolean(entry.reverses_id), balance: running.toFixed(2) };
    }));
    return h.ok({ account, base_currency: 'SAR', period: { from: url.searchParams.get('from') ?? '2026-01-01', to: url.searchParams.get('to') ?? '2026-12-31' }, opening: '0.00',
      closing: running.toFixed(2), items, total: items.length, limit: 50, offset: 0 });
  }
  if (accountMatch && method === 'PATCH') {
    if (!h.account) return h.accountantsOnly();
    const account = ledger.accounts.find((a) => a.id === accountMatch[1]);
    if (body?.archived && account?.system_key) return h.refuse(409, 'system_account', 'The posting rules use this account: it can be renamed or renumbered, never archived.');
    Object.assign(account ?? {}, body, { version: Number(account?.version ?? 1) + 1 });
    return h.ok(account);
  }
  if (path === '/ledger/status' && method === 'GET') {
    return h.ok({ to_post: ledger.pending, to_reverse: 0, issues: ledger.issues.length, issue_rows: ledger.issues, scheduled: 2, truncated: false,
      complete: ledger.pending === 0 && ledger.issues.length === 0, closed_through: locked(), can_post: true, can_account: h.account, can_reopen: h.owner });
  }
  if (path === '/ledger/post' && method === 'POST') {
    const posted = ledger.pending;
    ledger.pending = 0;
    if (ledger.accounts.length === 0) ledgerState({}).accounts.forEach((a) => ledger.accounts.push(a));
    return h.ok({ posted, reversed: 0, late: 0, issues: ledger.issues.length, issue_rows: ledger.issues, scheduled: 2, unchanged: ledger.entries.length, truncated: false });
  }
  if (path === '/ledger/issues' && method === 'GET') return h.ok({ items: ledger.issues, total: ledger.issues.length, limit: 25, offset: 0 });
  if (path === '/ledger/journal' && method === 'GET') {
    const kind = url.searchParams.get('source_kind');
    const rows = ledger.entries.filter((entry) => !kind || entry.source_kind === kind).slice().reverse();
    const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 25);
    return h.ok({ items: rows.slice(offset, offset + limit), total: rows.length, limit, offset, base_currency: 'SAR', can_account: h.account });
  }
  if (path === '/ledger/journal' && method === 'POST') {
    if (!h.account) return h.accountantsOnly();
    const lines = (body?.lines as Json[]) ?? [];
    const debit = lines.reduce((sum, line) => sum + Number(line.debit), 0), credit = lines.reduce((sum, line) => sum + Number(line.credit), 0);
    if (Math.abs(debit - credit) > 0.0001) return h.refuse(422, 'unbalanced', 'Debits and credits must be equal, on two lines or more.');
    if (locked() && String(body?.entry_date) <= String(locked())) return h.refuse(409, 'period_locked', `That day is in a closed period (closed through ${locked()}). Reopen it first.`);
    const id = `bje_m${ledger.entries.length + 1}`;
    const entry = { id, entry_date: body?.entry_date, event_date: body?.entry_date, source_kind: 'manual', source_id: id, description: body?.description, status: 'posted', reverses_id: null,
      reversed_by_id: null, late: false, created_by: 'user_local', created_at: at, can_reverse: true, total: debit.toFixed(2),
      lines: lines.map((line, index) => { const account = ledger.accounts.find((a) => a.id === line.account_id);
        return { id: `${id}_l${index}`, entry_id: id, line_no: index + 1, entry_date: body?.entry_date, source_kind: 'manual', account_id: line.account_id, account_code: account?.code,
          account_name: account?.name, account_key: account?.system_key, debit: Number(line.debit).toFixed(2), credit: Number(line.credit).toFixed(2), currency: 'SAR',
          amount: Number(line.debit) ? Number(line.debit).toFixed(2) : `-${Number(line.credit).toFixed(2)}`, rate: null, rate_date: null, rate_source: null, category: null,
          project_id: null, cost_centre_id: null, flow: null, counterparty_workspace_id: null, pair_id: null }; }) };
    ledger.entries.push(entry);
    return h.ok(entry, 201);
  }
  const reverseMatch = path.match(/^\/ledger\/journal\/([^/]+)\/reverse$/);
  if (reverseMatch && method === 'POST') {
    if (!h.account) return h.accountantsOnly();
    const entry = ledger.entries.find((item) => item.id === reverseMatch[1]);
    if (!entry || entry.source_kind !== 'manual') return h.refuse(409, 'not_manual', 'This entry comes from a source document. Change or void the document instead; the journal follows it.');
    const reversal = { ...entry, id: `${entry.id}_r`, description: `Reversed: ${body?.reason}`, reverses_id: entry.id, can_reverse: false,
      lines: (entry.lines as Json[]).map((line) => ({ ...line, id: `${line.id}_r`, debit: line.credit, credit: line.debit })) };
    Object.assign(entry, { status: 'reversed', reversed_by_id: reversal.id, can_reverse: false });
    ledger.entries.push(reversal);
    return h.ok(reversal);
  }
  if (path === '/ledger/statements' && method === 'GET') {
    if (ledger.failStatements > 0) { ledger.failStatements -= 1; return h.refuse(500, 'internal', 'The statements could not be read.'); }
    if (ledger.accounts.length === 0) return h.ok({ ...statements(state, false), trial_balance: { rows: [], totals: {}, balanced: true, difference: '0.00' } });
    return h.ok(statements(state, url.searchParams.get('compare') === 'true'));
  }
  if (path === '/close' && method === 'GET') {
    const through = locked() === '2026-09-30' ? '2026-10-31' : '2026-11-30';
    return h.ok({ closed_through: locked(), next: { through, year_end: false, ended: through < '2026-11-04', ready: through < '2026-11-04' && ledger.issues.length === 0 && ledger.pending === 0,
      issues: ledger.issues.length, issue_rows: ledger.issues, drafts: 0, allocations_in_review: 0, to_post: ledger.pending },
      history: ledger.closes.slice().reverse(), history_total: ledger.closes.length, fiscal_year_start_month: 1, can_close: h.account, can_reopen: h.owner });
  }
  if (path === '/close' && method === 'POST') {
    if (!h.account) return h.accountantsOnly();
    const end = String(body?.through);
    ledger.closes.push({ id: `bclose_${ledger.closes.length + 1}`, period_start: `${end.slice(0, 8)}01`, period_end: end, kind: 'month', status: 'closed', closed_by: 'user_local',
      closed_at: at, closing_entry_id: null, reopened_by: null, reopened_at: null, reopen_reason: '', snapshot: {} });
    return h.ok({ closed_through: end, previous: '2026-09-30', closed: ledger.closes.slice(-1) });
  }
  if (path === '/reopen' && method === 'POST') {
    if (!h.owner) return h.refuse(403, 'forbidden', 'Only an owner can reopen a closed period.');
    const from = String(body?.month);
    const rows = ledger.closes.filter((row) => row.status === 'closed' && String(row.period_end) >= from);
    rows.forEach((row) => Object.assign(row, { status: 'reopened', reopened_by: 'user_local', reopened_at: at, reopen_reason: body?.reason }));
    return h.ok({ closed_through: locked(), previous: '2026-10-31', reopened: rows });
  }
  if (path === '/accountants' && method === 'GET') return h.ok({ items: ledger.accountants, can_manage: h.owner, me: false });
  if (path === '/accountants' && method === 'POST') {
    if (!h.owner) return h.refuse(403, 'forbidden', 'Only workspace owners can do this.');
    const item = { user_id: body?.user_id, added_by: 'user_local', added_at: at };
    ledger.accountants.push(item);
    return h.ok(item, 201);
  }
  const accountantMatch = path.match(/^\/accountants\/([^/]+)$/);
  if (accountantMatch && method === 'DELETE') {
    ledger.accountants = ledger.accountants.filter((item) => item.user_id !== decodeURIComponent(accountantMatch[1]));
    return { status: 204, body: null };
  }
  return null;
}

const CSV_HEADERS = { 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' };

/** The Business mock plus the books routes (registered last, so they answer first). */
export async function mockBooks(page_: Page, options: BooksOptions = {}): Promise<BooksMock> {
  await mockBusiness(page_, { role: options.role === 'member' ? 'member' : 'owner' });
  const state = booksState(options);
  const mock: BooksMock = { calls: [], state };
  await page_.route('http://localhost:8000/api/v1/business/books/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postData() ? request.postDataJSON() as Json : null;
    mock.calls.push({ method: request.method(), path: url.pathname, body, key: request.headers()['idempotency-key'] ?? null });
    if (url.pathname.endsWith('.csv')) {
      const name = url.pathname.endsWith('bills.csv') ? 'business-bills.csv' : url.pathname.endsWith('group.csv') ? 'group-pnl-2026-01-01-2026-12-31.csv'
        : url.pathname.endsWith('statements.csv') ? 'business-statements-2026-01-01-2026-12-31.csv' : 'business-pnl-2026-01-01-2026-12-31.csv';
      return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': `attachment; filename="${name}"`, ...CSV_HEADERS }, body: 'section,line,currency,amount\n' });
    }
    const reply = booksReply(state, request.method(), url, body);
    if (!reply) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    if (reply.status === 204) return route.fulfill({ status: 204 });
    return route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.body) });
  });
  // The active members an owner can name accountant (Books, Settings).
  await page_.route('http://localhost:8000/api/v1/workspace-members**', (route: Route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
    { tenant_id: 'tenant_local', workspace_id: 'workspace_local', user_id: 'user_local', role: 'owner', active: true, email: 'owner@example.com', created_at: at, updated_at: at },
    { tenant_id: 'tenant_local', workspace_id: 'workspace_local', user_id: 'member_local', role: 'member', active: true, email: 'member@example.com', created_at: at, updated_at: at }]) }));
  return mock;
}
