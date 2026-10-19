import type { Page } from '@playwright/test';

// WhatsApp inbox (docs/whatsapp.md): conversations, the 24-hour window, drafts with provenance,
// approve-to-send with the payload hash, templates outside the window, setup, and every state.
const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const inHours = (hours: number) => new Date(Date.now() + hours * 3600_000).toISOString();
export const hash = '5d'.repeat(32);

export const connected = {
  configured: true, can_manage: true, webhook_path: '/api/v1/whatsapp/webhook', deployment_problems: [], phone_number_id: '106540352242922',
  business_account_id: '102290129340398', display_phone_number: '+966 11 000 0000', verified_name: 'Tyre Shop', quality_rating: 'GREEN',
  access_token_hint: '...oken', app_secret_set: true, verify_token_set: true, owner_test_number: '966500000999', auto_send_templates: [],
  triage_enabled: true, message_retention_days: null, effective_message_retention_days: 365, default_message_retention_days: 365, status: 'connected', last_error: null, last_checked_at: minutesAgo(30), last_webhook_at: minutesAgo(2), updated_at: minutesAgo(60), updated_by: 'user_local',
};
export const notConnected = { ...connected, configured: false, phone_number_id: null, business_account_id: null, display_phone_number: null, verified_name: null, status: null, access_token_hint: null, app_secret_set: false, verify_token_set: false, owner_test_number: null, last_webhook_at: null };

export function conversation(id: string, name: string, preview: string, extra: Record<string, unknown> = {}) {
  return {
    id, contact_id: `c_${id}`, wa_id: '966500000001', contact_name: name, last_message_at: minutesAgo(5), last_message_preview: preview,
    last_direction: 'inbound', last_inbound_at: minutesAgo(5), unread_count: 1, category: 'price_stock', category_source: 'rules', summary: null,
    suggestion: null, needs_triage: false, window_open: true, window_closes_at: inHours(23.9), awaiting_approval: 0, ...extra,
  };
}

export const ahmed = conversation('wacv_ahmed', 'أحمد', 'بكم كفر 205/55R16؟ متوفر؟');
export const sara = conversation('wacv_sara', 'Sara Trading', 'Where is my order A-1001?', { wa_id: '971500000002', category: 'order_status', unread_count: 0, last_inbound_at: minutesAgo(60 * 30), window_open: false, window_closes_at: minutesAgo(60 * 6), last_message_at: minutesAgo(60 * 30) });

export const inbound = (id: string, body: string, minutes: number) => ({ id, conversation_id: 'wacv_ahmed', wamid: `wamid.${id}`, direction: 'inbound', kind: 'text', body, media: null, template: null, status: 'received', error: null, sent_by: null, approval_id: null, timestamp: minutesAgo(minutes), approval: null });
const approval = {
  id: 'approval_wa', task_id: 'task_wa', action: 'send_whatsapp', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
  created_at: minutesAgo(0), run_id: 'run_wa', step_id: 'step_wa', payload_hash: hash, expires_at: inHours(24), target: 'graph.facebook.com',
  arguments: { conversation_id: 'wacv_ahmed', to: '966500000001', text: '', reply_to: null, message_id: 'wam_out' },
};
export const templates = [
  { name: 'order_update', language: 'en_US', status: 'APPROVED', category: 'UTILITY', body: 'Hi {{1}}, your order {{2}} is on its way.', parameter_count: 2, header: null, footer: null, buttons: [], auto_send: false },
  { name: 'summer_sale', language: 'ar', status: 'PENDING', category: 'MARKETING', body: 'عرض الصيف', parameter_count: 0, header: null, footer: null, buttons: [], auto_send: false },
];

export interface WaMockOptions { role?: string; account?: Record<string, unknown>; conversations?: Record<string, unknown>[]; failList?: boolean; ticketConflict?: boolean }

