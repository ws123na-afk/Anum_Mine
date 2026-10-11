import { expect, test, type Page, type Route } from '@playwright/test';
import { emailAccount, emailDetail, emailDraft, emailMessages, emailSetup, mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Unified inbox (docs/email.md): connect a mailbox, read a message as plain text, draft a reply with
// its provenance, and send only through an owner's approval with the payload hash shown.
const hash = '9d'.repeat(32);
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const sendApproval = {
  id: 'approval_send', task_id: 'task_send', action: 'send_email', risk_level: 'high', status: 'pending', reason: 'External or high-impact actions require explicit approval.',
  created_at: new Date().toISOString(), run_id: 'run_send', step_id: 'step_send', payload_hash: hash, expires_at: later, target: null,
  arguments: { draft_id: 'edr_1', account_id: 'emb_shop', provider: 'gmail', from: 'shop@example.com', to: ['sara@example.com'], cc: [], subject: emailDraft.subject, body: emailDraft.body, in_reply_to: 'eml_question', drafted_by: 'model:anthropic/claude-sonnet-5-5' },
};

type Calls = { complete: unknown[]; start: unknown[]; approve: unknown[]; askSend: number; messageQueries: string[]; patches: unknown[] };

async function mockInbox(page: Page, options: { role?: 'owner' | 'member' | 'viewer'; accounts?: unknown[]; messagesStatus?: number } = {}): Promise<Calls> {
  const calls: Calls = { complete: [], start: [], approve: [], askSend: 0, messageQueries: [], patches: [] };
  let draft: Record<string, unknown> = { ...emailDraft };
  await mockWorkspaceApi(page);
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const role = options.role ?? 'owner';
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/email/setup') {
      if (role === 'viewer') return json({ error: { code: 'forbidden', message: 'Reading email needs the owner or member role in this workspace.' } }, 403);
      return json({ ...emailSetup, can_connect: role === 'owner' });
    }
    if (path === '/api/v1/email/accounts') return json(options.accounts ?? [emailAccount]);
    if (path === '/api/v1/email/messages') {
      calls.messageQueries.push(url.search);
      if (options.messagesStatus) return json({ error: { code: 'internal', message: 'Gmail answered 500: backend error' } }, options.messagesStatus);
      const category = url.searchParams.get('category');
      const items = emailMessages.filter((m) => !category || m.category === category);
      return json({ items: url.searchParams.get('q') === 'nothing' ? [] : items, next_cursor: null, accounts: 1 });
    }
    if (path === '/api/v1/email/oauth/complete') { calls.complete.push(request.postDataJSON()); return json({ ...emailAccount, address: 'owner@contoso.example', provider: 'outlook' }, 201); }
    if (path.startsWith('/api/v1/email/oauth/') && path.endsWith('/start')) {
      calls.start.push({ provider: path.split('/')[5], ...request.postDataJSON() });
      return json({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?client_id=example&state=s', expires_at: later });
    }
    if (path === '/api/v1/email/drafts/edr_1' && method === 'PATCH') {
      calls.patches.push(request.postDataJSON());
      draft = { ...draft, ...(request.postDataJSON() as object), source: 'person' };
      return json(draft);
    }
    if (path === '/api/v1/email/drafts/edr_1/send-approval') {
      calls.askSend += 1;
      draft = { ...draft, status: 'pending_approval', approval: sendApproval };
      return json({ draft, approval: sendApproval });
    }
    if (path === '/api/v1/approvals/approval_send/approve') {
      calls.approve.push(request.postDataJSON());
      draft = { ...draft, status: 'sent', approval: null, sent_at: new Date().toISOString() };
      return json({ task: { id: 'task_send', title: 'Send', prompt: 'Send', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: later, updated_at: later }, run: { id: 'run_send', task_id: 'task_send', status: 'completed', steps: [], result: 'Sent to sara@example.com' }, approval: { ...sendApproval, status: 'approved' } });
    }
    if (path.startsWith('/api/v1/email/messages/') && path.endsWith('/read')) {
      return json({ status: 'done', summary: 'Marked read', approval: null, message: { ...emailMessages[0], is_read: true } });
    }
    if (path.startsWith('/api/v1/email/messages/') && method === 'GET') return json(emailDetail(path.split('/').pop() ?? '', [draft]));
    return route.fallback();
  });
  return calls;
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('owners read a message as plain text and send the AI draft only through approval', async ({ page }) => {
  const calls = await mockInbox(page);
  await page.goto('/#inbox');
  await expect(page.getByRole('heading', { level: 1, name: 'Inbox' })).toBeVisible();
  await expect(page.getByText('shop@example.com').first()).toBeVisible();
  await expect(page.locator('.inboxSetupNote').filter({ hasText: 'Zoho Mail' })).toContainText('ANUM_EMAIL_ZOHO_CLIENT_ID');
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Do you have 205/55R16 in stock?' })).toBeVisible();
  // The email's markup is text: nothing in it runs.
  await expect(page.locator('.inboxBodyText').first()).toContainText('<script>window.injected = true</script>');
  expect(await page.evaluate(() => (window as unknown as { injected?: boolean }).injected)).toBeUndefined();
  await expect(page.locator('.inboxSummary')).toContainText('Sara asks whether 205/55R16 tyres are in stock.');
  await expect(page.getByText(/Drafted by AI \(.*anthropic\/claude-sonnet-5-5/)).toBeVisible();
  await expect(page.getByRole('button', { name: /photo\.jpg/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Hello Sara, yes we have it.');
  await page.getByRole('button', { name: 'Ask approval to send' }).click();
  const card = page.getByRole('group', { name: 'Send approval' });
  await expect(card).toBeVisible();
  expect(calls.patches).toEqual([{ to: ['sara@example.com'], cc: [], subject: emailDraft.subject, body: 'Hello Sara, yes we have it.' }]);
  expect(calls.askSend).toBe(1);
  await expect(card).toContainText('shop@example.com');
  await expect(card).toContainText(hash.slice(0, 12));
  await card.getByRole('button', { name: 'Approve and send' }).click();
  await expect(page.getByText(/Sent to sara@example.com/).first()).toBeVisible();
  expect(calls.approve).toEqual([{ payload_hash: hash }]);
});

test('members draft and ask, but an owner approves sending', async ({ page }) => {
  await mockInbox(page, { role: 'member' });
  await page.goto('/#inbox');
  await expect(page.getByText('Only workspace owners can connect mailboxes.')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Connect Gmail/ })).toHaveCount(0);
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await page.getByRole('button', { name: 'Ask approval to send' }).click();
  await expect(page.getByText('A workspace owner approves sending; it is waiting in Approvals.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve and send' })).toHaveCount(0);
});

test('viewers are told who can read the inbox', async ({ page }) => {
  await mockInbox(page, { role: 'viewer' });
  await page.goto('/#inbox');
  await expect(page.getByRole('heading', { name: 'Email is for workspace owners and members' })).toBeVisible();
});

test('connecting goes to the provider and finishes when the browser comes back', async ({ page }) => {
  const calls = await mockInbox(page, { accounts: [] });
  await page.route('https://accounts.google.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Google</title><p>consent</p>' }));
  await page.goto('/#inbox');
  await expect(page.getByText('No mailbox is connected yet. Connect one below')).toBeVisible();
  await page.getByLabel('Only visible to me (a private mailbox)').check();
  await page.getByRole('button', { name: 'Connect Gmail / Google Workspace' }).click();
  await expect(page).toHaveURL(/accounts\.google\.com/);
  expect(calls.start).toEqual([{ provider: 'gmail', visibility: 'private' }]);

  await page.goto('/?email_connect=eoc_123#inbox');
  await expect(page.getByText('Connected owner@contoso.example. The latest messages are synced.')).toBeVisible();
  expect(calls.complete).toEqual([{ connection: 'eoc_123' }]);
  expect(new URL(page.url()).search).toBe('');
});

