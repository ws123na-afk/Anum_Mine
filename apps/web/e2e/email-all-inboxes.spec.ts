import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// All inboxes (docs/email.md#all-inboxes): one list over every readable mailbox with a mailbox badge
// per message, filters (mailbox, unread, starred, dates), search, paging, opening a message in its own
// mailbox so a reply is sent from that address after approval, and every state (loading, empty,
// failure, a mailbox that could not be read, permission denied). Plainly fake fixtures only.

const own = { id: 'mailbox_own', address: 'admin@example.com', display_name: 'Admin', status: 'connected', last_error: null, last_checked_at: null, created_at: '2026-10-01T08:00:00Z', access: 'owner', owner_user_id: 'user_local', shared_with: 0, grant_id: null };
const shared = { id: 'mailbox_sales', address: 'sales@example.com', display_name: 'Sales', status: 'connected', last_error: null, last_checked_at: null, created_at: '2026-10-02T08:00:00Z', access: 'send', owner_user_id: 'user_other', shared_with: 0, grant_id: 'grant_one' };
const sendHash = 'ef'.repeat(32);

interface Item { account: typeof own | typeof shared; uid: number; subject: string; date: string; seen: boolean; flagged: boolean }
interface Options { empty?: boolean; fail?: boolean; denied?: boolean; salesOffline?: boolean }

function seed(): Item[] {
  const items: Item[] = [];
  for (let i = 0; i < 30; i += 1) {
    const account = i % 2 === 0 ? own : shared;
    items.push({ account, uid: 200 - i, subject: i === 0 ? 'Tyre order for next week' : i === 1 ? 'Sales lead from the fair' : `Update number ${30 - i}`, date: new Date(Date.UTC(2026, 9, 14, 9, 0) - i * 6 * 3600_000).toISOString(), seen: i % 3 !== 0, flagged: i === 3 });
  }
  return items;
}

