import type { Page, Route } from '@playwright/test';
import { mockBusiness } from './businessApi';

// Business support tickets (docs/business.md#support-tickets): a small stateful stand-in for
// /api/v1/business/support that follows the API's rules for the main path (create, assign, status
// moves with TICKET_TRANSITIONS, resolve with a resolution, escalation, notes, reply drafts handed
// to the inbox's approval path with a payload hash). Errors use the API's error envelope.
type Json = Record<string, unknown>;
const now = () => new Date().toISOString();
const later = (hours: number) => new Date(Date.now() + hours * 3600_000).toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
export const REPLY_HASH = 'c4'.repeat(32);
const MANAGE_DENIED = 'Only workspace owners can do this (the catalog, research caps, deletions and removing a do-not-contact mark).';
const READ_DENIED = "Business is for workspace owners and members: it holds contacts' personal data. Ask an owner to make you a member.";

const TRANSITIONS: Record<string, string[]> = {
  new: ['triaged', 'in_progress', 'waiting_on_customer', 'resolved'],
  triaged: ['in_progress', 'waiting_on_customer', 'resolved'],
  in_progress: ['triaged', 'waiting_on_customer', 'resolved'],
  waiting_on_customer: ['in_progress', 'resolved'],
  resolved: ['closed', 'in_progress'],
  closed: ['in_progress'],
};
const OPEN = ['new', 'triaged', 'in_progress', 'waiting_on_customer'];
const POLICY = {
  timezone: 'Asia/Riyadh', work_days: [6, 0, 1, 2, 3], start_hour: 9, end_hour: 17, note: '',
  targets: { critical: { response_hours: 2, resolution_hours: 16 }, high: { response_hours: 4, resolution_hours: 40 }, normal: { response_hours: 8, resolution_hours: 80 }, low: { response_hours: 16, resolution_hours: 160 } },
};

export interface SupportMock {
  calls: { method: string; path: string; body: unknown }[];
  /** Adds a ticket as if opened from an inbound email or WhatsApp message (or closed, for reopen checks). */
  seed: (fields: Json) => string;
}