test('a failed connection return explains what to do', async ({ page }) => {
  await mockInbox(page);
  await page.goto('/?email_connect_error=invalid_state#inbox');
  await expect(page.getByRole('alert')).toContainText('Start the connection again.');
});

test('filters, no-match state and a list error with retry', async ({ page }) => {
  const calls = await mockInbox(page);
  await page.goto('/#inbox');
  await page.getByLabel('Category').selectOption('invoice');
  await expect(page.getByRole('button', { name: /Invoice 1042/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Sara Customer/ })).toHaveCount(0);
  expect(calls.messageQueries.at(-1)).toContain('category=invoice');
  await page.getByRole('searchbox').fill('nothing');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Nothing matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('button', { name: /Sara Customer/ })).toBeVisible();
});

test('a provider error on the list says what failed and offers a retry', async ({ page }) => {
  await mockInbox(page, { messagesStatus: 502 });
  await page.goto('/#inbox');
  await expect(page.getByRole('alert')).toContainText('Gmail answered 500');
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('Arabic: the inbox is right to left without horizontal overflow', async ({ page }) => {
  await mockInbox(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#inbox');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'البريد' })).toBeVisible();
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await expect(page.getByText(/كتبها الذكاء الاصطناعي/)).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('narrow screens show the list or the message, with a way back', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockInbox(page);
  await page.goto('/#inbox');
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await expect(page.locator('.inboxList')).toBeHidden();
  await page.getByRole('button', { name: 'Back to the list' }).click();
  await expect(page.getByRole('button', { name: /Sara Customer/ })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
