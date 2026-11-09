import type { Page, Route } from '@playwright/test';

// The fixed asset register, loan schedules, revaluation and group balance sheet API (docs/business-books.md#fixed-asset-register)
// for the browser tests. Registered after mockBooks, so it answers these routes first. Lenders, assets, rates and amounts
// are SYNTHETIC. The server works out every figure; the mock keeps a little state so the page's changes show.
type Json = Record<string, unknown>;
export interface AssetsOptions { canEdit?: boolean; empty?: boolean; failAssets?: number; missingRate?: boolean; me?: string }
export interface AssetsMock { calls: { method: string; path: string; body: Json | null; key: string | null }[] }

const at = '2026-11-01T09:00:00Z';
const CSV_HEADERS = { 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' };

function asset(id: string, name: string, extra: Json = {}): Json {
  return { id, name, category: 'equipment', source_kind: 'bill', source_id: `bill_${id}`, source_live: true, acquired_on: '2026-06-15', currency: 'SAR',
    cost: '1200.00', useful_life_months: 24, residual_value: '0.00', method: 'straight_line', rate_pct: null, depreciation_start: '2026-06-01', status: 'active',
    disposed_on: null, disposal_kind: null, proceeds: '0.00', accumulated_depreciation: '300.00', impairment: '0.00', net_book_value: '900.00', as_of: '2026-11-09',
    gain_or_loss: null, version: 1, pending_impairments: 0, ...extra };
}

function schedule(): Json[] {
  return Array.from({ length: 24 }, (_, index) => {
    const month = new Date(Date.UTC(2026, 5 + index, 1)).toISOString().slice(0, 10);
    return { month, amount: '50.00', accumulated: `${(50 * (index + 1)).toFixed(2)}`, state: index < 4 ? 'closed' : index < 6 ? 'posted' : 'scheduled' };
  });
}

function loanRow(extra: Json = {}): Json {
  return { id: 'bloan_1', lender: 'Example Bank', principal: '12000.00', currency: 'SAR', annual_rate: '12', start_date: '2026-08-01', term_months: 12,
    frequency: 'monthly', method: 'annuity', early_repayment: 'reduce_term', received_adjustment_id: null, status: 'active', version: 1, payment: '1066.19', periods: 12,
    maturity: '2027-08-01', outstanding: '11053.81', interest_accrued: '107.00', repaid: '946.19', next_due: '2026-11-01', next_payment: '1066.19', repayments: 1,
    void_reason: '', ...extra };
}

function loanDetail(repaid: boolean): Json {
  const rows = Array.from({ length: 12 }, (_, index) => ({ number: index + 1, due: new Date(Date.UTC(2026, 8 + index, 1)).toISOString().slice(0, 10),
    payment: '1066.19', interest: index === 0 ? '120.00' : '110.54', principal: index === 0 ? '946.19' : '955.65', balance: `${(12000 - 950 * (index + 1)).toFixed(2)}` }));
  return { ...loanRow(), schedule: rows, revised: rows.slice(2),
    actual: rows.map((row, index) => ({ ...row, paid: index === 0 || (repaid && index === 1) ? '1066.19' : '0.00', paid_interest: index === 0 ? '120.00' : '0.00',
      paid_principal: index === 0 ? '946.19' : '0.00', difference: index === 0 ? '0.00' : '-1066.19', state: index === 0 ? 'paid' : index === 1 ? (repaid ? 'paid' : 'overdue') : 'upcoming' })),
    repayment_rows: [{ id: 'brepay_1', loan_id: 'bloan_1', paid_on: '2026-09-01', amount: '1066.19', interest: '120.00', principal: '946.19', currency: 'SAR', bill_id: null,
      adjustment_id: null, status: 'active', created_by: 'user_local', created_at: at, void_reason: '' }],
    accruals: [{ month_end: '2026-09-30', amount: '107.00' }, { month_end: '2026-10-31', amount: '110.54' }], can_edit: true };
}

/** The group balance sheet the Books mock also answers (All businesses), so the existing group specs see a finished card. */
export function groupBalanceSheet(): Json {
  const row = (key: string, a: string, b: string, elim: string, total: string) => ({ key, businesses: { ws_anum: a, ws_tyre: b }, eliminations: elim, total });
  return {
    enough: true, as_of: '2026-11-09', currency: 'SAR', skipped: [], available: [], included: ['ws_anum', 'ws_tyre'], not_converted: [],
    rates: [{ workspace_id: 'ws_tyre', from_currency: 'AED', to_currency: 'SAR', rate: '1.02', as_of: '2026-11-09', source: 'Example reference' }],
    balanced: true, difference: '0.00', complete: true,
    businesses: [{ workspace_id: 'ws_anum', name: 'Example ANUM Business', base_currency: 'SAR', complete: true, notes: [], included: true, translated: false },
      { workspace_id: 'ws_tyre', name: 'Example Tyre Business', base_currency: 'AED', complete: true, notes: [], included: true, translated: true }],
    rows: [row('current_assets', '1000.00', '4180.00', '-375.00', '4805.00'), row('non_current_assets', '0.00', '0.00', '0.00', '0.00'),
      row('total_assets', '1000.00', '4180.00', '-375.00', '4805.00'), row('current_liabilities', '0.00', '380.00', '-380.00', '0.00'),
      row('non_current_liabilities', '0.00', '0.00', '0.00', '0.00'), row('total_liabilities', '0.00', '380.00', '-380.00', '0.00'),
      row('capital', '1000.00', '3750.00', '0.00', '4750.00'), row('retained_earnings', '0.00', '0.00', '0.00', '0.00'),
      row('translation_reserve', '0.00', '50.00', '0.00', '50.00'), row('intercompany_difference', '0.00', '0.00', '5.00', '5.00'),
      row('total_equity', '1000.00', '3800.00', '5.00', '4805.00'), row('liabilities_and_equity', '1000.00', '4180.00', '-375.00', '4805.00')],
  };
}

export async function mockAssets(page: Page, options: AssetsOptions = {}): Promise<AssetsMock> {
  const mock: AssetsMock = { calls: [] };
  const canEdit = options.canEdit !== false;
  let failures = options.failAssets ?? 0;
  const assets: Json[] = options.empty ? [] : [
    asset('basset_1', 'Example delivery van', { cost: '1200.00' }),
    asset('basset_2', 'Example laptop', { cost: '600.00', useful_life_months: 12, accumulated_depreciation: '250.00', net_book_value: '350.00', pending_impairments: 1 }),
    asset('basset_3', 'Example forklift', { status: 'disposed', disposed_on: '2026-10-10', disposal_kind: 'sale', proceeds: '1500.00', gain_or_loss: '400.00',
      net_book_value: '0.00' }),
  ];
  const impairments: Json[] = [{ id: 'bimp_1', asset_id: 'basset_2', entry_date: '2026-10-20', amount: '50.00', currency: 'SAR', reason: 'Example screen damage',
    status: 'pending', requested_by: 'owner_two', decided_by: null, decided_at: null, created_at: at, version: 1 }];
  let loans: Json[] = options.empty ? [] : [loanRow()];
  let repaid = false;
  let revalued = false;
  await page.route(/http:\/\/localhost:8000\/api\/v1\/business\/books\/(assets|loans|ledger\/revaluation|group\/balance-sheet)/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business/books', '');
    const body = request.postData() ? request.postDataJSON() as Json : null;
    mock.calls.push({ method: request.method(), path, body, key: request.headers()['idempotency-key'] ?? null });
    const json = (status: number, value: unknown) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const refuse = () => json(403, { detail: 'Only an owner or an accountant of this business can do this. An owner can name accountants in Books, Settings.', code: 'forbidden' });
    if (path === '/assets.csv') return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="fixed-assets-2026-01-01-2026-12-31.csv"', ...CSV_HEADERS }, body: 'section,name\n' });
    if (path === '/assets' && request.method() === 'GET') {
      if (failures > 0) { failures -= 1; return json(500, { detail: 'The register could not be read.' }); }
      const status = url.searchParams.get('status') ?? 'all';
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const items = assets.filter((item) => (status === 'all' || item.status === status) && (!q || String(item.name).toLowerCase().includes(q)));
      return json(200, { items, total: items.length, limit: 25, offset: 0, period: { from: '2026-01-01', to: '2026-12-31' }, as_of: '2026-11-09',
        roll_forward: assets.length ? [{ currency: 'SAR', opening_cost: '0.00', additions: '3000.00', disposals_cost: '1200.00', closing_cost: '1800.00',
          opening_depreciation: '0.00', depreciation: '650.00', impairment: '0.00', disposals_depreciation: '100.00', closing_depreciation: '550.00', opening_nbv: '0.00',
          closing_nbv: '1250.00' }] : [], base_currency: 'SAR', closed_through: '2026-09-30', can_edit: canEdit,
        pending_impairments: impairments.filter((row) => row.status === 'pending').length });
    }
    const assetMatch = path.match(/^\/assets\/([^/]+)(\/.*)?$/);
    if (assetMatch) {
      const found = assets.find((item) => item.id === assetMatch[1]);
      if (!found) return json(404, { detail: 'Asset not found.', code: 'asset_not_found' });
      const rest = assetMatch[2] ?? '';
      const detail = () => ({ ...found, schedule: schedule(), impairments: impairments.filter((row) => row.asset_id === found.id), can_edit: canEdit, disposal: null,
        closed_through: '2026-09-30', me: options.me ?? 'user_local' });
      if (request.method() === 'GET' && !rest) return json(200, detail());
      if (!canEdit) return refuse();
      if (request.method() === 'PATCH' && !rest) {
        if (body?.useful_life_months === 6) return json(409, { detail: 'This changes depreciation already in a closed period (closed through 2026-09-30). An owner can reopen the period with a reason.', code: 'period_locked' });
        Object.assign(found, body, { version: Number(found.version) + 1 });
        return json(200, detail());
      }
      if (rest === '/dispose') { Object.assign(found, { status: 'disposed', disposed_on: body?.disposed_on, disposal_kind: body?.kind, proceeds: body?.proceeds,
        gain_or_loss: '200.00', net_book_value: '0.00' }); return json(200, detail()); }
      if (rest === '/impairments') {
        const row = { id: `bimp_${impairments.length + 1}`, asset_id: found.id, currency: 'SAR', status: 'pending', requested_by: options.me ?? 'user_local', decided_by: null,
          decided_at: null, created_at: at, version: 1, ...body };
        impairments.push(row); return json(201, row);
      }
      const decision = rest.match(/^\/impairments\/([^/]+)\/(approve|reject)$/);
      if (decision) {
        const row = impairments.find((item) => item.id === decision[1]);
        if (!row) return json(404, { detail: 'Impairment not found.' });
        if (row.requested_by === (options.me ?? 'user_local')) return json(403, { detail: 'Another owner or accountant must approve an impairment: not the person who asked for it.', code: 'same_person' });
        Object.assign(row, { status: decision[2] === 'approve' ? 'approved' : 'rejected', decided_by: options.me ?? 'user_local', decided_at: at, version: 2 });
        found.pending_impairments = 0;
        return json(200, row);
      }
    }
    if (path === '/loans' && request.method() === 'GET') {
      const status = url.searchParams.get('status') ?? 'active';
      const items = loans.filter((item) => status === 'all' || item.status === status);
      return json(200, { items, total: items.length, limit: 100, offset: 0, base_currency: 'SAR', can_edit: canEdit,
        match: { received: [{ id: 'badj_9', date: '2026-08-01', amount: '12000.00', currency: 'SAR', description: 'Example loan in' }], repayments: [],
          bills: [{ id: 'bill_7', date: '2026-10-01', amount: '1066.19', currency: 'SAR', description: 'Example lender bill', reference: 'L-10' }] } });
    }
    if (path === '/loans' && request.method() === 'POST') {
      if (!canEdit) return refuse();
      const made = loanRow({ id: `bloan_${loans.length + 1}`, ...body, principal: `${Number(body?.principal).toFixed(2)}`, outstanding: `${Number(body?.principal).toFixed(2)}`,
        repayments: 0, interest_accrued: '0.00' });
      loans = [...loans, made];
      return json(201, made);
    }
    const loanMatch = path.match(/^\/loans\/([^/]+)(\/.*)?$/);
    if (loanMatch) {
      const rest = loanMatch[2] ?? '';
      if (request.method() === 'GET' && !rest) return json(200, { ...loanDetail(repaid), ...loans.find((item) => item.id === loanMatch[1]), can_edit: canEdit });
      if (!canEdit) return refuse();
      if (rest === '/repayments') {
        if (Number(body?.amount) > 20000) return json(422, { detail: 'That is more than is owed on that day (11160.81 SAR).', code: 'more_than_owed', remaining: '11160.81' });
        repaid = true;
        return json(201, { id: 'brepay_2', loan_id: loanMatch[1], paid_on: body?.paid_on, amount: body?.amount, interest: '110.54', principal: '955.65', currency: 'SAR',
          bill_id: body?.bill_id ?? null, adjustment_id: body?.adjustment_id ?? null, status: 'active', created_by: 'user_local', created_at: at, void_reason: '' });
      }
      return json(200, { ok: true });
    }
    if (path === '/ledger/revaluation') {
      if (request.method() === 'POST') {
        if (!canEdit) return refuse();
        revalued = true;
        return json(200, { period_end: body?.period_end, base_currency: 'SAR', posted: 1, reversed: 0, missing: options.missingRate ? ['AED'] : [], currencies: [] });
      }
      const periodEnd = url.searchParams.get('period_end') ?? '2026-10-31';
      const usd = { currency: 'USD', status: revalued ? 'revalued' : 'to_revalue', rate: '3.8', rate_date: periodEnd, source: 'Example reference', difference: '-5.00',
        accounts: [{ account_id: 'acc_ar', code: '1100', name: 'Accounts receivable', system_key: 'ar', original: '100.00', booked: '375.00', closing: '380.00', difference: '5.00' },
          { account_id: 'acc_ap', code: '2000', name: 'Accounts payable', system_key: 'ap', original: '-200.00', booked: '-750.00', closing: '-760.00', difference: '-10.00' }] };
      const aed = { currency: 'AED', status: 'missing_rate', rate: null, rate_date: null, source: null, difference: '0.00',
        accounts: [{ account_id: 'acc_ar', code: '1100', name: 'Accounts receivable', system_key: 'ar', original: '100.00', booked: '102.00', closing: null, difference: null }] };
      return json(200, { period_end: periodEnd, base_currency: 'SAR', currencies: options.empty ? [] : options.missingRate ? [aed, usd] : [usd], closed_through: '2026-09-30',
        can_edit: canEdit, ended: true });
    }
    if (path === '/group/balance-sheet') return json(200, groupBalanceSheet());
    return json(404, { detail: 'Not found' });
  });
  return mock;
}
