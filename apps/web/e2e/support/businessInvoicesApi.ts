import type { Page, Route } from '@playwright/test';
import { mockBusiness } from './businessApi';

// Business invoices and the portal's owner routes (docs/business.md#invoices, docs/business-portal.md): a
// small stateful stand-in for /api/v1/business/{invoices,billing-profile,portal} that follows the API's
// rules for the main path (a draft from milestones, the freeze and the approval request, the owner's
// approval that numbers and sends it, payments by hand, void) and its sentences for the refusals. The
// rest of Business comes from mockBusiness. Errors use the API's error envelope.
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
export const INVOICE_HASH = 'd9'.repeat(32);
export const PORTAL_TOKEN = 'anum_portal.tenant_local.workspace_foundation.bpacc_9.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd';
const MANAGE_DENIED = 'Only workspace owners can do this (the catalog, research caps, deletions and removing a do-not-contact mark).';
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

const PROFILE = {
  legal_name: 'Najd Digital Co.', address: 'Olaya Street\nRiyadh', tax_label: 'VAT number', tax_number: '300123456700003', email: 'billing@najd.example', phone: '+966500000000',
  bank_details: 'Bank: Example Bank\nIBAN: SA0000000000000000000000', footer_note: 'Thank you.', invoice_prefix: 'INV', default_due_days: 14, default_tax_rate_pct: 15,
};

export interface InvoicesMock {
  calls: { method: string; path: string; body: unknown }[];
  /** An owner approves the send request in Approvals: the invoice gets its number and is sent. */
  approveSend: (invoiceId: string) => void;
  /** An owner rejects the request: the invoice returns to a draft with the reason. */
  rejectSend: (invoiceId: string, reason: string) => void;
  seedSent: (fields?: Json) => string;
  /** A customer reports a payment from the portal: a pending notice appears on the invoice. */
  seedNotice: (invoiceId: string, fields?: Json) => string;
  accessLinks: Json[];
}

interface Options { role?: 'owner' | 'member'; listStatus?: 403 | 500; noProject?: boolean; withContractShare?: boolean }

