import type { Page } from '@playwright/test';
import { later, mockMarketing, now } from './marketingApi';

// Email campaigns through Brevo (docs/brevo.md): a fake /api/v1/brevo for one workspace. Values are
// plainly fake (gitleaks): the key below only has the xkeysib- shape the page checks.
export const brevoKey = 'xkeysib-example-example-example';
export const brevoHash = 'b7'.repeat(32);

const connection = { status: 'connected', key_last4: 'mple', account_email: 'owner@shop.example', account_name: 'Sam Owner', company: 'Tyre Shop', plan: 'free', last_checked_at: now, last_error: null, connected_by: 'user_local', updated_at: now };
const stats = (delivered: number, opens: number, clicks: number) => ({ sent: delivered + 20, delivered, unique_views: opens, unique_clicks: clicks, unsubscriptions: 4, hard_bounces: 5, soft_bounces: 15, complaints: 0, open_rate: delivered ? Math.round(opens / delivered * 1000) / 10 : null, click_rate: delivered ? Math.round(clicks / delivered * 1000) / 10 : null });
const campaign = (id: number, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, name, subject: `${name} subject`, status, sender_name: 'Tyre Shop', sender_email: 'news@shop.example', list_ids: [7], scheduled_at: null, sent_at: status === 'sent' ? now : null,
  created_at: now, tag: '', stats: stats(status === 'sent' ? 980 : 0, status === 'sent' ? 490 : 0, status === 'sent' ? 98 : 0), list_stats: [{ list_id: 7, delivered: 980, open_rate: 50 }], ...extra,
});
export const approval = {
  id: 'approval_bv', task_id: 'task_bv', action: 'brevo.send_campaign', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
  created_at: now, run_id: 'run_bv', step_id: 'step_bv', payload_hash: brevoHash, expires_at: later, target: 'api.brevo.com',
  arguments: { draft_id: 'brevodraft_1', campaign: 'Winter tyres', subject: 'Fit winter tyres now', preview_text: '', sender: 'Tyre Shop <news@shop.example>', sender_id: 1, sender_email: 'news@shop.example',
    list_ids: [7], lists: ['Newsletter'], recipients: 1200, scheduled_at: 'as soon as approved', brevo_account: 'owner@shop.example', text: 'Winter tyres Book a fitting this week.', content_hash: 'c'.repeat(64), written_by: 'person' },
};
const draft = (extra: Record<string, unknown> = {}) => ({
  id: 'brevodraft_1', name: 'Winter tyres', subject: 'Fit winter tyres now', preview_text: '', sender_id: 1, sender_name: 'Tyre Shop', sender_email: 'news@shop.example', list_ids: [7], list_names: ['Newsletter'],
  status: 'draft', scheduled_at: null, content_hash: 'c'.repeat(64), approved_hash: null, brevo_campaign_id: null, last_error: null, test_sent_at: null, sent_at: null, source: 'person',
  created_by: 'user_local', created_at: now, updated_at: now, html_chars: 60, approval: null, approval_state: 'none',
  html: '<h1>Winter tyres</h1><p>Book a fitting this week.</p>', preview_html: '<h1>Winter tyres</h1><p>Book a fitting this week.</p>', remote_images: 0, ...extra,
});

export interface BrevoSent { keys: unknown[]; drafts: unknown[]; sends: unknown[]; tests: number; decisions: unknown[] }

export async function mockBrevo(page: Page, { role = 'owner', connected = true }: { role?: string; connected?: boolean } = {}): Promise<BrevoSent> {
  await mockMarketing(page, { role });
  const sent: BrevoSent = { keys: [], drafts: [], sends: [], tests: 0, decisions: [] };
  let linked = connected;
  let drafts: Record<string, unknown>[] = [];
  const status = () => ({ connected: linked, connection: linked ? connection : null, can_read: true, can_write: role !== 'viewer', can_manage: role === 'owner', host: 'api.brevo.com' });
  const campaigns = [campaign(101, 'October offers', 'sent'), campaign(102, 'Ramadan hours', 'scheduled', { scheduled_at: later })];
  await page.route('http://localhost:8000/api/v1/brevo/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/brevo', ''), method = request.method();
    const json = (body: unknown, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/status') return json(status());
    if (path === '/connection' && method === 'PUT') { sent.keys.push(request.postDataJSON()); linked = true; return json(status()); }
    if (path === '/connection/check') return json(status());
    if (!linked) return json({ error: { code: 'conflict', message: 'Brevo is not connected.', correlation_id: 'c' } }, 409);
    if (path === '/campaigns') return json({ items: url.searchParams.get('status') === 'suspended' ? [] : campaigns, total: 2, limit: 20, offset: 0 });
    if (path === '/campaigns/101') return json(campaigns[0]);
    if (path === '/senders') return json({ items: [{ id: 1, name: 'Tyre Shop', email: 'news@shop.example', active: true }, { id: 2, name: 'Old', email: 'old@shop.example', active: false }] });
    if (path === '/lists') return json({ items: [{ id: 7, name: 'Newsletter', subscribers: 1200, blacklisted: 3, folder_id: 1 }, { id: 9, name: 'VIP', subscribers: 40, blacklisted: 0, folder_id: 1 }], total: 2, limit: 50, offset: 0 });
    if (path === '/drafts' && method === 'GET') return json({ items: drafts, total: drafts.length, limit: 20, offset: 0 });
    if (path === '/drafts' && method === 'POST') {
      sent.drafts.push(request.postDataJSON());
      const body = request.postDataJSON() as { name: string; subject: string; html: string };
      drafts = [draft({ name: body.name, subject: body.subject, html: body.html, preview_html: body.html })];
      return json(drafts[0], 201);
    }
    if (path === '/drafts/brevodraft_1' && method === 'GET') return json(drafts[0]);
    if (path === '/drafts/brevodraft_1/test') { sent.tests += 1; drafts = [{ ...drafts[0], test_sent_at: now, brevo_campaign_id: 501 }]; return json(drafts[0]); }
    if (path === '/drafts/brevodraft_1/send') { sent.sends.push(request.postDataJSON()); drafts = [{ ...drafts[0], status: 'pending_approval', approval, approval_state: 'pending' }]; return json(drafts[0]); }
    return json({ error: { code: 'not_found', message: `unmocked ${method} ${path}`, correlation_id: 'c' } }, 404);
  });
  await page.route('http://localhost:8000/api/v1/approvals/approval_bv/approve', async (route) => {
    sent.decisions.push(route.request().postDataJSON());
    drafts = [{ ...drafts[0], status: 'sent', approval: null, approval_state: 'approved', approved_hash: 'c'.repeat(64), brevo_campaign_id: 501, sent_at: now }];
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ approval: { ...approval, status: 'approved' }, task: { id: 'task_bv', title: 't', prompt: 'p', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now }, run: { id: 'run_bv', task_id: 'task_bv', status: 'completed', steps: [], result: 'Approved. "Winter tyres" is being sent now through Brevo (campaign 501).' } }) });
  });
  return sent;
}
