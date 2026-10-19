import type { Page, Route } from '@playwright/test';

// The customer portal (docs/business-portal.md): a small stateful stand-in for /api/v1/portal that follows
// the API's rules for the main path (one bearer link, offers and change orders decided once against the
// content hash the customer was shown, a 409 when it changed, invoices with the seller's bank details, a
// request opened by the customer) and its sentences for the refusals. It records every request so a spec
// can check that only the bearer link is sent (no workspace, tenant or user headers) and nothing else is called.
type Json = Record<string, unknown>;
export const PORTAL_LINK = 'anum_portal.tenant_local.workspace_foundation.bpacc_9.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd';
export const OFFER_HASH = 'a1'.repeat(32);
export const CHANGE_HASH = 'c3'.repeat(32);
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const now = () => new Date().toISOString();
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const INVALID = 'This link is not valid. Ask the business that sent it for a new one.';
const NO_DECISION = 'This link can read but not approve. Ask the business to turn on approvals for you.';
const ARCHIVED = 'This business has paused its account, so nothing can be changed now. You can still read everything.';

export interface PortalOptions {
  link?: 'ok' | 'expired' | 'revoked' | 'unknown';
  canDecide?: boolean;
  archived?: boolean;
  empty?: boolean;
  failList?: 'offers' | 'invoices';
  expiresInDays?: number;
  overdueInvoices?: number;
}
export interface PortalMock {
  requests: { method: string; path: string; headers: Record<string, string>; body: unknown }[];
  /** Calls to the API that are not portal routes. */
  leaks: string[];
  /** The business sends a new version: the hash the customer holds is now stale. */
  changeOffer: () => void;
  decisions: Json[];
}

