import type { Page, Route } from '@playwright/test';
import { mockInvoices } from './businessInvoicesApi';

// Recurring invoices and the planned emails over the mocked API (docs/business.md#recurring-invoices), on top of the
// invoices mock. Every name is SYNTHETIC.

type Json = Record<string, unknown>;
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_local' };
const now = () => new Date().toISOString();
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

export interface SchedulesMock { calls: { method: string; path: string; body: Json }[]; schedules: Json[]; automation: Json }

export function schedule(fields: Json = {}): Json {
  return {
    id: 'binsch_1', ...scope, title: 'Monthly support retainer', account_id: 'bacc_1', project_id: null, contact_id: null, currency: 'SAR',
    lines: [{ description: 'Support retainer', kind: 'service', quantity: 1, unit_price: 1000, tax_rate_pct: 15, tax_code: null }],
    due_in_days: 14, customer_reference: 'PO 17', notes: '', payment_terms: '', frequency: 'monthly', interval: 1, start_date: day(-31), end_date: null,
    max_count: null, issue_mode: 'request_approval', status: 'active', anchor_date: day(-31), next_index: 2, next_date: day(1), created_count: 2,
    last_period: day(-1), last_invoice_id: 'binv_1', last_error: '', end_reason: '', ended_at: null, version: 3, created_by: 'user_local', updated_by: 'user_local',
    created_at: now(), updated_at: now(), account_name: 'Acme Trading', project_name: null, subtotal: 1000, tax: 150, total: 1150,
    upcoming: [day(1), day(32), day(62)],
    invoices: [
      { id: 'binv_1', number: 'INV-2026-0001', status: 'sent', period_date: day(-31), total: 1150, currency: 'SAR', payment_state: 'unpaid', created_at: now() },
      { id: 'binv_2', number: null, status: 'pending_approval', period_date: day(-1), total: 1150, currency: 'SAR', payment_state: 'unpaid', created_at: now() },
    ],
    invoices_total: 2,
    ...fields,
  };
}

export async function mockSchedules(page: Page, options: { role?: 'owner' | 'member'; empty?: boolean } = {}): Promise<SchedulesMock> {
  const role = options.role ?? 'owner';
  await mockInvoices(page, { role });
  const mock: SchedulesMock = {
    calls: [],
    schedules: options.empty ? [] : [schedule(), schedule({ id: 'binsch_2', title: 'Quarterly hosting, a long name to check how a long title wraps on a phone', frequency: 'quarterly', status: 'paused', next_date: null, upcoming: [], invoices: [], invoices_total: 0, created_count: 0, version: 1 })],
    automation: { auto_reminders: false, email_scheduled: false, language: 'en', last_run_on: null, last_run_at: null, last_run_summary: null, can_change: role === 'owner' },
  };
  let counter = 2;
  await page.route(/http:\/\/localhost:8000\/api\/v1\/business\/invoice-(schedules|automation).*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const parts = path.split('/').filter(Boolean);
    if (parts[0] === 'invoice-automation') {
      if (method === 'PUT' || (method === 'POST' && parts[1] === 'run')) {
        if (role !== 'owner') return json({ error: { code: 'forbidden', message: 'Only workspace owners can do this.', correlation_id: 'corr_1', details: [] } }, 403);
        if (method === 'PUT') Object.assign(mock.automation, body);
        else Object.assign(mock.automation, { last_run_on: day(0), last_run_at: now(), last_run_summary: { reminders_planned: 1, emails_planned: 0, skipped_do_not_contact: 0, failed: 0, note: '' } });
      }
      return json(mock.automation);
    }
    if (parts.length === 1 && method === 'GET') {
      const status = url.searchParams.get('status');
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const items = mock.schedules.filter((item) => (!status || item.status === status) && (!q || String(item.title).toLowerCase().includes(q)));
      return json({ items, total: items.length, limit: 25, offset: 0 });
    }
    if (parts.length === 1 && method === 'POST') {
      const made = schedule({ id: `binsch_${++counter}`, title: body.title ?? 'Acme Trading: recurring invoice', lines: body.lines, issue_mode: body.issue_mode ?? 'draft',
        frequency: body.frequency, start_date: body.start_date, next_date: body.start_date, created_count: 0, invoices: [], invoices_total: 0, version: 1, last_error: '' });
      mock.schedules.unshift(made);
      return json(made, 201);
    }
    const found = mock.schedules.find((item) => item.id === parts[1]);
    if (!found) return json({ error: { code: 'not_found', message: 'Recurring invoice not found', correlation_id: 'corr_1', details: [] } }, 404);
    if (parts.length === 2 && method === 'GET') return json(found);
    if (parts.length === 2 && method === 'DELETE') { mock.schedules.splice(mock.schedules.indexOf(found), 1); return route.fulfill({ status: 204 }); }
    if (parts.length === 2 && method === 'PATCH') { Object.assign(found, body, { version: Number(found.version) + 1 }); return json(found); }
    if (parts[2] === 'pause') { Object.assign(found, { status: 'paused', next_date: null, upcoming: [], version: Number(found.version) + 1 }); return json(found); }
    if (parts[2] === 'resume') { Object.assign(found, { status: 'active', next_date: day(1), upcoming: [day(1)], version: Number(found.version) + 1 }); return json(found); }
    if (parts[2] === 'end') { Object.assign(found, { status: 'ended', next_date: null, upcoming: [], end_reason: 'owner', version: Number(found.version) + 1 }); return json(found); }
    if (parts[2] === 'run') return json({ drafted: 0, sent_for_approval: 0, note: `Nothing is due yet. The next invoice is drafted on ${found.next_date ?? '-'}.`, schedule: found });
    return json({ error: { code: 'not_found', message: 'Not found', correlation_id: 'corr_1', details: [] } }, 404);
  });
  return mock;
}
