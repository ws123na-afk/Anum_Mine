import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// WhatsApp inbox (docs/whatsapp.md): conversations, the 24-hour window, drafts with provenance,
// approve-to-send with the payload hash, templates outside the window, setup, and every state.
const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const inHours = (hours: number) => new Date(Date.now() + hours * 3600_000).toISOString();
const hash = '5d'.repeat(32);

const connected = {
  configured: true, can_manage: true, webhook_path: '/api/v1/whatsapp/webhook', deployment_problems: [], phone_number_id: '106540352242922',
  business_account_id: '102290129340398', display_phone_number: '+966 11 000 0000', verified_name: 'Tyre Shop', quality_rating: 'GREEN',
  access_token_hint: '...oken', app_secret_set: true, verify_token_set: true, owner_test_number: '966500000999', auto_send_templates: [],
  triage_enabled: true, message_retention_days: null, effective_message_retention_days: 365, default_message_retention_days: 365, status: 'connected', last_error: null, last_checked_at: minutesAgo(30), last_webhook_at: minutesAgo(2), updated_at: minutesAgo(60), updated_by: 'user_local',
};
const notConnected = { ...connected, configured: false, phone_number_id: null, business_account_id: null, display_phone_number: null, verified_name: null, status: null, access_token_hint: null, app_secret_set: false, verify_token_set: false, owner_test_number: null, last_webhook_at: null };

function conversation(id: string, name: string, preview: string, extra: Record<string, unknown> = {}) {
  return {
    id, contact_id: `c_${id}`, wa_id: '966500000001', contact_name: name, last_message_at: minutesAgo(5), last_message_preview: preview,
    last_direction: 'inbound', last_inbound_at: minutesAgo(5), unread_count: 1, category: 'price_stock', category_source: 'rules', summary: null,
    suggestion: null, needs_triage: false, window_open: true, window_closes_at: inHours(23.9), awaiting_approval: 0, ...extra,
  };
}

const ahmed = conversation('wacv_ahmed', 'أحمد', 'بكم كفر 205/55R16؟ متوفر؟');
const sara = conversation('wacv_sara', 'Sara Trading', 'Where is my order A-1001?', { wa_id: '971500000002', category: 'order_status', unread_count: 0, last_inbound_at: minutesAgo(60 * 30), window_open: false, window_closes_at: minutesAgo(60 * 6), last_message_at: minutesAgo(60 * 30) });

const inbound = (id: string, body: string, minutes: number) => ({ id, conversation_id: 'wacv_ahmed', wamid: `wamid.${id}`, direction: 'inbound', kind: 'text', body, media: null, template: null, status: 'received', error: null, sent_by: null, approval_id: null, timestamp: minutesAgo(minutes), approval: null });
const approval = {
  id: 'approval_wa', task_id: 'task_wa', action: 'send_whatsapp', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
  created_at: minutesAgo(0), run_id: 'run_wa', step_id: 'step_wa', payload_hash: hash, expires_at: inHours(24), target: 'graph.facebook.com',
  arguments: { conversation_id: 'wacv_ahmed', to: '966500000001', text: '', reply_to: null, message_id: 'wam_out' },
};
const templates = [
  { name: 'order_update', language: 'en_US', status: 'APPROVED', category: 'UTILITY', body: 'Hi {{1}}, your order {{2}} is on its way.', parameter_count: 2, header: null, footer: null, buttons: [], auto_send: false },
  { name: 'summer_sale', language: 'ar', status: 'PENDING', category: 'MARKETING', body: 'عرض الصيف', parameter_count: 0, header: null, footer: null, buttons: [], auto_send: false },
];

interface Options { role?: string; account?: Record<string, unknown>; conversations?: Record<string, unknown>[]; failList?: boolean }

async function mockApi(page: Page, { role = 'owner', account = connected, conversations = [ahmed, sara], failList = false }: Options = {}) {
  const sent: { sends: unknown[]; decisions: unknown[]; saves: Record<string, unknown>[]; drafts: number } = { sends: [], decisions: [], saves: [], drafts: 0 };
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
    return route.abort();
  });
  return sent;
}

async function openInbox(page: Page) {
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: 'WhatsApp' })).toBeVisible();
}

test('an owner drafts a reply, sends it for approval and approves it with the payload hash', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await expect(page.locator('.waConversation')).toHaveCount(2);
  await page.locator('.waConversation').filter({ hasText: 'أحمد' }).click();
  const thread = page.locator('.waThread');
  await expect(thread.getByRole('heading', { name: 'أحمد' })).toBeVisible();
  await expect(thread.locator('.waWindow')).toContainText('Free-form replies for 23 more hours');
  await expect(thread.locator('.waText').first()).toHaveAttribute('dir', 'auto');

  await thread.getByRole('button', { name: 'Suggest a reply' }).click();
  await expect(thread.locator('.waDraftText')).toContainText('شكراً لسؤالك');
  await expect(thread.locator('.waProvenance')).toContainText('Rule-based draft');
  await thread.getByRole('button', { name: 'Use this draft' }).click();
  await expect(thread.getByLabel('Your reply')).toHaveValue(/شكراً لسؤالك/);
  await thread.getByLabel('Your reply').fill('نعم متوفر، السعر 350 ريال.');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect.poll(() => sent.sends).toEqual([{ text: 'نعم متوفر، السعر 350 ريال.' }]);

  const card = thread.locator('.waApproval');
  await expect(card).toContainText('graph.facebook.com');
  await expect(card).toContainText('5d5d5d5d5d5d');
  await card.getByRole('button', { name: 'Approve & send' }).click();
  await expect.poll(() => sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(thread.locator('.waBubble.outbound .statusBadge')).toHaveText('sent');
  await expect(thread.getByRole('status').filter({ hasText: 'Approved. The reply was sent.' })).toBeVisible();
});

