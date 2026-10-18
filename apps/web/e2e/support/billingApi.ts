import type { Page, Route } from '@playwright/test';

// Customer billing (docs/billing.md): a fake /api/v1/billing for one organisation billing admin,
// the platform console under /api/v1/platform/billing, and the public plan list. Register it after
// mockWorkspaceApi: Playwright tries the newest route first.
const now = new Date().toISOString();
const later = new Date(Date.now() + 10 * 86400_000).toISOString();
const earlier = new Date(Date.now() - 20 * 86400_000).toISOString();
const limits = (seats: number | null, workspaces: number | null, runs: number | null, storage: number | null, messages: number | null) =>
  ({ seats, workspaces, agent_runs_per_month: runs, storage_gb: storage, messages_per_month: messages });

export const plans = [
  { id: 'plan_solo', code: 'solo', name: 'Solo', description: 'One person getting started.', price_minor: 9900, currency: 'SAR', interval: 'month', trial_days: 14, per_seat: false, limits: limits(1, 1, 100, 1, 1000), features: ['Email assistant'] },
  { id: 'plan_starter', code: 'starter', name: 'Starter', description: 'Small teams.', price_minor: 29900, currency: 'SAR', interval: 'month', trial_days: 14, per_seat: false, limits: limits(5, 2, 1000, 10, 10000), features: ['Email assistant', 'Shared inbox'] },
  { id: 'plan_business', code: 'business', name: 'Business', description: 'Growing companies.', price_minor: 79900, currency: 'SAR', interval: 'month', trial_days: 14, per_seat: false, limits: limits(25, 10, null, 100, null), features: ['Everything in Starter', 'Priority support'] },
];

const subscription = {
  id: 'sub_1', plan_id: 'plan_starter', plan_code: 'starter', plan_name: 'Starter', price_minor: 29900, currency: 'SAR', interval: 'month', per_seat: false,
  limits: plans[1].limits, features: plans[1].features, seats: 1, status: 'active', trial_end: null, current_period_start: earlier, current_period_end: later,
  cancel_at_period_end: false, cancelled_at: null, pending_plan_id: null, pending_seats: null, paused_at: null, past_due_since: null, grace_until: null,
};
const bank = { enabled: true, bank_name: 'Riyad Bank', account_name: 'ANUM Platform LLC', iban: 'SA0380000000608010167519', swift: 'RIBLSARI' };
const account = { display_name: 'Acme Trading', contact_name: 'Sara Ali', contact_email: 'billing@acme.example', company_name: 'Acme Trading LLC', address_line1: 'King Fahd Road 1', address_line2: '', city: 'Riyadh', postal_code: '12211', country: 'SA', vat_number: '300000000000003', credit_balance_minor: 0, state: 'active' };
export const invoice = {
  id: 'inv_1', number: 'INV-000001', status: 'open', kind: 'subscription', currency: 'SAR', period_start: earlier, period_end: later, issued_at: earlier, due_at: later,
  subtotal_minor: 29900, tax_rate_bp: 1500, tax_country: 'SA', tax_minor: 4485, total_minor: 34385, amount_paid_minor: 0, amount_credited_minor: 0, amount_due_minor: 34385, overdue: false, reminders_sent: 0, last_email: null,
};
const detail = (extra: Record<string, unknown> = {}) => ({
  ...invoice, bill_to: { company_name: 'Acme Trading LLC', address_line1: 'King Fahd Road 1', city: 'Riyadh', postal_code: '12211', country: 'SA', vat_number: '300000000000003' },
  issuer: { issuer_name: 'ANUM Platform LLC', issuer_address: 'Olaya Street, Riyadh', issuer_vat_number: '311111111100003' }, bank_details: bank,
  lines: [{ id: 'line_1', description: 'Starter, monthly', quantity: 1, unit_minor: 29900, amount_minor: 29900, kind: 'plan', period_start: earlier, period_end: later }],
  payments: [], credit_notes: [], ...extra,
});
const meters = [
  { key: 'seats', used: 3, limit: 5, unit: 'seats' }, { key: 'workspaces', used: 1, limit: 2, unit: 'workspaces' }, { key: 'agent_runs', used: 120, limit: 1000, unit: 'runs' },
  { key: 'storage', used: 2 * 1024 ** 3, limit: 10 * 1024 ** 3, unit: 'bytes' }, { key: 'messages', used: 9500, limit: 10000, unit: 'messages' },
];

