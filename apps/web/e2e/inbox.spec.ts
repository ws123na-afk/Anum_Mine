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

// A new email (POST /email/drafts) and its send approval, with Cc and Bcc in the approved arguments.
const newHash = '7e'.repeat(32);
const newDraftBase = { ...emailDraft, id: 'edr_new', message_id: null, kind: 'new', source: 'person', model_provider: null, model_name: null, saved_in_provider: false };
const newApproval = (draft: Record<string, unknown>) => ({
  ...sendApproval, id: 'approval_new', task_id: 'task_new', run_id: 'run_new', payload_hash: newHash,
  arguments: { draft_id: 'edr_new', account_id: 'emb_shop', provider: 'gmail', from: 'shop@example.com', to: draft.to, cc: draft.cc, bcc: draft.bcc, subject: draft.subject, body: draft.body, in_reply_to: null, drafted_by: 'person:user_local' },
});
const createdTicket = { id: 'bst_1', reference: 'T-1A2B3C', title: emailDraft.subject, source: 'email', source_ref: 'eml_question', severity: 'normal', status: 'new', replies: [], activities: [], can_reply: true, reply_channel: 'email' };

type Calls = { complete: unknown[]; start: unknown[]; approve: unknown[]; askSend: number; messageQueries: string[]; patches: unknown[]; creates: unknown[]; newPatches: unknown[]; askNew: number; approveNew: unknown[]; tickets: unknown[] };
type InboxOptions = { role?: 'owner' | 'member' | 'viewer'; accounts?: unknown[]; messagesStatus?: number; drafts?: Record<string, unknown>[]; ticketConflict?: boolean };