test('outside the 24-hour window only a template can be sent, with its values filled in', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await page.locator('.waConversation').filter({ hasText: 'Sara Trading' }).click();
  const thread = page.locator('.waThread');
  await expect(thread.locator('.waWindow')).toHaveText('24-hour window closed: templates only');
  await thread.getByRole('button', { name: 'Message', exact: true }).click();
  await expect(thread.getByRole('note')).toContainText('More than 24 hours passed');
  await thread.getByRole('button', { name: 'Choose a template' }).click();
  const select = thread.getByRole('combobox', { name: 'Template' });
  await expect(select.locator('option')).toHaveCount(2); // the placeholder and the one approved template
  await select.selectOption('order_update|en_US');
  await thread.getByLabel('Value 1').fill('Sara');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect(thread.getByRole('alert')).toContainText('Fill in every value');
  await thread.getByLabel('Value 2').fill('A-1001');
  await expect(thread.locator('.waTemplatePreview')).toContainText('Hi Sara, your order A-1001 is on its way.');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect.poll(() => sent.sends).toEqual([{ template: { name: 'order_update', language: 'en_US', parameters: ['Sara', 'A-1001'] } }]);
});

test('search, filters, and a failed load that can be retried', async ({ page }) => {
  await mockApi(page, { failList: true });
  await openInbox(page);
  await expect(page.getByRole('alert')).toContainText('The database is not reachable.');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.waConversation')).toHaveCount(2);
  await page.getByRole('button', { name: 'Unread', exact: true }).click();
  await expect(page.locator('.waConversation')).toHaveCount(1);
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('nothing like this');
  await page.getByRole('searchbox', { name: 'Search conversations' }).press('Enter');
  await expect(page.getByText('No matching conversations')).toBeVisible();
  await page.getByRole('button', { name: 'Show all conversations' }).click();
  await expect(page.locator('.waConversation')).toHaveCount(2);
});

test('an owner connects the number from Setup; the form says what is missing', async ({ page }) => {
  const sent = await mockApi(page, { account: notConnected, conversations: [] });
  await openInbox(page);
  await expect(page.getByText('WhatsApp is not connected yet')).toBeVisible();
  await page.getByRole('button', { name: 'Connect WhatsApp' }).click();
  await expect(page.locator('.waWebhook code')).toHaveText('http://localhost:8000/api/v1/whatsapp/webhook');
  await page.getByLabel('Phone number ID').fill('106540352242922');
  await page.getByLabel('WhatsApp Business Account ID').fill('102290129340398');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('enter the access token, the app secret and a verify token');
  await page.getByLabel('Permanent access token (system user)').fill('example-token');
  await page.getByLabel('App secret').fill('example-secret');
  await page.getByRole('button', { name: 'Generate' }).click();
  await page.getByLabel('Your own WhatsApp number (for the test)').fill('+966 500 000 999');
  const retention = page.getByLabel('Keep message history');
  await expect(retention.locator('option').first()).toHaveText('Server default (365 days)');
  await retention.selectOption('90');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connected as Tyre Shop' })).toBeVisible();
  expect(sent.saves).toHaveLength(1);
  const saved = sent.saves[0];
  expect(saved).toMatchObject({ phone_number_id: '106540352242922', business_account_id: '102290129340398', access_token: 'example-token', app_secret: 'example-secret', owner_test_number: '966500000999', message_retention_days: 90 });
  expect(String(saved.verify_token)).toMatch(/^[A-Za-z0-9_-]{32}$/);
  await expect(page.getByLabel('Permanent access token (system user)')).toHaveValue('');
});

test('an owner puts a template on the auto-send list', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await page.getByRole('button', { name: 'Templates', exact: true }).click();
  await expect(page.locator('.waTemplateCard')).toHaveCount(2);
  await page.locator('.waTemplateCard').filter({ hasText: 'order_update' }).getByRole('checkbox').click();
  await expect(page.locator('.waTemplateCard').filter({ hasText: 'order_update' }).getByRole('checkbox')).toBeChecked();
  await expect(page.getByText('order_update is now sent without asking.')).toBeVisible();
  expect(sent.saves[0]).toMatchObject({ auto_send_templates: ['order_update'] });
  expect(sent.saves[0]).not.toHaveProperty('access_token');
});

test('members read and ask to send but cannot approve or change the setup; viewers are told who can', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await openInbox(page);
  await page.getByRole('button', { name: 'Setup', exact: true }).click();
  await expect(page.getByText('Only owners change the connection')).toBeVisible();
  await page.goto('about:blank');
  const viewer = await page.context().newPage();
  await mockApi(viewer, { role: 'viewer' });
  await viewer.goto('/#whatsapp');
  await expect(viewer.getByRole('heading', { name: 'WhatsApp is for owners and members' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  test(`the inbox has no serious axe violations (${locale})`, async ({ page }) => {
    await mockApi(page);
    await useSavedLocale(page, locale);
    await page.goto('/#whatsapp');
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
    await page.locator('.waConversation').first().click();
    await expect(page.locator('.waBubble').first()).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    expect(serious).toEqual([]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test('in Arabic the inbox reads right to left and customer text keeps its own direction', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: 'واتساب' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'بانتظار الرد' })).toBeVisible();
  await page.locator('.waConversation').filter({ hasText: 'Sara Trading' }).click();
  await expect(page.locator('.waWindow')).toHaveText('انتهت نافذة الـ24 ساعة: القوالب فقط');
  await expect(page.locator('.waText').first()).toHaveAttribute('dir', 'auto');
});
