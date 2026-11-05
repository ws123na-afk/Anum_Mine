import type { Page, Route } from '@playwright/test';

// Ready to sell (docs/selling-readiness.md): a fake of the readiness checklist, the SPF lookup, the company
// details and legal facts, the Moyasar and Stripe settings, a refund of a card payment (Moyasar or Stripe) and the
// public legal facts.
// Register it after mockWorkspaceApi and mockBilling: Playwright tries the newest route first.
const now = new Date().toISOString();
const later = new Date(Date.now() + 10 * 86400_000).toISOString();
const earlier = new Date(Date.now() - 20 * 86400_000).toISOString();
const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });

export const COMPANY = {
  legal_name: 'Example Business Group LLC', trading_name: 'ANUM', registration_number: '1010000000', registration_country: 'SA',
  registration_authority: 'Ministry of Commerce', address: 'Olaya Street, Riyadh', contact_email: 'hello@example.com', support_contact: '',
  phone: '', vat_number: '300000000000003', dpo_name: 'Privacy Lead', dpo_email: 'privacy@example.com', security_email: '',
  server_location: 'Oracle Cloud, Riyadh (Saudi Arabia)', hosting_provider: 'oracle', governing_law: '',
};
const EMPTY_COMPANY = Object.fromEntries(Object.keys(COMPANY).map((k) => [k, '']));
const TERMS = [
  { key: 'EFFECTIVE_DATE', label: 'Effective date', help: 'The date the reviewed documents take effect.', generated: false },
  { key: 'BREACH_NOTICE_HOURS', label: 'Hours to tell a customer about a breach', help: 'A number of hours, for example 48.', generated: false },
  { key: 'MINIMUM_AGE', label: 'Minimum age', help: 'For example: 18.', generated: false },
];
const DOCS = ['terms', 'privacy', 'dpa', 'aup', 'subprocessors'] as const;

export interface SellingState { legal: unknown[]; moyasar: unknown[]; refunds: unknown[]; dns: string[]; publicAuth: (string | null)[]; settings: unknown[] }

/** ``provider``: which card provider took the paid invoice's payment. ``stripeOn``: Stripe is on (a UAE company) and Moyasar off. */
export interface SellingOptions { ready?: boolean; companySet?: boolean; provider?: 'moyasar' | 'stripe'; stripeOn?: boolean }