export async function mockWhatsApp(page: Page, { role = 'owner', account = connected, conversations = [ahmed, sara], failList = false, ticketConflict = false }: WaMockOptions = {}) {
  const sent: { sends: unknown[]; decisions: unknown[]; saves: Record<string, unknown>[]; drafts: number; tickets: unknown[] } = { sends: [], decisions: [], saves: [], drafts: 0, tickets: [] };
  let messages: Record<string, unknown>[] = [inbound('m1', 'السلام عليكم', 8), inbound('m2', 'بكم كفر 205/55R16؟ متوفر؟', 5)];
  let currentAccount = account;
  let listFails = failList;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path.startsWith('/api/v1/whatsapp/') && role === 'viewer') return json({ error: { code: 'forbidden', message: 'Workspace permission denied', correlation_id: 'c' } }, 403);
    if (path === '/api/v1/whatsapp/account' && method === 'GET') return json({ ...currentAccount, can_manage: role === 'owner' });
    if (path === '/api/v1/whatsapp/account' && method === 'PUT') {
      const body = request.postDataJSON() as Record<string, unknown>;
      sent.saves.push(body);
      currentAccount = { ...connected, auto_send_templates: (body.auto_send_templates as string[]) ?? [] };
      return json(currentAccount);
    }
    if (path === '/api/v1/whatsapp/conversations') {
      if (listFails) { listFails = false; return json({ error: { code: 'service_unavailable', message: 'The database is not reachable.', correlation_id: 'c' } }, 503); }
      const filter = url.searchParams.get('filter');
      const q = url.searchParams.get('q');
      let items = conversations;
      if (filter === 'unread') items = items.filter((item) => (item.unread_count as number) > 0);
      if (q) items = items.filter((item) => JSON.stringify(item).includes(q));
      return json(items);
    }
    if (path === '/api/v1/whatsapp/conversations/wacv_ahmed') return json({ conversation: { ...ahmed, unread_count: 0 }, messages, has_more: false });
    if (path === '/api/v1/whatsapp/conversations/wacv_sara') return json({ conversation: sara, messages: [{ ...inbound('s1', 'Where is my order A-1001?', 60 * 30), conversation_id: 'wacv_sara' }], has_more: false });
    if (/\/read$/.test(path)) return json({ ...ahmed, unread_count: 0 });
    if (path === '/api/v1/whatsapp/conversations/wacv_ahmed/draft') {
      sent.drafts += 1;
      return json({ text: 'شكراً لسؤالك. سعر [مقاس الإطار] هو [السعر] وهو [متوفر / يتوفر بتاريخ].', category: 'price_stock', summary: 'بكم كفر 205/55R16؟', language: 'ar', source: 'rules', provider: null, model: null, generated_at: minutesAgo(0), generated_by: 'user_local', untrusted_messages: 0 });
    }
    if (/\/conversations\/wacv_(ahmed|sara)\/send$/.test(path)) {
      const body = request.postDataJSON() as { text?: string; template?: { name: string; parameters: string[] } };
      sent.sends.push(body);
      const text = body.text ?? `Hi ${body.template?.parameters[0]}, your order ${body.template?.parameters[1]} is on its way.`;
      const pending = { ...approval, arguments: { ...approval.arguments, text }, action: body.template ? 'send_whatsapp_template' : 'send_whatsapp', risk_level: body.template ? 'medium' : 'high' };
      const out = { id: 'wam_out', conversation_id: 'wacv_ahmed', wamid: null, direction: 'outbound', kind: body.template ? 'template' : 'text', body: text, media: null, template: body.template ? { ...body.template, language: 'en_US' } : null, status: 'awaiting_approval', error: null, sent_by: 'user_local', approval_id: approval.id, timestamp: minutesAgo(0), approval: pending };
      messages = [...messages, out];
      return json({ outcome: 'awaiting_approval', message: out, approval: pending, detail: 'Waiting for approval.' });
    }
    if (path === '/api/v1/approvals/approval_wa/approve') {
      sent.decisions.push(request.postDataJSON());
      messages = messages.map((m) => m.id === 'wam_out' ? { ...m, status: 'sent', wamid: 'wamid.out1', approval: null } : m);
      return json({ approval: { ...approval, status: 'approved' }, task: { id: 'task_wa', title: 'Reply', prompt: 'Reply', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: minutesAgo(0), updated_at: minutesAgo(0) }, run: { id: 'run_wa', task_id: 'task_wa', status: 'completed', steps: [], result: 'Sent the WhatsApp reply.' } });
    }
    if (path === '/api/v1/whatsapp/templates') return json({ templates, fetched_at: minutesAgo(0) });
    // Business support: a ticket from this conversation (docs/business.md#tickets-from-an-inbox).
    if (path === '/api/v1/business/support/tickets/from-message' && method === 'POST') {
      sent.tickets.push(request.postDataJSON());
      if (ticketConflict) return json({ error: { code: 'conflict', message: 'This whatsapp is already linked to open ticket T-9Z8Y7X (bst_wa_open).', correlation_id: 'c' } }, 409);
      return json({ id: 'bst_wa_1', reference: 'T-4D5E6F', title: 'Support request from أحمد', source: 'whatsapp', source_ref: 'wacv_ahmed', severity: 'normal', status: 'new', replies: [], activities: [], can_reply: true, reply_channel: 'whatsapp' }, 201);
    }
    return route.abort();
  });
  return sent;
}