export async function mockPortal(page: Page, options: PortalOptions = {}): Promise<PortalMock> {
  const canDecide = options.canDecide ?? true;
  const mock: PortalMock = { requests: [], leaks: [], changeOffer: () => undefined, decisions: [] };
  let offerHash = OFFER_HASH;
  const decided: Record<string, Json> = {};
  let counter = 0;
  const tickets: Json[] = [{
    id: 'btkt_1', reference: 'T-1001', title: 'Logo is cut off on mobile', severity: 'normal', status: 'resolved', project_name: 'Website rebuild', reported_at: now(), updated_at: now(), opened_here: true,
    description: 'The logo is clipped on my phone.', resolution_summary: 'We fixed the header and it is live.', resolved_at: now(), replies: [{ body: 'Thanks, we have fixed it. Please check on your phone.', sent_at: now() }],
  }];
  const offer = (): Json => ({
    id: 'bprop_1', title: 'Website rebuild', status: decided.offer || options.empty ? 'waiting' : 'waiting', currency: 'SAR', total: 5750, version: 2, sent_at: now(), valid_until: day(14), expired: false,
    decision: decided.offer ?? null, can_decide: canDecide && !decided.offer,
    summary: 'A new website for your shop.', scope: 'Design, build and launch of 8 pages.', deliverables: ['Eight pages', 'Arabic and English'], exclusions: ['Hosting'], assumptions: ['You send the content by 1 Nov'], acceptance_criteria: ['You sign off each page'],
    line_items: [{ description: 'Design', quantity: 1, unit_price: 2000, amount: 2000 }, { description: 'Build', quantity: 1, unit_price: 3000, amount: 3000 }],
    milestones: [{ title: 'Design approved', amount: 2500, due_in_days: 14, acceptance: 'You approve the design' }, { title: 'Launch', amount: 2500, due_in_days: 45, acceptance: '' }],
    subtotal: 5000, tax: 750, tax_rate_pct: 15, tax_note: 'VAT is added at 15 percent.', payment_terms: '50 percent at the start.', content_hash: offerHash,
  });
  const oldOffer = (): Json => ({ ...offer(), id: 'bprop_2', title: 'Old maintenance offer', total: 1150, version: 1, valid_until: day(-10), expired: true, can_decide: false, decision: null });
  const change = (): Json => ({
    id: 'bco_1', number: 1, title: 'Extra pages', project_name: 'Website rebuild', status: 'waiting', currency: 'SAR', total_delta: 575, timeline_days: 5, decision: decided.change ?? null, can_decide: canDecide && !decided.change, stale: false,
    request: 'Add two more pages for services.', subtotal_delta: 500, tax_delta: 75, content_hash: CHANGE_HASH,
    lines: [{ action: 'add', description: 'Services page', quantity: 2, unit_price: 250, amount: 500 }],
  });
  const staleChange = (): Json => ({ ...change(), id: 'bco_2', number: 2, title: 'Blog section', total_delta: 1150, stale: true, can_decide: false, decision: null });
  const invoice = (n: number): Json => {
    const base = { currency: 'SAR', customer_reference: `PO ${n}`, lines: [{ description: 'Design approved', quantity: 1, unit_price: 1000, tax_rate_pct: 15, total: 1150 }], subtotal: 1000, tax: 150, notes: 'Thank you.', payment_terms: 'Pay within 14 days.',
      bill_to_name: 'Acme Trading', bill_to_address: 'Riyadh', bill_to_tax_number: '300111', seller: { legal_name: 'Najd Digital Co.', address: 'Olaya Street, Riyadh', tax_label: 'VAT number', tax_number: '300123456700003', email: 'billing@najd.example', phone: '+966500000000', bank_details: 'Example Bank\nIBAN SA0000000000000000000000' }, void_reason: '' };
    if (n === 1) return { ...base, id: 'binv_1', number: 'INV-0001', status: 'sent', total: 1150, amount_paid: 400, balance: 750, issue_date: day(-40), due_date: day(-26), overdue: true, days_overdue: 26, payment_state: 'partially_paid', payments: [{ paid_on: day(-20), amount: 400, method: 'bank_transfer', reference: 'TT-5' }] };
    if (n === 2) return { ...base, id: 'binv_2', number: 'INV-0002', status: 'paid', total: 1150, amount_paid: 1150, balance: 0, issue_date: day(-60), due_date: day(-46), overdue: false, days_overdue: 0, payment_state: 'paid', payments: [{ paid_on: day(-50), amount: 1150, method: 'cash', reference: '' }] };
    return { ...base, id: 'binv_3', number: 'INV-0003', status: 'void', total: 1150, amount_paid: 0, balance: 1150, issue_date: day(-30), due_date: day(-16), overdue: false, days_overdue: 0, payment_state: 'unpaid', payments: [], void_reason: 'Issued twice' };
  };
  const summary = (item: Json) => { const { lines, subtotal, tax, notes, payment_terms, bill_to_name, bill_to_address, bill_to_tax_number, seller, payments, void_reason, ...rest } = item; void [lines, subtotal, tax, notes, payment_terms, bill_to_name, bill_to_address, bill_to_tax_number, seller, payments, void_reason]; return rest; };
  const offerSummary = (item: Json) => { const { summary: s, scope, deliverables, exclusions, assumptions, acceptance_criteria, line_items, milestones, subtotal, tax, tax_rate_pct, tax_note, payment_terms, content_hash, ...rest } = item; void [s, scope, deliverables, exclusions, assumptions, acceptance_criteria, line_items, milestones, subtotal, tax, tax_rate_pct, tax_note, payment_terms, content_hash]; return rest; };
  const changeSummary = (item: Json) => { const { request, subtotal_delta, tax_delta, content_hash, lines, ...rest } = item; void [request, subtotal_delta, tax_delta, content_hash, lines]; return rest; };
  const ticketSummary = (item: Json) => { const { description, resolution_summary, resolved_at, replies, ...rest } = item; void [description, resolution_summary, resolved_at, replies]; return rest; };
  const invoices = () => (options.empty ? [] : [invoice(1), invoice(2), invoice(3)]);
  mock.changeOffer = () => { offerHash = 'b2'.repeat(32); };

  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (!url.pathname.startsWith('/api/v1/portal/')) { mock.leaks.push(`${method} ${url.pathname}`); return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }); }
    const path = url.pathname.replace('/api/v1/portal', '');
    const body = request.postData() ? request.postDataJSON() as Json : null;
    mock.requests.push({ method, path, headers: request.headers(), body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (options.link === 'unknown') return json({ detail: INVALID }, 401);
    if (options.link === 'expired') return json({ detail: 'This link has expired. Ask the business that sent it for a new one.' }, 401);
    if (options.link === 'revoked') return json({ detail: 'This link was turned off. Ask the business that sent it for a new one.' }, 401);
    if (request.headers().authorization !== `Bearer ${PORTAL_LINK}`) return json({ detail: 'Open the link you were sent.' }, 401);
    const parts = path.split('/').filter(Boolean);

    if (path === '/me') {
      const offers = options.empty ? [] : [offer()].filter((item) => !item.decision), changes = options.empty ? [] : [change()].filter((item) => !item.decision);
      return json({
        business_name: 'Najd Digital Co.', account_name: 'Acme Trading', contact_name: 'Sara Khan', expires_at: new Date(Date.now() + (options.expiresInDays ?? 60) * 86_400_000).toISOString(), can_decide: canDecide,
        counts: { offers_waiting: offers.length, change_orders_waiting: changes.length, invoices_open: options.empty ? 0 : 1, invoices_overdue: options.empty ? 0 : (options.overdueInvoices ?? 1), tickets_open: tickets.filter((item) => item.status !== 'resolved' && item.status !== 'closed').length },
        projects: [{ id: 'bprj_1', name: 'Website rebuild' }],
      });
    }
    if (path === '/offers') return options.failList === 'offers' ? json(envelope('internal_error', 'The server could not reach its database'), 500) : json(options.empty ? [] : [offerSummary(offer()), offerSummary(oldOffer())]);
    if (parts[0] === 'offers' && parts.length === 2) return parts[1] === 'bprop_2' ? json(oldOffer()) : parts[1] === 'bprop_1' ? json(offer()) : json(envelope('not_found', 'Offer not found'), 404);
    if (parts[0] === 'offers' && parts[2] === 'decision') {
      if (options.archived) return json(envelope('forbidden', ARCHIVED), 403);
      if (!canDecide) return json(envelope('forbidden', NO_DECISION), 403);
      const input = body as Json;
      if (input.content_hash !== offerHash) return json(envelope('conflict', 'This offer changed after you opened it, so nothing was recorded. Reload the page to read the latest version.'), 409);
      decided.offer = { decision: input.decision, signer_name: input.signer_name, note: input.note ?? '', decided_at: now() };
      mock.decisions.push({ kind: 'offer', ...input });
      return json(offer());
    }
    if (path === '/change-orders') return json(options.empty ? [] : [changeSummary(change()), changeSummary(staleChange())]);
    if (parts[0] === 'change-orders' && parts.length === 2) return parts[1] === 'bco_2' ? json(staleChange()) : json(change());
    if (parts[0] === 'change-orders' && parts[2] === 'decision') {
      if (!canDecide) return json(envelope('forbidden', NO_DECISION), 403);
      const input = body as Json;
      if (input.content_hash !== CHANGE_HASH) return json(envelope('conflict', 'This change order changed after you opened it, so nothing was recorded. Reload the page to read the latest version.'), 409);
      decided.change = { decision: input.decision, signer_name: input.signer_name, note: input.note ?? '', decided_at: now() };
      mock.decisions.push({ kind: 'change', ...input });
      return json(change());
    }
    if (path === '/invoices') return options.failList === 'invoices' ? json(envelope('internal_error', 'The server could not reach its database'), 500) : json(invoices().map(summary));
    if (parts[0] === 'invoices' && parts.length === 2) return json(invoices().find((item) => item.id === parts[1]) ?? envelope('not_found', 'Invoice not found'), invoices().some((item) => item.id === parts[1]) ? 200 : 404);
    if (parts[0] === 'invoices' && parts[2] === 'print') {
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="${url.searchParams.get('lang')}"><head><meta charset="utf-8"><title>Invoice</title></head><body><h1>Invoice ${parts[1]}</h1></body></html>` });
    }
    if (path === '/tickets' && method === 'GET') return json(options.empty ? [] : tickets.map(ticketSummary));
    if (path === '/tickets' && method === 'POST') {
      const input = body as Json;
      const created = { id: `btkt_new${++counter}`, reference: `T-${1001 + tickets.length}`, title: input.title, severity: input.severity ?? 'normal', status: 'new', project_name: input.project_id ? 'Website rebuild' : null, reported_at: now(), updated_at: now(), opened_here: true,
        description: input.description, resolution_summary: '', resolved_at: null, replies: [] };
      tickets.unshift(created);
      return json(created, 201);
    }
    if (parts[0] === 'tickets' && parts.length === 2) return json(tickets.find((item) => item.id === parts[1]) ?? envelope('not_found', 'Request not found'), tickets.some((item) => item.id === parts[1]) ? 200 : 404);
    return json(envelope('not_found', 'Not found'), 404);
  });
  return mock;
}