export async function mockSelling(page: Page, { ready = false, companySet = false, provider = 'moyasar', stripeOn = false }: SellingOptions = {}): Promise<SellingState> {
  const state: SellingState = { legal: [], moyasar: [], refunds: [], dns: [], publicAuth: [], settings: [] };
  const currency = provider === 'stripe' ? 'AED' : 'SAR';
  let company: Record<string, string> = companySet ? { ...COMPANY } : { ...EMPTY_COMPANY };
  let moyasar = { enabled: false, has_secret_key: false, has_webhook_secret: false, test_mode: null as boolean | null, webhook_url: '/api/v1/billing/webhooks/moyasar/anum', events: ['payment_paid', 'payment_refunded'] };
  const values = () => ({
    LEGAL_ENTITY_NAME: company.legal_name || null, REGISTERED_ADDRESS: company.address || null, PRIVACY_CONTACT_EMAIL: company.dpo_email || company.contact_email || null,
    TRADE_LICENCE_NUMBER: company.registration_number || null, SERVICE_URL: 'https://app.example', WEB_HOST: 'app.example', CONVERSATION_RETENTION_DAYS: '30',
    IN_USE_MOYASAR: moyasar.enabled ? 'Yes' : 'No', EFFECTIVE_DATE: null,
  });
  const missing = () => Object.fromEntries(DOCS.map((d) => [d, company.legal_name ? ['EFFECTIVE_DATE'] : ['EFFECTIVE_DATE', 'LEGAL_ENTITY_NAME']]));
  const profile = () => ({
    company, legal_terms: {}, published_docs: [], terms: TERMS, values: values(), missing: missing(),
    generated: [{ key: 'SERVICE_URL', value: 'https://app.example' }, { key: 'CONVERSATION_RETENTION_DAYS', value: '30' }, { key: 'IN_USE_MOYASAR', value: moyasar.enabled ? 'Yes' : 'No' }],
    required_company: ['legal_name', 'registration_number', 'registration_country', 'address', 'contact_email', 'dpo_email', 'server_location'],
    updated_by: company.legal_name ? 'user_local' : null, updated_at: company.legal_name ? now : null, public_issuer: true,
  });
  const item = (key: string, done: boolean, fix: string, detail: Record<string, unknown>, stateOverride?: string) => ({ key, state: stateOverride ?? (done ? 'done' : 'missing'), fix, detail });
  const readiness = () => {
    const companyDone = Boolean(company.legal_name && company.dpo_email);
    const items = [
      item('payment_provider', ready || moyasar.enabled || stripeOn, 'payments', {
        moyasar: moyasar.enabled ? 'ready' : 'off', stripe: stripeOn ? 'ready' : 'off', active: moyasar.enabled ? 'moyasar' : stripeOn ? 'stripe' : null,
        currencies: moyasar.enabled ? ['SAR', 'AED', 'USD'] : stripeOn ? ['AED', 'SAR', 'USD', 'EUR', 'GBP'] : [], moyasar_optional: !moyasar.enabled, live_keys: ready, bank_transfer: true,
      }, ready ? 'done' : moyasar.enabled || stripeOn ? 'warning' : 'missing'),
      item('company_details', ready || companyDone, 'company', { missing: companyDone || ready ? [] : ['legal_name', 'dpo_email'] }),
      item('mail_sending', ready, 'mail', { mailbox_connected: ready, smtp_configured: false }),
      item('backups_local', true, 'backups', { recorded: true, ok: true, finished_at: earlier, last_success_at: earlier, keep_days: 7 }),
      item('backups_offsite', ready, 'backups', { recorded: true, ok: ready, finished_at: earlier, last_success_at: null }),
      item('legal_pages', ready, 'legal', { missing: { terms: ready ? 0 : 20, privacy: ready ? 0 : 18, dpa: 0, aup: 0, subprocessors: 0 }, published: ready ? [...DOCS] : [] }),
      item('plans', true, 'plans', { active: 3, published: 3 }),
      item('public_issuer', true, 'env', { configured: true, is_you: true, web_app_url: true }),
    ];
    const done = items.filter((i) => i.state === 'done').length;
    return { items, done, total: items.length, ready: done === items.length, mail_domain: 'example.com', checked_at: now };
  };
  await page.route('http://localhost:8000/api/v1/platform/readiness**', async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (path.endsWith('/dns')) {
      const domain = (request.postDataJSON() as { domain: string }).domain;
      state.dns.push(domain);
      return json(route, { domain, checked_at: now, spf: { record: 'v=spf1 ~all', status: 'no_senders', senders: [], all: '~all', error: null }, dmarc: { record: 'v=DMARC1; p=none', policy: 'none', status: 'monitor', error: null } });
    }
    return json(route, readiness());
  });
  await page.route('http://localhost:8000/api/v1/platform/legal', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (request.method() === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.legal.push(body);
      company = Object.fromEntries(Object.keys(COMPANY).map((k) => [k, String(body[k] ?? '')]));
    }
    return json(route, profile());
  });
  await page.route('http://localhost:8000/api/v1/platform/billing/providers/moyasar', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (request.method() === 'PUT') {
      const body = request.postDataJSON() as { enabled: boolean; secret_key: string | null; webhook_secret: string | null };
      state.moyasar.push(body);
      moyasar = { ...moyasar, enabled: body.enabled, has_secret_key: moyasar.has_secret_key || Boolean(body.secret_key), has_webhook_secret: moyasar.has_webhook_secret || Boolean(body.webhook_secret), test_mode: body.secret_key ? body.secret_key.startsWith('sk_test_') : moyasar.test_mode };
    }
    return json(route, moyasar);
  });
  // The Stripe settings (a UAE company's account): keys write-only, the webhook address and events to pick in Stripe.
  let stripe = { stripe_enabled: stripeOn, has_stripe_secret_key: stripeOn, has_stripe_webhook_secret: stripeOn, stripe_test_mode: stripeOn ? true : null };
  const settings = () => ({
    issuer_name: 'Example Business Group LLC', issuer_address: 'Business Bay, Dubai', issuer_vat_number: '', issuer_email: 'billing@example.com', bank_name: 'Emirates NBD',
    bank_account_name: 'Example Business Group LLC', bank_iban: 'AE070331234567890123456', bank_swift: 'EBILAEAD', invoice_prefix: 'INV', credit_note_prefix: 'CN',
    payment_terms_days: 14, tax_rules: { AE: 500 }, default_tax_bp: 0, reminder_days: [1, 7, 14], grace_days: 7, sending_workspace_id: null, ...stripe,
    moyasar_enabled: moyasar.enabled, stripe_webhook_url: '/api/v1/billing/webhooks/stripe/anum',
    stripe_events: ['checkout.session.completed', 'charge.refunded', 'refund.created', 'refund.updated', 'refund.failed'],
  });
  await page.route('http://localhost:8000/api/v1/platform/billing/settings', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (request.method() === 'PUT') {
      const body = request.postDataJSON() as { stripe_enabled: boolean; stripe_secret_key: string | null; stripe_webhook_secret: string | null };
      state.settings.push(body);
      stripe = { stripe_enabled: body.stripe_enabled, has_stripe_secret_key: stripe.has_stripe_secret_key || Boolean(body.stripe_secret_key),
        has_stripe_webhook_secret: stripe.has_stripe_webhook_secret || Boolean(body.stripe_webhook_secret), stripe_test_mode: body.stripe_secret_key ? body.stripe_secret_key.startsWith('sk_test_') : stripe.stripe_test_mode };
    }
    return json(route, settings());
  });
  // A paid invoice whose payment came through the card provider, and its refund.
  const payment = { id: 'pay_m', amount_minor: 34385, currency, method: provider === 'stripe' ? 'stripe' : 'card', reference: provider === 'stripe' ? 'Stripe pi_example' : 'Moyasar pay_example', received_at: now, has_proof: false, proof_name: null, provider, recorded_by: `system:${provider}`, refunded_minor: 0, refundable_minor: 34385 };
  const paid = {
    id: 'inv_1', number: 'INV-000001', status: 'paid', kind: 'subscription', currency, period_start: earlier, period_end: later, issued_at: earlier, due_at: later,
    subtotal_minor: 29900, tax_rate_bp: 1500, tax_country: 'SA', tax_minor: 4485, total_minor: 34385, amount_paid_minor: 34385, amount_credited_minor: 0, amount_due_minor: 0, overdue: false, reminders_sent: 0, last_email: null,
    bill_to: { company_name: 'Acme Trading LLC', city: 'Riyadh', country: 'SA' }, issuer: { issuer_name: 'Example Business Group LLC' },
    bank_details: { enabled: true, bank_name: 'Riyad Bank', account_name: 'ANUM', iban: 'SA0380000000608010167519', swift: 'RIBLSARI' },
    lines: [{ id: 'l1', description: 'Starter, monthly', quantity: 1, unit_minor: 29900, amount_minor: 29900, kind: 'plan', period_start: earlier, period_end: later }],
    payments: [payment], credit_notes: [] as unknown[], refunds: [] as unknown[],
  };
  await page.route('http://localhost:8000/api/v1/platform/billing/organizations/acme/invoices/inv_1**', async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (path.endsWith('/refunds') && request.method() === 'POST') {
      const body = request.postDataJSON() as { amount_minor: number; reason: string };
      state.refunds.push(body);
      const refund = { id: 'refund_1', payment_id: 'pay_m', amount_minor: body.amount_minor, currency, provider, status: 'succeeded', reason: body.reason, credit_note_id: 'cn_1', failure: null, created_at: now };
      paid.amount_paid_minor -= body.amount_minor; paid.amount_credited_minor += body.amount_minor;
      payment.refunded_minor += body.amount_minor; payment.refundable_minor -= body.amount_minor;
      paid.credit_notes = [{ id: 'cn_1', number: 'CN-000001', reason: `Refund: ${body.reason}`, subtotal_minor: 0, tax_minor: 0, total_minor: body.amount_minor, issued_at: now }];
      paid.refunds = [refund];
      return json(route, { refund, invoice: paid }, 201);
    }
    if (path.endsWith('/inv_1')) return json(route, paid);
    return route.fallback();
  });
  await page.route('http://localhost:8000/api/v1/public/legal', (route) => {
    state.publicAuth.push(route.request().headers().authorization ?? null);
    return json(route, { configured: true, company: Object.fromEntries(Object.entries(company).filter(([k]) => k !== 'hosting_provider')), values: values(), published: [], missing: missing() });
  });
  return state;
}