async function mockAllInboxes(page: Page, options: Options = {}) {
  const items = options.empty ? [] : seed();
  const calls = { queries: [] as string[], drafts: [] as Record<string, unknown>[], sends: [] as unknown[], flags: [] as unknown[] };
  const view = (m: Item) => ({
    uid: m.uid, folder: 'INBOX', subject: m.subject, sender: { name: 'Sara Example', address: 'sara@example.com' }, to: [{ name: '', address: m.account.address }], cc: [], date: m.date,
    message_id: `<m${m.uid}@example.com>`, seen: m.seen, flagged: m.flagged, answered: false, size: 1200, maybe_attachments: false,
    account_id: m.account.id, account_address: m.account.address, account_name: m.account.display_name, access: m.account.access,
  });
  const draftView = (d: Record<string, unknown>) => ({
    id: d.id, account_id: d.account_id, identity_id: null, from_address: d.from, from_name: '', kind: d.kind, to: d.to, cc: [], bcc: [], subject: d.subject, body: 'Quoted text.',
    in_reply_to: '<m200@example.com>', message_id: `<${String(d.id)}@example.com>`, status: d.status, error: null, approval_note: null, saved_to_sent: null, sent_at: null, send_at: null,
    attachments: [], attachment_note: null, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-14T09:00:00Z',
    approval: d.status === 'pending_approval' ? {
      id: 'approval_send', task_id: 'task_send', action: 'send_mail', risk_level: 'high', status: 'pending', reason: 'Sending mail needs approval.', created_at: '2026-10-14T09:00:00Z',
      run_id: 'run_send', step_id: 'step_send', payload_hash: sendHash, expires_at: new Date(Date.now() + 3600_000).toISOString(), target: null,
      arguments: { from: d.from, to: d.to, subject: d.subject, body: 'Quoted text.', send_at: null, attachments: [] },
    } : null,
  });
  await page.route('http://localhost:8000/api/v1/mail/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/mail', '');
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/setup') {
      return json({
        enabled: true, configured: true, tenant_allowed: true, imap_host: 'mail.example.com', imap_port: 993, smtp_host: 'mail.example.com', smtp_port: 587, smtp_security: 'starttls',
        allowed_domains: ['example.com'], can_read: true, can_act: true, max_recipients: 50, send_per_hour: 30, attachment_max_bytes: 10485760, attachments_per_message: 10,
        attachments_total_bytes: 20971520, attachment_extensions: ['.pdf'], attachments_accepted: 'PDF', schedule_max_days: 30, accounts: [own, shared],
      });
    }
    if (path === '/unread') return json({ enabled: true, sync_running: false, total: 0, checked_at: null, mailboxes: [] });
    if (path === '/ai/status') return json({ available: false, reason: 'No model is set up.', model: null, can_read: true, can_act: true });
    if (path === '/all-inboxes') {
      calls.queries.push(url.search);
      if (options.denied) return json({ error: { code: 'forbidden', message: 'Reading email needs the owner or member role in this workspace.' } }, 403);
      if (options.fail) return json({ error: { code: 'offline', message: 'The mail server did not answer.' } }, 503);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const accountId = url.searchParams.get('account_id');
      const since = url.searchParams.get('since');
      const until = url.searchParams.get('until');
      let found = items.filter((m) => !(options.salesOffline && m.account.id === shared.id));
      if (accountId) found = found.filter((m) => m.account.id === accountId);
      if (url.searchParams.get('unread')) found = found.filter((m) => !m.seen);
      if (url.searchParams.get('flagged')) found = found.filter((m) => m.flagged);
      if (q) found = found.filter((m) => m.subject.toLowerCase().includes(q));
      if (since) found = found.filter((m) => m.date.slice(0, 10) >= since);
      if (until) found = found.filter((m) => m.date.slice(0, 10) <= until);
      const offset = Number(url.searchParams.get('cursor') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 25);
      const pageItems = found.slice(offset, offset + limit);
      const mailboxes = [own, shared].filter((a) => !accountId || a.id === accountId).map((a) => ({
        account_id: a.id, address: a.address, display_name: a.display_name, access: a.access,
        state: options.salesOffline && a.id === shared.id ? 'offline' : 'ok', error: options.salesOffline && a.id === shared.id ? 'The mail server did not answer.' : null, total: 0,
      }));
      return json({ items: pageItems.map(view), total: found.length - offset, next_cursor: offset + limit < found.length ? String(offset + limit) : null, mailboxes });
    }
    const message = /^\/accounts\/([^/]+)\/message$/.exec(path);
    if (message && method === 'GET') {
      const uid = Number(url.searchParams.get('uid'));
      const item = items.find((m) => m.account.id === message[1] && m.uid === uid);
      if (!item) return json({ error: { code: 'not_found', message: 'That message is no longer in this folder.' } }, 404);
      return json({ message: { ...view(item), seen: true }, reply_to: [], text: `Body of ${item.subject}.`, text_truncated: false, html: null, remote_images_blocked: 0, inline_images_hidden: 0, attachments: [] });
    }
    if (/^\/accounts\/[^/]+\/flags$/.test(path) && method === 'POST') { calls.flags.push(request.postDataJSON()); return json({ status: 'done', summary: 'Done.' }); }
    if (/^\/accounts\/[^/]+\/identities$/.test(path)) return json([]);
    if (path === '/drafts' && method === 'POST') {
      const body = request.postDataJSON() as { account_id: string; kind: string; uid?: number };
      const account = body.account_id === shared.id ? shared : own;
      const original = items.find((m) => m.account.id === account.id && m.uid === body.uid);
      const draft = { id: `draft_${calls.drafts.length + 1}`, account_id: account.id, from: account.address, kind: body.kind, to: ['sara@example.com'], subject: original ? `Re: ${original.subject}` : '', status: 'draft' };
      calls.drafts.push(draft);
      return json(draftView(draft), 201);
    }
    const draftPath = /^\/drafts\/([^/]+)(\/send)?$/.exec(path);
    if (draftPath) {
      const draft = calls.drafts.find((d) => d.id === draftPath[1]);
      if (!draft) return json({ error: { code: 'not_found', message: 'Message not found.' } }, 404);
      if (draftPath[2] && method === 'POST') { calls.sends.push(request.postDataJSON()); draft.status = 'pending_approval'; return json({ draft: draftView(draft), approval: draftView(draft).approval }); }
      return json(draftView(draft));
    }
    if (method === 'GET') return json([]);
    return json({ error: { code: 'not_found', message: 'Not found.' } }, 404);
  });
  return calls;
}