async function mockInbox(page: Page, options: InboxOptions = {}): Promise<Calls> {
  const calls: Calls = { complete: [], start: [], approve: [], askSend: 0, messageQueries: [], patches: [], creates: [], newPatches: [], askNew: 0, approveNew: [], tickets: [] };
  let draft: Record<string, unknown> = { ...emailDraft };
  let newDraft: Record<string, unknown> | null = options.drafts?.find((d) => d.id === 'edr_new') ?? null;
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
    if (path === '/api/v1/email/drafts' && method === 'GET') return json([...(newDraft ? [newDraft] : []), ...(options.drafts ?? [draft]).filter((d) => d.id !== 'edr_new')]);
    if (path === '/api/v1/email/drafts' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      calls.creates.push(body);
      newDraft = { ...newDraftBase, ...body, status: 'draft', approval: null };
      return json({ status: 'done', summary: 'Draft saved.', draft: newDraft, approval: null, warning: null }, 201);
    }
    if (path === '/api/v1/email/drafts/edr_new' && method === 'PATCH') {
      calls.newPatches.push(request.postDataJSON());
      newDraft = { ...newDraft, ...(request.postDataJSON() as object) };
      return json(newDraft);
    }
    if (path === '/api/v1/email/drafts/edr_new/send-approval') {
      calls.askNew += 1;
      const approval = newApproval(newDraft ?? {});
      newDraft = { ...newDraft, status: 'pending_approval', approval };
      return json({ draft: newDraft, approval });
    }
    if (path === '/api/v1/approvals/approval_new/approve') {
      calls.approveNew.push(request.postDataJSON());
      newDraft = { ...newDraft, status: 'sent', approval: null, sent_at: new Date().toISOString() };
      return json({ task: { id: 'task_new', title: 'Send', prompt: 'Send', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: later, updated_at: later }, run: { id: 'run_new', task_id: 'task_new', status: 'completed', steps: [], result: 'Sent to lina@example.com' }, approval: { ...newApproval(newDraft), status: 'approved' } });
    }
    if (path === '/api/v1/business/support/tickets/from-message' && method === 'POST') {
      calls.tickets.push(request.postDataJSON());
      if (options.ticketConflict) return json({ error: { code: 'conflict', message: 'This email is already linked to open ticket T-OPEN1 (bst_open).' } }, 409);
      return json(createdTicket, 201);
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
  // The mailbox setup sits behind the status chip; the summary band reads GET /email/summary and /email/drafts.
  await expect(page.getByRole('region', { name: 'Inbox at a glance' })).toContainText('Drafts waiting');
  await page.getByRole('button', { name: /1 mailbox/ }).click();
  const mailboxes = page.getByRole('dialog', { name: 'Mailboxes' });
  await expect(mailboxes.getByText('shop@example.com').first()).toBeVisible();
  await expect(mailboxes.locator('.inboxSetupNote').filter({ hasText: 'Zoho Mail' })).toContainText('ANUM_EMAIL_ZOHO_CLIENT_ID');
  await mailboxes.getByRole('button', { name: 'Close' }).click();
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
  expect(calls.patches).toEqual([{ to: ['sara@example.com'], cc: [], bcc: [], subject: emailDraft.subject, body: 'Hello Sara, yes we have it.' }]);
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
  await expect(page).toHaveURL(/^https:\/\/accounts\.google\.com\//);
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

// ---- A new email (POST /email/drafts) -------------------------------------------------------

async function openCompose(page: Page) {
  await page.goto('/#inbox');
  await expect(page.getByRole('heading', { level: 1, name: 'Inbox' })).toBeVisible();
  await page.getByRole('button', { name: 'New email' }).click();
  const drawer = page.getByRole('dialog', { name: 'New email' });
  await expect(drawer).toBeVisible();
  return drawer;
}

test('owners write a new email, save it, ask to send and approve it with the payload hash', async ({ page }) => {
  const calls = await mockInbox(page);
  const drawer = await openCompose(page);
  await expect(drawer.getByLabel('From')).toHaveValue('emb_shop');
  await expect(drawer.getByText('To send attachments, use the Email Center.')).toBeVisible();
  await drawer.getByLabel('To', { exact: true }).fill('Lina@Example.com');
  await drawer.getByRole('button', { name: 'Add Cc or Bcc' }).click();
  await drawer.getByLabel('Cc', { exact: true }).fill('ops@example.com');
  await drawer.getByLabel('Bcc', { exact: true }).fill('audit@example.com');
  await drawer.getByLabel('Subject').fill('Your quote for 4 tyres');
  await drawer.getByLabel('Message', { exact: true }).fill('Hello Lina,\n\nYour quote is ready.');
  await drawer.getByRole('button', { name: 'Save draft' }).click();
  await expect(drawer.getByText('Draft saved.')).toBeVisible();
  expect(calls.creates).toEqual([{ account_id: 'emb_shop', to: ['lina@example.com'], cc: ['ops@example.com'], bcc: ['audit@example.com'], subject: 'Your quote for 4 tyres', body: 'Hello Lina,\n\nYour quote is ready.' }]);
  // The saved draft opens in the same card replies use, with Bcc and the mailbox it sends from.
  await expect(drawer.getByLabel('Bcc', { exact: true })).toHaveValue('audit@example.com');
  await expect(drawer.locator('.inboxMeta').first()).toContainText('shop@example.com');

  await drawer.getByRole('button', { name: 'Ask approval to send' }).click();
  const card = drawer.getByRole('group', { name: 'Send approval' });
  await expect(card).toBeVisible();
  expect(calls.askNew).toBe(1);
  expect(calls.newPatches).toEqual([]);
  await expect(card).toContainText('lina@example.com');
  await expect(card).toContainText('ops@example.com');
  await expect(card).toContainText('audit@example.com');
  await expect(card).toContainText(newHash.slice(0, 12));
  await card.getByRole('button', { name: 'Approve and send' }).click();
  await expect(drawer.getByText(/Sent to lina@example.com/).first()).toBeVisible();
  expect(calls.approveNew).toEqual([{ payload_hash: newHash }]);
});

test('members ask to send a new email; it needs a recipient and an owner approves it', async ({ page }) => {
  const calls = await mockInbox(page, { role: 'member' });
  const drawer = await openCompose(page);
  await drawer.getByLabel('Message', { exact: true }).fill('Hello, your order ships today.');
  await drawer.getByRole('button', { name: 'Ask approval to send' }).click();
  await expect(drawer.getByRole('alert')).toContainText('Add at least one recipient.');
  await drawer.getByLabel('To', { exact: true }).fill('not an address');
  await drawer.getByRole('button', { name: 'Ask approval to send' }).click();
  await expect(drawer.getByRole('alert')).toContainText('Not an email address: not');
  expect(calls.creates).toEqual([]);
  await drawer.getByLabel('To', { exact: true }).fill('lina@example.com');
  await drawer.getByRole('button', { name: 'Ask approval to send' }).click();
  await expect(drawer.getByText('A workspace owner approves sending; it is waiting in Approvals.')).toBeVisible();
  expect(calls.creates).toHaveLength(1);
  expect(calls.askNew).toBe(1);
  await expect(drawer.getByRole('button', { name: 'Approve and send' })).toHaveCount(0);
});

test('a new-email draft in the queue opens in the compose drawer', async ({ page }) => {
  const waiting = { ...newDraftBase, to: ['lina@example.com'], subject: 'Quote follow-up', body: 'Hello again', status: 'draft', approval: null };
  await mockInbox(page, { drafts: [waiting, emailDraft] });
  await page.goto('/#inbox');
  const queue = page.locator('.inboxQueue');
  if ((await queue.getAttribute('open')) === null) await queue.locator('summary').click();
  await queue.getByRole('button', { name: /Quote follow-up/ }).click();
  const drawer = page.getByRole('dialog', { name: 'New email' });
  await expect(drawer.getByLabel('Subject')).toHaveValue('Quote follow-up');
  await expect(drawer.getByLabel('To', { exact: true })).toHaveValue('lina@example.com');
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  await expect(drawer).toHaveCount(0);
});

test('closing the drawer over unsaved edits of a reopened draft asks first', async ({ page }) => {
  const waiting = { ...newDraftBase, to: ['lina@example.com'], subject: 'Quote follow-up', body: 'Hello again', status: 'draft', approval: null };
  await mockInbox(page, { drafts: [waiting, emailDraft] });
  await page.goto('/#inbox');
  const queue = page.locator('.inboxQueue');
  if ((await queue.getAttribute('open')) === null) await queue.locator('summary').click();
  await queue.getByRole('button', { name: /Quote follow-up/ }).click();
  const drawer = page.getByRole('dialog', { name: 'New email' });
  await drawer.getByLabel('Subject').fill('Quote follow-up, edited');
  // Keep editing: the confirm is dismissed and the drawer stays with the edit.
  page.once('dialog', (dialog) => { expect(dialog.message()).toContain('Close without saving'); void dialog.dismiss(); });
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  await expect(drawer.getByLabel('Subject')).toHaveValue('Quote follow-up, edited');
  // The footer Close button asks too, and closing is allowed once confirmed.
  page.once('dialog', (dialog) => { void dialog.accept(); });
  await drawer.getByRole('button', { name: 'Close' }).last().click();
  await expect(drawer).toHaveCount(0);
});

// ---- Create a support ticket from an email --------------------------------------------------

test('a ticket is created from an email and links to it', async ({ page }) => {
  const calls = await mockInbox(page);
  await page.goto('/#inbox');
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await page.getByRole('button', { name: 'Create ticket' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create a support ticket' });
  await expect(dialog.getByLabel('Title')).toHaveValue('Do you have 205/55R16 in stock?');
  await dialog.getByLabel('Severity').selectOption('high');
  await dialog.getByRole('button', { name: 'Create ticket' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'Ticket T-1A2B3C created.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open ticket' })).toHaveAttribute('href', '#business-support/bst_1');
  expect(calls.tickets).toEqual([{ source: 'email', ref_id: 'eml_question', severity: 'high', title: 'Do you have 205/55R16 in stock?' }]);
});

test('an email that already has an open ticket says so and links to that ticket', async ({ page }) => {
  await mockInbox(page, { ticketConflict: true });
  await page.goto('/#inbox');
  await page.getByRole('button', { name: /Sara Customer/ }).click();
  await page.getByRole('button', { name: 'Create ticket' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create a support ticket' });
  await dialog.getByLabel('Title').fill('ab');
  await dialog.getByRole('button', { name: 'Create ticket' }).click();
  await expect(dialog.getByRole('alert')).toContainText('at least 3 characters');
  await dialog.getByLabel('Title').fill('');
  await dialog.getByRole('button', { name: 'Create ticket' }).click();
  await expect(dialog.getByRole('alert')).toContainText('already linked to open ticket T-OPEN1');
  await expect(dialog.getByRole('link', { name: 'Open T-OPEN1' })).toHaveAttribute('href', '#business-support/bst_open');
});

test('@gate the compose drawer and the ticket form fit every gate size', async ({ page }) => {
  await mockInbox(page);
  for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.goto('about:blank'); // a fresh page each size: no message left open from the last one
    const drawer = await openCompose(page);
    await drawer.getByRole('button', { name: 'Add Cc or Bcc' }).click();
    expect(await noHorizontalOverflow(page), `compose at ${width}x${height}`).toBe(true);
    const box = await drawer.boundingBox();
    expect(Boolean(box && box.x >= 0 && box.x + box.width <= width + 1), `drawer inside the viewport at ${width}x${height}`).toBe(true);
    await drawer.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: /Sara Customer/ }).click();
    await page.getByRole('button', { name: 'Create ticket' }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a support ticket' });
    await expect(dialog).toBeVisible();
    expect(await noHorizontalOverflow(page), `ticket form at ${width}x${height}`).toBe(true);
    const form = await dialog.boundingBox();
    expect(Boolean(form && form.x >= 0 && form.x + form.width <= width + 1), `ticket form inside the viewport at ${width}x${height}`).toBe(true);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
  }
});