export async function mockInvoices(page: Page, options: Options = {}): Promise<InvoicesMock> {
  const role = options.role ?? 'owner';
  await mockBusiness(page, { role });
  const invoices: Json[] = [];
  const profile: Json = { ...PROFILE };
  const access: Json[] = [];
  const decisions: Json[] = [];
  let counter = 0, sequence = 0;
  const id = (prefix: string) => `${prefix}_${++counter}`;
  const mock: InvoicesMock = { calls: [], approveSend: () => undefined, rejectSend: () => undefined, seedSent: () => '', seedNotice: () => '', accessLinks: access };
  const project = {
    id: 'bprj_1', ...scope, proposal_id: 'bprop_1', proposal_version: 1, opportunity_id: 'bopp_1', account_id: 'bacc_1', name: 'Website rebuild', status: 'active', currency: 'SAR',
    contract_total: 5750, owner_id: 'user_local', start_on: null, contract_limits: '', created_by: 'user_local', created_at: now(), updated_at: now(), account_name: 'Acme Trading',
    milestones: [], accepted_milestones: 0, accepted_value: 0, paid_value: 0,
  };
  const account = {
    id: 'bacc_1', ...scope, name: 'Acme Trading', name_key: 'acme trading', website: null, domain: null, industry: '', country: 'SA', city: 'Riyadh', notes: '', source: 'manual',
    owner_id: 'user_local', do_not_contact: false, dnc_reason: '', created_by: 'user_local', created_at: now(), updated_at: now(),
  };
  const contacts = [
    { id: 'bcon_1', ...scope, account_id: 'bacc_1', name: 'Sara Khan', role_title: 'Finance', email: 'sara@acme.example', phone: null, profile_url: null, consent_basis: 'existing_relationship', consent_note: '', consent_recorded_at: now(), do_not_contact: false, dnc_reason: '', source: 'manual', created_by: 'user_local', created_at: now(), updated_at: now(), opted_out: [], block_reason: null, contactable: true },
    { id: 'bcon_2', ...scope, account_id: 'bacc_1', name: 'Omar Ali', role_title: '', email: null, phone: null, profile_url: null, consent_basis: 'unknown', consent_note: '', consent_recorded_at: null, do_not_contact: true, dnc_reason: 'asked', source: 'manual', created_by: 'user_local', created_at: now(), updated_at: now(), opted_out: [], block_reason: 'Do not contact', contactable: false },
  ];

  const compute = (invoice: Json) => {
    const lines = invoice.lines as Json[];
    for (const line of lines) {
      const net = Math.round(Number(line.quantity) * Number(line.unit_price) * 100) / 100;
      const tax = Math.round(net * Number(line.tax_rate_pct)) / 100;
      Object.assign(line, { net, tax, total: Math.round((net + tax) * 100) / 100 });
    }
    const subtotal = lines.reduce((sum, line) => sum + Number(line.net), 0);
    const tax = lines.reduce((sum, line) => sum + Number(line.tax), 0);
    Object.assign(invoice, { subtotal: Math.round(subtotal * 100) / 100, tax: Math.round(tax * 100) / 100, total: Math.round((subtotal + tax) * 100) / 100 });
  };
  const issues = (invoice: Json) => {
    const found: string[] = [];
    if (!(invoice.lines as Json[]).length) found.push('Add at least one line.');
    if (!String(invoice.bill_to_name)) found.push('Name the customer the invoice is made out to.');
    if (!String(profile.legal_name)) found.push('Add your legal name to the billing profile.');
    return found;
  };
  const canOf = (invoice: Json) => {
    const status = String(invoice.status);
    const open = status === 'draft' || status === 'pending_approval';
    return {
      edit: open, request_send: status === 'draft', cancel_request: status === 'pending_approval', delete: status === 'draft',
      void: role === 'owner' && status === 'sent' && Number(invoice.amount_paid) === 0 && Number(invoice.amount_credited) === 0, record_payment: status === 'sent',
      remove_payment: role === 'owner' && (status === 'sent' || status === 'paid'),
      credit: role === 'owner' && status === 'sent' && dueState(invoice).balance > 0, void_credit_note: role === 'owner' && (status === 'sent' || status === 'paid'),
      decide_notice: status === 'sent' || status === 'paid',
    };
  };
  const dueState = (invoice: Json) => {
    const due = invoice.due_date ? String(invoice.due_date) : null;
    const late = invoice.status === 'sent' && due !== null && due < day(0);
    const days = late ? Math.round((Date.parse(day(0)) - Date.parse(due!)) / 86_400_000) : 0;
    const paid = Number(invoice.amount_paid);
    const credited = Number(invoice.amount_credited ?? 0);
    const balance = Math.round((Number(invoice.total) - paid - credited) * 100) / 100;
    return { overdue: late && balance > 0, days_overdue: days, balance, payment_state: balance <= 0 && Number(invoice.total) > 0 ? (paid <= 0 && credited > 0 ? 'credited' : 'paid') : paid <= 0 ? 'unpaid' : 'partially_paid' };
  };
  const view = (invoice: Json) => ({
    ...invoice, account_name: 'Acme Trading', project_name: invoice.project_id ? 'Website rebuild' : null, ...dueState(invoice),
    payments: invoice.payments, approval_state: invoice.status === 'pending_approval' ? 'pending' : 'none',
    approval: invoice.status === 'pending_approval' ? { id: 'approval_inv', task_id: 'task_inv', action: 'business.send_invoice', risk_level: 'high', status: 'pending', reason: '', created_at: now(), decided_at: null, arguments: {}, payload_hash: invoice.requested_hash, expires_at: null, decided_by: null, decision_reason: null } : null,
    issues: invoice.status === 'draft' || invoice.status === 'pending_approval' ? issues(invoice) : [], can: canOf(invoice),
  });
  const item = (invoice: Json) => ({
    id: invoice.id, number: invoice.number, status: invoice.status, account_id: invoice.account_id, account_name: 'Acme Trading', project_id: invoice.project_id, project_name: invoice.project_id ? 'Website rebuild' : null,
    currency: invoice.currency, total: invoice.total, amount_paid: invoice.amount_paid, amount_credited: invoice.amount_credited ?? 0, ...dueState(invoice), issue_date: invoice.issue_date, due_date: invoice.due_date,
    customer_reference: invoice.customer_reference, notices_pending: (invoice.payment_notices as Json[]).filter((n) => n.status === 'pending').length, lines: (invoice.lines as Json[]).length, updated_at: invoice.updated_at, created_at: invoice.created_at,
  });
  const send = (invoice: Json) => {
    Object.assign(invoice, { status: 'sent', number: `INV-${String(++sequence).padStart(4, '0')}`, seq: sequence, issue_date: day(0), due_date: day(Number(invoice.due_in_days)), seller: { ...profile }, content_hash: invoice.requested_hash, sent_by: 'user_owner', sent_at: now(), updated_at: now() });
  };
  const blank = (fields: Json): Json => ({
    id: id('binv'), ...scope, account_id: 'bacc_1', project_id: null, status: 'draft', currency: 'SAR', lines: [], subtotal: 0, tax: 0, total: 0, amount_paid: 0, amount_credited: 0, due_in_days: 14, seq: null, number: null,
    issue_date: null, due_date: null, customer_reference: '', notes: '', payment_terms: '', bill_to_name: 'Acme Trading', bill_to_address: 'Riyadh', bill_to_email: 'sara@acme.example', bill_to_tax_number: '',
    seller: null, content_hash: null, requested_hash: null, approval_id: null, requested_by: null, requested_at: null, sent_by: null, sent_at: null, paid_at: null, voided_by: null, voided_at: null,
    void_reason: '', decision_note: '', created_by: 'user_local', created_at: now(), updated_at: now(), payments: [], credit_notes: [], payment_notices: [], ...fields,
  });
  const line = (description: string, unitPrice: number, quantity: number, tax: number, source: string, sourceId: string | null) => ({ description, kind: 'service', quantity, unit_price: unitPrice, tax_rate_pct: tax, source, source_id: sourceId, gross_total: null, net: 0, tax: 0, total: 0 });
  mock.seedSent = (fields: Json = {}) => {
    const invoice = blank({ lines: [line('Late project fee', 1000, 1, 15, 'manual', null)], due_in_days: 14, ...fields });
    compute(invoice); invoice.requested_hash = INVOICE_HASH; send(invoice);
    Object.assign(invoice, fields);
    invoices.unshift(invoice);
    return String(invoice.id);
  };
  mock.seedNotice = (invoiceId, fields = {}) => {
    const invoice = invoices.find((entry) => entry.id === invoiceId)!;
    const notice = { id: id('bpnote'), ...scope, invoice_id: invoiceId, account_id: 'bacc_1', access_id: 'bpacc_1', contact_id: 'bcon_1', payer_name: 'Sara Khan', amount: 400, paid_on: day(-1), method: 'bank_transfer', reference: 'TRX-31', note: '', status: 'pending', decided_by: null, decided_at: null, decision_note: '', payment_id: null, created_at: now(), updated_at: now(), ...fields };
    (invoice.payment_notices as Json[]).unshift(notice);
    return String(notice.id);
  };
  mock.approveSend = (invoiceId) => { const invoice = invoices.find((entry) => entry.id === invoiceId)!; send(invoice); };
  mock.rejectSend = (invoiceId, reason) => { const invoice = invoices.find((entry) => entry.id === invoiceId)!; Object.assign(invoice, { status: 'draft', decision_note: `Rejected by user_owner: ${reason}`, approval_id: null, requested_hash: null }); };

  const billable = (projectId: string | null) => ({
    account_id: 'bacc_1', project_id: projectId, currency: 'SAR', tax_rate_pct: 15,
    milestones: projectId ? (options.withContractShare ? [] : [
      { id: 'bmil_1', position: 1, title: 'Design approved', amount: 1000, due_on: null, status: 'accepted', change_order_number: null, billable: !invoices.some((entry) => (entry.lines as Json[]).some((l) => l.source_id === 'bmil_1' && entry.status !== 'void')), reason: invoices.some((entry) => (entry.lines as Json[]).some((l) => l.source_id === 'bmil_1' && entry.status !== 'void')) ? 'Already on a draft invoice.' : '' },
      { id: 'bmil_2', position: 2, title: 'Extra pages', amount: 500, due_on: null, status: 'accepted', change_order_number: 1, billable: true, reason: '' },
      { id: 'bmil_3', position: 3, title: 'Launch', amount: 2000, due_on: null, status: 'planned', change_order_number: null, billable: false, reason: 'Already marked paid.' },
    ]) : [],
    contract: projectId && options.withContractShare ? { contract_total: 5750, invoiced: 0, remaining: 5750 } : null,
    usage: [{ id: 'busage_1', description: 'Domain renewal', kind: 'external_cost', occurred_on: day(-3), quantity: 1, unit_cost: 80, amount: 80, currency: 'SAR', project_id: projectId, billable: true, reason: '' }],
    offers: projectId ? [] : [{ id: 'boffer_1', label: 'Monthly support v1', currency: 'SAR', price_type: 'fixed', price: 400 }],
  });

  await page.route('http://localhost:8000/api/v1/business/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const parts = path.split('/').filter(Boolean);
    const mine = parts[0] === 'invoices' || parts[0] === 'billing-profile' || (parts[0] === 'portal') || path === '/accounts' || parts[0] === 'accounts' || (path === '/projects' && method === 'GET');
    if (!mine || (parts[0] === 'accounts' && parts.length > 2)) return route.fallback();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const denied = (message = MANAGE_DENIED) => json(envelope('forbidden', message), 403);
    const page_ = (items: Json[]) => ({ items, total: items.length, limit: 25, offset: 0 });
    const find = (invoiceId: string) => invoices.find((entry) => entry.id === invoiceId);

    if (path === '/accounts' && method === 'GET') return json(page_([{ ...account, contacts: contacts.length, open_leads: 0 }]));
    if (parts[0] === 'accounts') return json({ ...account, id: parts[1], contacts: contacts.map((entry) => ({ ...entry, account_id: parts[1] })), leads: [] });
    if (path === '/projects') return json(page_(options.noProject ? [] : [project]));

    if (path === '/billing-profile') {
      if (method === 'PUT') { if (role !== 'owner') return denied(); Object.assign(profile, body); return json(profile); }
      return json(profile);
    }

    if (parts[0] === 'portal') {
      if (parts[1] === 'access' && parts.length === 2 && method === 'GET') return json(access);
      if (parts[1] === 'access' && parts.length === 2 && method === 'POST') {
        if (role !== 'owner') return denied();
        const created = { id: id('bpacc'), account_id: body.account_id, contact_id: body.contact_id, contact_name: contacts.find((c) => c.id === body.contact_id)?.name ?? '', contact_email: 'sara@acme.example', status: 'active', expired: false,
          can_decide: body.can_decide !== false, expires_at: new Date(Date.now() + Number(body.expires_in_days ?? 90) * 86_400_000).toISOString(), last_used_at: null, created_at: now(), created_by: 'user_owner', revoked_at: null };
        access.push(created);
        return json({ ...created, token: PORTAL_TOKEN, portal_path: `/portal#access=${PORTAL_TOKEN}` }, 201);
      }
      if (parts[1] === 'access' && parts[3] === 'revoke') {
        if (role !== 'owner') return denied();
        const found = access.find((entry) => entry.id === parts[2])!;
        Object.assign(found, { status: 'revoked', revoked_at: now() });
        return json(found);
      }
      if (parts[1] === 'decisions') return json(decisions);
    }

    if (options.listStatus === 403 && parts[0] === 'invoices') return denied("Business is for workspace owners and members: it holds contacts' personal data. Ask an owner to make you a member.");
    if (options.listStatus === 500 && parts[0] === 'invoices') return json(envelope('internal_error', 'Database unavailable'), 500);

    if (path === '/invoices/summary') {
      const sum = (rows: Json[], key: string) => Math.round(rows.reduce((total, entry) => total + Number(entry[key]), 0) * 100) / 100;
      const sent = invoices.filter((entry) => entry.status === 'sent'), late = sent.filter((entry) => dueState(entry).overdue);
      const counts = Object.fromEntries(['draft', 'pending_approval', 'sent', 'paid', 'void'].map((status) => [status, invoices.filter((entry) => entry.status === status).length]));
      return json({
        counts,
        currencies: invoices.length ? [{ currency: 'SAR', outstanding: sum(sent.map((e) => ({ v: dueState(e).balance })), 'v'), overdue: sum(late.map((e) => ({ v: dueState(e).balance })), 'v'), overdue_count: late.length,
          invoiced_this_month: sum(invoices.filter((e) => e.number), 'total'), collected_this_month: sum(invoices, 'amount_paid'), drafts: sum(invoices.filter((e) => e.status === 'draft' || e.status === 'pending_approval'), 'total') }] : [],
        truncated: false, notices_pending: invoices.reduce((total, entry) => total + (entry.payment_notices as Json[]).filter((n) => n.status === 'pending').length, 0), notes: [],
      });
    }
    if (path === '/invoices/billable') return json(billable(url.searchParams.get('project_id')));
    if (path === '/invoices' && method === 'GET') {
      const statuses = url.searchParams.getAll('status'), q = (url.searchParams.get('q') ?? '').toLowerCase(), overdue = url.searchParams.get('overdue') === 'true';
      const shown = invoices.filter((entry) => (!statuses.length || statuses.includes(String(entry.status))) && (!overdue || dueState(entry).overdue) && (!q || `${entry.number} ${entry.bill_to_name} ${entry.customer_reference}`.toLowerCase().includes(q)));
      return json(page_(shown.map(item)));
    }
    if (path === '/invoices' && method === 'POST') {
      if (!body.project_id && !body.account_id) return json(envelope('validation_error', 'Choose the client account or the project to invoice.'), 422);
      const lines: Json[] = [];
      for (const milestoneId of (body.milestone_ids as string[] | undefined) ?? []) {
        const found = billable('bprj_1').milestones.find((m) => m.id === milestoneId);
        if (!found || !found.billable) return json(envelope('validation_error', `The milestone cannot be invoiced: ${found?.reason ?? 'not found'}`), 422);
        lines.push(line(found.title, found.amount, 1, Number(body.tax_rate_pct ?? 15), 'milestone', found.id));
      }
      if (body.contract_amount !== undefined) lines.push(line('Share of the contract', Number(body.contract_amount) / 1.15, 1, 15, 'contract', null));
      if (body.offer_id) lines.push(line('Monthly support v1', 400, Number(body.offer_quantity ?? 1), Number(body.tax_rate_pct ?? 15), 'offer', 'boffer_1'));
      for (const entry of ((body.usage_ids as string[] | undefined) ?? [])) lines.push(line('Domain renewal', 80 * (1 + Number(body.usage_markup_pct ?? 0) / 100), 1, Number(body.tax_rate_pct ?? 15), 'usage', entry));
      for (const entry of (body.lines as Json[] | undefined) ?? []) lines.push(line(String(entry.description), Number(entry.unit_price), Number(entry.quantity ?? 1), Number(entry.tax_rate_pct ?? body.tax_rate_pct ?? 15), 'manual', null));
      if (!lines.length) return json(envelope('validation_error', 'Choose what to invoice: milestones, billable costs, an offer or a line you write.'), 422);
      const invoice = blank({ project_id: body.project_id ?? null, currency: body.currency ?? 'SAR', lines, due_in_days: body.due_in_days ?? Number(profile.default_due_days), customer_reference: body.customer_reference ?? '',
        notes: body.notes ?? '', payment_terms: body.payment_terms ?? '', ...(body.bill_to_name ? { bill_to_name: body.bill_to_name } : {}) });
      compute(invoice); invoices.unshift(invoice);
      return json(view(invoice), 201);
    }

    const invoice = find(parts[1] ?? '');
    if (parts[0] === 'invoices' && !invoice) return json(envelope('not_found', 'Invoice not found'), 404);
    if (parts[0] === 'invoices' && invoice) {
      const status = String(invoice.status);
      if (parts.length === 2 && method === 'GET') return json(view(invoice));
      if (parts.length === 2 && method === 'PATCH') {
        if (status !== 'draft' && status !== 'pending_approval') return json(envelope('conflict', `The invoice is ${status}; a sent invoice never changes. Void it and issue a new one.`), 409);
        if (status === 'pending_approval') Object.assign(invoice, { status: 'draft', approval_id: null, requested_hash: null });
        if ('customer_reference' in body) invoice.customer_reference = body.customer_reference;
        if ('notes' in body) invoice.notes = body.notes;
        if ('bill_to_name' in body) {
          if (!String(body.bill_to_name)) return json(envelope('validation_error', 'Name the customer the invoice is made out to.'), 422);
          invoice.bill_to_name = body.bill_to_name;
        }
        if (body.due_in_days !== undefined) invoice.due_in_days = body.due_in_days;
        let lines = invoice.lines as Json[];
        const drop = new Set((body.remove_lines as number[] | undefined) ?? []);
        lines = lines.filter((_, index) => !drop.has(index));
        if (body.lines) lines = [...lines.filter((entry) => entry.source !== 'manual'), ...(body.lines as Json[]).map((entry) => line(String(entry.description), Number(entry.unit_price), Number(entry.quantity ?? 1), Number(entry.tax_rate_pct ?? 15), 'manual', null))];
        invoice.lines = lines; compute(invoice); invoice.updated_at = now();
        return json(view(invoice));
      }
      if (parts.length === 2 && method === 'DELETE') {
        if (status !== 'draft') return json(envelope('conflict', `The invoice is ${status}; a numbered invoice is voided, not deleted.`), 409);
        invoices.splice(invoices.indexOf(invoice), 1);
        return route.fulfill({ status: 204 });
      }
      if (parts[2] === 'request-approval') {
        if (status !== 'draft') return json(envelope('conflict', `The invoice is ${status.replace('_', ' ')}; only a draft is sent for approval.`), 409);
        const found = issues(invoice);
        if (found.length) return json(envelope('conflict', `Fix the invoice first: ${found.join(' ')}`), 409);
        Object.assign(invoice, { status: 'pending_approval', requested_hash: INVOICE_HASH, content_hash: INVOICE_HASH, approval_id: 'approval_inv', requested_by: 'user_local', requested_at: now(), decision_note: '' });
        return json(view(invoice));
      }
      if (parts[2] === 'cancel-request') {
        Object.assign(invoice, { status: 'draft', approval_id: null, requested_hash: null, decision_note: 'The request was cancelled.' });
        return json(view(invoice));
      }
      if (parts[2] === 'void') {
        if (role !== 'owner') return denied('Only workspace owners can void an invoice.');
        if (status !== 'sent') return json(envelope('conflict', `The invoice is ${status}; only a sent invoice is voided.`), 409);
        if (Number(invoice.amount_credited) > 0) return json(envelope('conflict', 'The invoice has credit notes. Void them first, then void the invoice.'), 409);
        Object.assign(invoice, { status: 'void', voided_by: 'user_owner', voided_at: now(), void_reason: body.reason });
        return json(view(invoice));
      }
      if (parts[2] === 'payments' && method === 'POST') {
        if (status !== 'sent') return json(envelope('conflict', `The invoice is ${status}; payments are recorded on a sent invoice.`), 409);
        const balance = dueState(invoice).balance;
        if (Number(body.amount) > balance) return json(envelope('validation_error', `The payment is more than the balance of ${balance.toFixed(2)}.`), 422);
        (invoice.payments as Json[]).push({ id: id('bpay'), ...scope, invoice_id: invoice.id, amount: body.amount, paid_on: body.paid_on, method: body.method ?? 'bank_transfer', reference: body.reference ?? '', note: body.note ?? '', recorded_by: 'user_local', created_at: now(), updated_at: now() });
        invoice.amount_paid = Math.round(((invoice.payments as Json[]).reduce((sum, p) => sum + Number(p.amount), 0)) * 100) / 100;
        if (dueState(invoice).balance <= 0) Object.assign(invoice, { status: 'paid', paid_at: now() });
        return json(view(invoice), 201);
      }
      if (parts[2] === 'credit-notes' && parts.length === 3 && method === 'GET') return json(invoice.credit_notes);
      if (parts[2] === 'credit-notes' && parts.length === 3 && method === 'POST') {
        if (role !== 'owner') return denied('Only workspace owners can do this (the catalog, research caps, deletions and removing a do-not-contact mark).');
        if (status !== 'sent') return json(envelope('conflict', `The invoice is ${status}; a credit note is issued against a sent invoice.`), 409);
        const balance = dueState(invoice).balance;
        if (Number(body.amount) > balance) return json(envelope('validation_error', `A credit note is at most the balance of SAR ${balance.toFixed(2)}.`), 422);
        const notes = invoice.credit_notes as Json[];
        const share = Number(invoice.total) > 0 ? Math.round(Number(body.amount) * Number(invoice.tax) / Number(invoice.total) * 100) / 100 : 0;
        notes.push({ id: id('bcrn'), ...scope, invoice_id: invoice.id, account_id: 'bacc_1', seq: notes.length + 1, number: `${invoice.number}-CN${notes.length + 1}`, status: 'issued', currency: invoice.currency, amount: body.amount, subtotal: Math.round((Number(body.amount) - share) * 100) / 100, tax: share, reason: body.reason, issue_date: day(0), issued_by: 'user_owner', voided_by: null, voided_at: null, void_reason: '', created_at: now(), updated_at: now() });
        invoice.amount_credited = Math.round((Number(invoice.amount_credited) + Number(body.amount)) * 100) / 100;
        if (dueState(invoice).balance <= 0) Object.assign(invoice, { status: 'paid', paid_at: now() });
        return json(view(invoice), 201);
      }
      if (parts[2] === 'credit-notes' && parts[4] === 'void') {
        if (role !== 'owner') return denied();
        const found = (invoice.credit_notes as Json[]).find((entry) => entry.id === parts[3]);
        if (!found) return json(envelope('not_found', 'Credit note not found'), 404);
        Object.assign(found, { status: 'void', voided_by: 'user_owner', voided_at: now(), void_reason: body.reason });
        invoice.amount_credited = Math.round((Number(invoice.amount_credited) - Number(found.amount)) * 100) / 100;
        if (invoice.status === 'paid') Object.assign(invoice, { status: 'sent', paid_at: null });
        return json(view(invoice));
      }
      if (parts[2] === 'payment-notices' && parts.length === 3 && method === 'GET') return json(invoice.payment_notices);
      if (parts[2] === 'payment-notices' && parts[4] === 'confirm') {
        const found = (invoice.payment_notices as Json[]).find((entry) => entry.id === parts[3]);
        if (!found) return json(envelope('not_found', 'Payment notice not found'), 404);
        if (found.status !== 'pending') return json(envelope('conflict', `This notice was already ${found.status}.`), 409);
        const balance = dueState(invoice).balance;
        if (Number(found.amount) > balance) return json(envelope('validation_error', `The payment is more than the balance of SAR ${balance.toFixed(2)}. Reject this notice and record the payment by hand if the figures differ.`), 422);
        const payment = { id: id('bpay'), ...scope, invoice_id: invoice.id, amount: found.amount, paid_on: found.paid_on, method: found.method, reference: found.reference, note: `Reported in the portal by ${found.payer_name}.`, recorded_by: 'user_local', created_at: now(), updated_at: now() };
        (invoice.payments as Json[]).push(payment);
        invoice.amount_paid = Math.round((Number(invoice.amount_paid) + Number(found.amount)) * 100) / 100;
        if (dueState(invoice).balance <= 0) Object.assign(invoice, { status: 'paid', paid_at: now() });
        Object.assign(found, { status: 'confirmed', decided_by: 'user_local', decided_at: now(), decision_note: body.note ?? '', payment_id: payment.id });
        return json(view(invoice));
      }
      if (parts[2] === 'payment-notices' && parts[4] === 'reject') {
        const found = (invoice.payment_notices as Json[]).find((entry) => entry.id === parts[3]);
        if (!found) return json(envelope('not_found', 'Payment notice not found'), 404);
        if (found.status !== 'pending') return json(envelope('conflict', `This notice was already ${found.status}.`), 409);
        Object.assign(found, { status: 'rejected', decided_by: 'user_local', decided_at: now(), decision_note: body.reason });
        return json(view(invoice));
      }
      if (parts[2] === 'pdf') {
        return route.fulfill({ status: 200, contentType: 'application/pdf', headers: { 'content-disposition': `attachment; filename="${invoice.number ?? `draft-${invoice.id}`}.pdf"`, 'access-control-expose-headers': 'Content-Disposition' }, body: '%PDF-1.4 mock' });
      }
      if (parts[2] === 'payments' && method === 'DELETE') {
        if (role !== 'owner') return denied('Only workspace owners can remove a payment.');
        invoice.payments = (invoice.payments as Json[]).filter((p) => p.id !== parts[3]);
        invoice.amount_paid = (invoice.payments as Json[]).reduce((sum, p) => sum + Number(p.amount), 0);
        if (invoice.status === 'paid' && Number(invoice.amount_paid) < Number(invoice.total)) invoice.status = 'sent';
        return json(view(invoice));
      }
      if (parts[2] === 'print') {
        const language = url.searchParams.get('lang') ?? 'en';
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="${language}"><head><meta charset="utf-8"><title>${invoice.number ?? 'Draft'}</title></head><body><h1>Invoice ${invoice.number ?? 'DRAFT'}</h1><p>Total ${invoice.total}</p></body></html>` });
      }
    }
    return json(envelope('not_found', 'Not found'), 404);
  });
  return mock;
}