async function openAllInboxes(page: Page, options: Options = {}, english = true) {
  await mockWorkspaceApi(page);
  const calls = await mockAllInboxes(page, options);
  await page.addInitScript(() => { try { localStorage.setItem('anum.mail.mailbox', '__all__'); } catch { /* storage blocked */ } });
  await page.goto('/#email');
  if (english) await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  return calls;
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const list = (page: Page) => page.getByRole('region', { name: 'All inboxes' });

test('the mailbox picker opens one list over every inbox, each row with its mailbox', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockAllInboxes(page);
  await page.goto('/#email');
  await page.getByRole('combobox', { name: 'Mailbox' }).first().selectOption({ label: 'All inboxes' });
  const rows = list(page).locator('.mailRow');
  await expect(rows.first()).toContainText('Tyre order for next week');
  await expect(rows.first().locator('.mailAllBadge')).toContainText('Mailbox: Admin');
  await expect(rows.nth(1).locator('.mailAllBadge')).toContainText('Mailbox: Sales (shared with you)');
  await expect(list(page).getByText('25 of 30')).toBeVisible();
  await list(page).getByRole('button', { name: 'Load more' }).click();
  await expect(rows).toHaveCount(30);
  await expect(list(page).getByRole('button', { name: 'Load more' })).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('filters by mailbox, unread, starred, dates and search go to the server', async ({ page }) => {
  const calls = await openAllInboxes(page);
  const region = list(page);
  await region.getByRole('combobox', { name: 'Mailbox' }).selectOption(shared.id);
  await expect(region.locator('.mailRow')).toHaveCount(15);
  expect(calls.queries.at(-1)).toContain('account_id=mailbox_sales');
  await region.getByRole('button', { name: 'Clear search and filters' }).first().click();
  await region.getByText('Starred').click();
  await expect(region.locator('.mailRow')).toHaveCount(1);
  expect(calls.queries.at(-1)).toContain('flagged=true');
  await region.getByRole('button', { name: 'Clear search and filters' }).first().click();
  await region.getByLabel('From', { exact: true }).fill('2026-10-14');
  await expect(region.locator('.mailRow')).toHaveCount(2);
  expect(calls.queries.at(-1)).toContain('since=2026-10-14');
  await region.getByLabel('To', { exact: true }).fill('2026-10-01');
  await expect(region.getByText('The start date is after the end date.', { exact: false })).toBeVisible();
  await region.getByRole('button', { name: 'Clear search and filters' }).first().click();
  const search = region.getByRole('search');
  await search.getByRole('searchbox').fill('nothing here');
  await search.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(region.getByRole('heading', { name: 'Nothing matches in your inboxes' })).toBeVisible();
  expect(calls.queries.at(-1)).toContain('q=nothing+here');
});

test('a message opens in its own mailbox and the reply goes from that address after approval', async ({ page }) => {
  const calls = await openAllInboxes(page);
  await list(page).locator('.mailRow').nth(1).locator('.mailRowOpen').click();
  await expect(page.getByRole('heading', { name: 'Sales lead from the fair' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open in sales@example.com' })).toBeVisible();
  await page.getByRole('button', { name: 'Reply', exact: true }).click();
  // The composer writes from the message's own mailbox.
  await expect(page.locator('.mailComposer')).toContainText('sales@example.com');
  expect(calls.drafts[0]).toMatchObject({ account_id: shared.id, kind: 'reply' });
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('group', { name: 'Approve sending' })).toBeVisible();
  await expect(page.locator('.mailApproval')).toContainText('sales@example.com');
  expect(calls.sends).toHaveLength(1);
});

test('Open in the mailbox switches the page to that mailbox', async ({ page }) => {
  await openAllInboxes(page);
  await list(page).locator('.mailRow').first().locator('.mailRowOpen').click();
  await page.getByRole('button', { name: 'Open in admin@example.com' }).click();
  await expect(page.getByRole('region', { name: 'All inboxes' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Mailbox' }).first()).toHaveValue(own.id);
});

test('a mailbox that could not be read is named and the rest still list', async ({ page }) => {
  await openAllInboxes(page, { salesOffline: true });
  await expect(list(page).getByText('sales@example.com could not be read', { exact: false })).toBeVisible();
  await expect(list(page).locator('.mailRow').first()).toContainText('Tyre order for next week');
  await expect(list(page).getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('an empty list offers to check again', async ({ page }) => {
  await openAllInboxes(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'Nothing in any inbox' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check again' })).toBeVisible();
});

test('a failure says what failed and offers to try again', async ({ page }) => {
  await openAllInboxes(page, { fail: true });
  await expect(page.getByText('The inboxes could not be loaded', { exact: false })).toBeVisible();
  await expect(list(page).getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('permission denied names who reads email', async ({ page }) => {
  await openAllInboxes(page, { denied: true });
  await expect(page.getByRole('heading', { name: 'Your role cannot read email' })).toBeVisible();
});

test('All inboxes has no axe violations', async ({ page }) => {
  await openAllInboxes(page);
  await expect(list(page).locator('.mailRow').first()).toBeVisible();
  const results = await new AxeBuilder({ page }).include('main').analyze();
  expect(results.violations).toEqual([]);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  for (const locale of ['en', 'ar'] as const) {
    test(`All inboxes fits ${width}x${height} in ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await useSavedLocale(page, locale);
      await openAllInboxes(page, {}, locale === 'en');
      await expect(page.locator('.mailAll .mailRow').first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.dir)).toBe(locale === 'ar' ? 'rtl' : 'ltr');
      expect(await noHorizontalOverflow(page)).toBe(true);
      const box = await page.locator('.mailAll').boundingBox();
      expect(box && box.x >= -1 && box.x + box.width <= width + 1).toBe(true);
      await page.screenshot({ path: `test-results/all-inboxes-${locale}-${width}.png` });
    });
  }
}