export interface BillingState { changes: unknown[]; previews: string[]; pdfs: string[]; payments: unknown[]; accounts: unknown[]; publicAuth: (string | null)[]; signups: unknown[]; dismissed: number }

export async function mockBilling(page: Page, { role = 'owner', onboarding = true }: { role?: 'owner' | 'member'; onboarding?: boolean } = {}): Promise<BillingState> {
  const state: BillingState = { changes: [], previews: [], pdfs: [], payments: [], accounts: [], publicAuth: [], signups: [], dismissed: 0 };
  let current = { ...subscription }, issued: Record<string, unknown>[] = [invoice], paid = false;
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' }, body: JSON.stringify(body) });
  const denied = { error: { code: 'forbidden', message: 'Only a workspace owner of this organisation or an organisation administrator (realm role org_admin) can see and change billing.', correlation_id: 'c' } };
  await page.route('http://localhost:8000/api/v1/billing/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1/billing', ''), method = request.method();
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' } });
    if (role === 'member') return json(route, denied, 403);
    if (path === '/overview') return json(route, { account, subscription: current, usage: { period: '2026-10', meters }, state: 'active', open_balance_minor: paid ? 0 : 34385, overdue: false, currency: 'SAR', payment: { bank_transfer: bank, card_enabled: false }, issuer: { name: 'ANUM Platform LLC', email: 'billing@anum.example' } });
    if (path === '/plans') return json(route, plans.map((p) => ({ ...p, current: p.id === current.plan_id })));
    if (path === '/subscription/preview') {
      const id = url.searchParams.get('plan_id') ?? '';
      state.previews.push(id);
      if (id === 'plan_solo') return json(route, { effective: 'period_end', amount_minor: 0, tax_minor: 0, total_minor: 0, credit_minor: 0, blocked_by: [{ key: 'seats', used: 3, limit: 1 }, { key: 'workspaces', used: 2, limit: 1 }], currency: 'SAR' });
      return json(route, { effective: 'now', amount_minor: 25000, tax_minor: 3750, total_minor: 28750, credit_minor: 0, blocked_by: [], currency: 'SAR' });
    }
    if (path === '/subscription/change') {
      const body = request.postDataJSON() as { plan_id: string };
      state.changes.push(body);
      const plan = plans.find((p) => p.id === body.plan_id)!;
      current = { ...current, plan_id: plan.id, plan_code: plan.code, plan_name: plan.name, price_minor: plan.price_minor, limits: plan.limits, features: plan.features };
      const proration = { ...invoice, id: 'inv_2', number: 'INV-000002', kind: 'proration', subtotal_minor: 25000, tax_minor: 3750, total_minor: 28750, amount_due_minor: 28750, issued_at: now };
      issued = [proration, ...issued];
      return json(route, { subscription: current, invoice: proration });
    }
    if (path === '/account' && method === 'PUT') { state.accounts.push(request.postDataJSON()); return json(route, { ...account, ...request.postDataJSON() }); }
    if (path === '/invoices') {
      const q = (url.searchParams.get('q') ?? '').toUpperCase(), status = url.searchParams.get('status') ?? '';
      const items = issued.filter((i) => (!q || String(i.number).includes(q)) && (!status || i.status === status));
      return json(route, { items, total: items.length, limit: 20, offset: 0 });
    }
    if (path === '/invoices/inv_1') return json(route, detail());
    if (path === '/invoices/inv_1/pdf') { state.pdfs.push(request.headers()['x-user-id'] ?? request.headers().authorization ?? ''); return route.fulfill({ status: 200, contentType: 'application/pdf', headers: { 'content-disposition': 'attachment; filename="INV-000001.pdf"' }, body: '%PDF-1.4\n%fake invoice\n' }); }
    if (path === '/onboarding') return onboarding ? json(route, { items: [{ key: 'invite_team', done: true }, { key: 'connect_email', done: false }, { key: 'choose_plan', done: true }, { key: 'add_payment_details', done: false }], complete: false, dismissed: state.dismissed > 0 }) : json(route, { error: { code: 'not_found', message: 'No billing account.', correlation_id: 'c' } }, 404);
    if (path === '/onboarding/dismiss') { state.dismissed += 1; return json(route, { dismissed: true }); }
    return json(route, { error: { code: 'not_found', message: `unmocked ${method} ${path}`, correlation_id: 'c' } }, 404);
  });

  // The platform console (the local development user is a platform operator).
  await page.route('http://localhost:8000/api/v1/platform/billing/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1/platform/billing', ''), method = request.method();
    if (path === '/summary') return json(route, { currency: 'SAR', mrr_minor: paid ? 29900 : 29900, arr_minor: 358800, counts: { trialing: 1, active: 1, past_due: 0, paused: 0, cancelled: 0 }, organisations: 2, read_only: 0, open_invoices: paid ? 0 : 1, open_minor: paid ? 0 : 34385, overdue_minor: 0, collected_this_month_minor: paid ? 34385 : 0 });
    if (path === '/subscriptions') {
      const rows = [
        { tenant_id: 'acme', display_name: 'Acme Trading', plan_name: 'Starter', status: 'active', price_minor: 29900, currency: 'SAR', interval: 'month', mrr_minor: 29900, current_period_end: later, open_balance_minor: paid ? 0 : 34385, state: 'active' },
        { tenant_id: 'falcon', display_name: 'Falcon Logistics', plan_name: 'Business', status: 'trialing', price_minor: 79900, currency: 'SAR', interval: 'month', mrr_minor: 0, current_period_end: later, open_balance_minor: 0, state: 'active' },
      ];
      const q = (url.searchParams.get('q') ?? '').toLowerCase(), status = url.searchParams.get('status') ?? '';
      const items = rows.filter((r) => (!q || r.display_name.toLowerCase().includes(q)) && (!status || r.status === status));
      return json(route, { items, total: items.length, limit: 20, offset: 0 });
    }
    if (path === '/plans') return json(route, plans.map((p, i) => ({ ...p, published: true, active: true, sort_order: i, created_at: now, updated_at: now })));
    if (path === '/organizations/acme') return json(route, { tenant_id: 'acme', account, subscription, usage: { period: '2026-10', meters } });
    if (path === '/organizations/acme/invoices') return json(route, { items: [paid ? { ...invoice, status: 'paid', amount_paid_minor: 34385, amount_due_minor: 0 } : invoice], total: 1, limit: 20, offset: 0 });
    if (path === '/organizations/acme/invoices/inv_1') return json(route, paid ? detail({ status: 'paid', amount_paid_minor: 34385, amount_due_minor: 0 }) : detail());
    if (path === '/organizations/acme/invoices/inv_1/payments' && method === 'POST') {
      state.payments.push(request.postDataJSON());
      paid = true;
      return json(route, { payment_id: 'pay_1', invoice: detail({ status: 'paid', amount_paid_minor: 34385, amount_due_minor: 0, payments: [{ id: 'pay_1', amount_minor: 34385, currency: 'SAR', method: 'bank_transfer', reference: 'TRX-778', received_at: now, has_proof: true, proof_name: 'receipt.pdf', provider: null, recorded_by: 'user_local' }] }) }, 201);
    }
    return json(route, { error: { code: 'not_found', message: `unmocked ${method} ${path}`, correlation_id: 'c' } }, 404);
  });
  await page.route('http://localhost:8000/api/v1/platform/organizations', (route) => json(route, [{ tenant_id: 'acme', display_name: 'Acme Trading', plan: 'standard', first_workspace_id: 'acme_main', created_at: earlier, workspaces: 1 }]));
  return state;
}

/** The public plan list (no sign-in). */
export async function mockPublicPlans(page: Page, state: Pick<BillingState, 'publicAuth'>, { signupEnabled = true }: { signupEnabled?: boolean } = {}): Promise<void> {
  await page.route('http://localhost:8000/api/v1/public/billing/plans', (route) => {
    state.publicAuth.push(route.request().headers().authorization ?? null);
    return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify({ signup_enabled: signupEnabled, currency: 'SAR', tax: { country: 'SA', rate_bp: 1500 }, plans }) });
  });
}