export async function mockSupport(page: Page, options: { role?: 'owner' | 'member' | 'viewer'; listStatus?: 500 } = {}): Promise<SupportMock> {
  const role = options.role ?? 'owner';
  await mockBusiness(page, { role: role === 'viewer' ? 'member' : role });
  const tickets: Json[] = [];
  const policy = structuredClone(POLICY);
  let counter = 0;
  const id = (prefix: string) => `${prefix}_${++counter}`;
  const mock: SupportMock = { calls: [], seed: () => '' };

  const newTicket = (fields: Json): Json => {
    const ticketId = id('btkt');
    return {
      id: ticketId, ...scope, created_at: now(), updated_at: now(), title: 'Ticket', description: '', account_id: null, contact_id: null, project_id: null,
      requester_name: '', requester_email: null, requester_phone: null, requester_verified: false, entitlement: 'none', support_offer_id: null, entitlement_note: '',
      severity: 'normal', status: 'new', assignee_id: null, source: 'manual', source_ref: null, source_account_id: null, reported_at: now(),
      response_due_at: later(8), resolution_due_at: later(80), first_responded_at: null, sla_paused_at: null, sla_paused_minutes: 0, escalated: false,
      escalation_reason: '', escalated_at: null, escalated_by: null, resolution_code: null, resolution_summary: '', resolved_at: null, resolved_by: null,
      closed_at: null, closed_by: null, reopened_count: 0, created_by: 'user_local', replies: [], activities: [], ...fields,
      reference: `T-${String(1000 + counter)}`,
    };
  };
  const activity = (ticket: Json, kind: string, summary: string) => {
    (ticket.activities as Json[]).unshift({ id: id('bact'), ticket_id: ticket.id, lead_id: null, account_id: null, opportunity_id: null, proposal_id: null, project_id: null,
      kind, summary, metadata: {}, actor: 'user_local', idempotency_key: null, created_at: now() });
  };
  const sla = (ticket: Json) => {
    const responded = ticket.first_responded_at || ticket.resolved_at;
    const response = responded ? 'met' : 'on_track';
    const resolution = ticket.resolved_at ? 'met' : ticket.sla_paused_at ? 'paused' : 'on_track';
    return { response, resolution, breached: false };
  };
  const view = (ticket: Json) => {
    const { replies, activities, ...rest } = ticket;
    void replies; void activities;
    return { ...rest, account_name: null, contact_name: null, project_name: null, support_plan_name: null, sla: sla(ticket), allowed: TRANSITIONS[String(ticket.status)] };
  };
  const channel = (ticket: Json) => (ticket.source === 'email' ? 'email' : ticket.source === 'whatsapp' ? 'whatsapp' : null);
  const detail = (ticket: Json) => ({ ...view(ticket), replies: ticket.replies, activities: ticket.activities, can_reply: channel(ticket) !== null && ticket.status !== 'closed', reply_channel: channel(ticket) });
  mock.seed = (fields: Json) => { const ticket = newTicket(fields); tickets.unshift(ticket); return String(ticket.id); };

  const handler = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/business/support', '');
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() as Json : {};
    mock.calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (role === 'viewer') return json(envelope('forbidden', READ_DENIED), 403);
    const parts = path.split('/').filter(Boolean);

    if (path === '/setup') {
      return json({
        policy, severities: ['critical', 'high', 'normal', 'low'], statuses: ['new', 'triaged', 'in_progress', 'waiting_on_customer', 'resolved', 'closed'],
        open_statuses: OPEN, transitions: TRANSITIONS, resolution_codes: ['fixed', 'workaround', 'answered', 'no_fault_found', 'duplicate', 'out_of_scope'],
        entitlements: ['project', 'support_plan', 'none'], can_write: true, can_manage: role === 'owner', can_decide: role === 'owner', can_link_email: true, can_link_whatsapp: true,
      });
    }
    if (path === '/policy' && method === 'PUT') {
      if (role !== 'owner') return json(envelope('forbidden', MANAGE_DENIED), 403);
      Object.assign(policy, body);
      return json(policy);
    }
    if (path === '/assignees') return json([{ user_id: 'user_local', role, you: true }, { user_id: 'user_teammate', role: 'member', you: false }]);
    if (path === '/summary') {
      const open = tickets.filter((item) => OPEN.includes(String(item.status)));
      return json({ open: open.length, new: open.filter((item) => item.status === 'new').length, unassigned: open.filter((item) => !item.assignee_id).length,
        escalated: open.filter((item) => item.escalated).length, breached: 0, waiting_on_customer: open.filter((item) => item.status === 'waiting_on_customer').length, truncated: false });
    }
    if (path === '/tickets' && method === 'GET') {
      if (options.listStatus === 500) return json(envelope('internal_error', 'Database unavailable'), 500);
      let items = tickets;
      const status = url.searchParams.getAll('status');
      if (status.length) items = items.filter((item) => status.includes(String(item.status)));
      else if (url.searchParams.get('open_only') === 'true') items = items.filter((item) => OPEN.includes(String(item.status)));
      if (url.searchParams.get('assignee') === 'me') items = items.filter((item) => item.assignee_id === 'user_local');
      if (url.searchParams.get('unassigned') === 'true') items = items.filter((item) => !item.assignee_id);
      const q = url.searchParams.get('q')?.toLowerCase();
      if (q) items = items.filter((item) => String(item.title).toLowerCase().includes(q));
      return json({ items: items.map(view), total: items.length, limit: 25, offset: 0 });
    }
    if (path === '/tickets' && method === 'POST') {
      if (String(body.title ?? '').length < 3) return json(envelope('validation_error', 'Give the ticket a title.'), 422);
      const ticket = newTicket({ ...body, support_offer_id: body.support_offer_id ?? null });
      tickets.unshift(ticket);
      activity(ticket, 'ticket_opened', `Ticket ${ticket.reference} opened (${ticket.severity}, from manual).`);
      return json(detail(ticket), 201);
    }
    const ticket = tickets.find((item) => item.id === parts[1]);
    if (parts[0] !== 'tickets' || !ticket) return json(envelope('not_found', 'Ticket not found'), 404);
    if (parts.length === 2 && method === 'GET') return json(detail(ticket));
    if (parts.length === 2 && method === 'PATCH') {
      if (ticket.status === 'closed') return json(envelope('conflict', 'A closed ticket keeps its record. Reopen it (owners) to change it.'), 409);
      if ('assignee_id' in body && body.assignee_id !== ticket.assignee_id) activity(ticket, 'ticket_assigned', `Assigned to ${body.assignee_id ?? 'nobody'}.`);
      Object.assign(ticket, body, { updated_at: now() });
      activity(ticket, 'ticket_updated', `Changed ${Object.keys(body).join(', ').replace(/_/g, ' ')}.`);
      return json(detail(ticket));
    }
    if (parts[2] === 'status') {
      const from = String(ticket.status), to = String(body.to);
      if (!TRANSITIONS[from].includes(to)) return json(envelope('conflict', `A ${from} ticket can move to: ${TRANSITIONS[from].join(', ')}.`), 409);
      if (from === 'closed' && role !== 'owner') return json(envelope('forbidden', MANAGE_DENIED), 403);
      if (to === 'resolved') {
        if (!body.resolution_code || String(body.resolution_summary ?? '').trim().length < 10) return json(envelope('validation_error', 'Say how it was resolved: choose a resolution and write at least a sentence (10 characters).'), 422);
        Object.assign(ticket, { resolution_code: body.resolution_code, resolution_summary: body.resolution_summary, resolved_at: now(), resolved_by: 'user_local' });
      }
      if (to === 'waiting_on_customer') Object.assign(ticket, { first_responded_at: ticket.first_responded_at ?? now(), sla_paused_at: now() });
      if (from === 'waiting_on_customer') ticket.sla_paused_at = null;
      if (to === 'in_progress' && (from === 'resolved' || from === 'closed')) Object.assign(ticket, { resolved_at: null, resolution_code: null, resolution_summary: '', closed_at: null, reopened_count: Number(ticket.reopened_count) + 1 });
      Object.assign(ticket, { status: to, updated_at: now() });
      activity(ticket, 'ticket_status', `Status ${from.replace(/_/g, ' ')} to ${to.replace(/_/g, ' ')}.`);
      return json(detail(ticket));
    }
    if (parts[2] === 'escalation') {
      Object.assign(ticket, { escalated: body.escalated, escalation_reason: body.escalated ? body.reason : '', escalated_at: body.escalated ? now() : null, escalated_by: body.escalated ? 'user_local' : null, updated_at: now() });
      activity(ticket, body.escalated ? 'ticket_escalated' : 'ticket_deescalated', body.escalated ? `Escalated: ${body.reason}` : `De-escalated. ${body.reason}`);
      return json(detail(ticket));
    }
    if (parts[2] === 'notes') {
      if (body.responded && !ticket.first_responded_at) ticket.first_responded_at = now();
      activity(ticket, body.responded ? 'response_recorded' : 'note', String(body.text));
      return json(detail(ticket));
    }
    if (parts[2] === 'replies' && parts.length === 3 && method === 'POST') {
      if (!channel(ticket)) return json(envelope('conflict', 'This ticket was opened by hand, so ANUM has no conversation to reply in.'), 409);
      (ticket.replies as Json[]).push({ id: id('brpl'), ...scope, created_at: now(), updated_at: now(), ticket_id: ticket.id, channel: channel(ticket), body: body.body, status: 'draft',
        generator: 'person', external_ref: null, approval_id: null, task_id: null, error: '', requested_by: null, requested_at: null, sent_at: null, created_by: 'user_local', approval: null });
      activity(ticket, 'reply_drafted', `Reply drafted by a person (${channel(ticket)}).`);
      return json(detail(ticket), 201);
    }
    const reply = (ticket.replies as Json[]).find((item) => item.id === parts[3]);
    if (parts[2] === 'replies' && reply) {
      if (parts[4] === 'request-approval') {
        Object.assign(reply, { status: 'pending_approval', approval_id: 'approval_reply', external_ref: 'email_draft_1', requested_by: 'user_local', requested_at: now(), updated_at: now(),
          approval: { id: 'approval_reply', task_id: 'task_reply', action: 'send_email', risk_level: 'high', status: 'pending', reason: 'Sending email always asks.', created_at: now(),
            decided_at: null, arguments: { body: reply.body }, payload_hash: REPLY_HASH, expires_at: later(24), decided_by: null, decision_reason: null } });
        activity(ticket, 'reply_requested', 'Reply handed to the email approval path: pending approval.');
        return json({ ticket: detail(ticket), reply, detail: 'The reply waits for an owner in Approvals; nothing is sent until it is approved.' });
      }
      if (method === 'PATCH') { Object.assign(reply, { body: body.body, status: 'draft', error: '', updated_at: now() }); return json(detail(ticket)); }
      if (method === 'DELETE') { Object.assign(reply, { status: 'discarded', updated_at: now() }); return json(detail(ticket)); }
    }
    return json(envelope('not_found', `Unmocked ${method} ${path}`), 404);
  };
  await page.route('http://localhost:8000/api/v1/business/support/**', handler);
  await page.route('http://localhost:8000/api/v1/business/accounts**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, limit: 100, offset: 0 }) }));
  return mock;
}
