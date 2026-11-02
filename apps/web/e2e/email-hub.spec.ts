import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { mailAccount, mockMailApi } from './support/mailApi';
import { emailAccount, emailMessages, mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// One Email module (docs/email.md#one-email-module): one sidebar entry, one mailbox list over the
// provider inbox (Gmail) and the own mail server, All inboxes across both, a reader that opens each
// message in the back end that owns it, Write from any allowed address, and one Connections page.
type HubOptions = { role?: 'owner' | 'member' | 'viewer'; messagesFailOnce?: boolean; empty?: boolean };

const gmail = { ref: `provider:${emailAccount.id}`, id: emailAccount.id, backend: 'provider', source: 'gmail', address: emailAccount.address, display_name: null, status: 'connected', access: 'workspace', unread: 1, can_send: true };
const own = { ref: `server:${mailAccount.id}`, id: mailAccount.id, backend: 'server', source: 'mailserver', address: mailAccount.address, display_name: mailAccount.display_name, status: 'connected', access: 'owner', unread: 3, can_send: true };
const serverSummary = (uid: number, subject: string, date: string) => ({
  uid, folder: 'INBOX', subject, sender: { name: 'Omar Example', address: 'omar@example.com' }, to: [{ name: '', address: mailAccount.address }], cc: [], date,
  message_id: `<m${uid}@example.com>`, seen: false, flagged: false, answered: false, size: 1200, maybe_attachments: false,
  account_id: mailAccount.id, account_address: mailAccount.address, account_name: 'Admin', access: 'owner',
});

async function mockHub(page: Page, options: HubOptions = {}): Promise<{ unifiedQueries: string[] }> {
  const role = options.role ?? 'owner';
  await mockWorkspaceApi(page);
  const mail = await mockMailApi(page, { role });
  const serverTop = mail.state.messages.find((m) => m.folder === 'INBOX')!;
  const calls = { unifiedQueries: [] as string[] };
  let failed = false;
  await page.route('http://localhost:8000/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (!url.pathname.startsWith('/api/v1/email/unified/')) return route.fallback();
    if (role === 'viewer') return json({ error: { code: 'forbidden', message: 'Reading email needs the owner or member role in this workspace.' } }, 403);
    if (url.pathname === '/api/v1/email/unified/mailboxes') {
      return json({ items: options.empty ? [] : [gmail, own], provider: { state: options.empty ? 'none' : 'ok', detail: null }, server: { state: 'ok', detail: null }, can_act: true, can_manage: role === 'owner' });
    }
    calls.unifiedQueries.push(url.search);
    if (options.messagesFailOnce && !failed) { failed = true; return json({ error: { code: 'internal', message: 'The mail server did not answer.' } }, 503); }
    if (options.empty) return json({ items: [], next_cursor: null, provider: { state: 'none', detail: null }, server: { state: 'none', detail: null }, unreadable: [] });
    const q = url.searchParams.get('q') ?? '';
    const provider = emailMessages.map((m) => ({
      ref: `provider:${m.id}`, backend: 'provider', source: 'gmail', mailbox_id: m.account_id, mailbox_address: m.account_address, message_id: m.id, folder: null, uid: null,
      from_name: m.from_name, from_address: m.from_address, subject: m.subject, snippet: m.snippet, received_at: m.received_at, unread: !m.is_read, flagged: false,
      has_attachments: m.has_attachments, read_only: false, server_message: null,
    }));
    const server = [{
      ref: `server:${mailAccount.id}:INBOX:${serverTop.uid}`, backend: 'server', source: 'mailserver', mailbox_id: mailAccount.id, mailbox_address: mailAccount.address, message_id: null,
      folder: 'INBOX', uid: serverTop.uid, from_name: serverTop.sender.name, from_address: serverTop.sender.address, subject: serverTop.subject, snippet: '', received_at: serverTop.date,
      unread: !serverTop.seen, flagged: serverTop.flagged, has_attachments: false, read_only: false, server_message: serverSummary(serverTop.uid, serverTop.subject, serverTop.date),
    }];
    const items = [...provider, ...server].filter((m) => !q || m.subject.toLowerCase().includes(q.toLowerCase()));
    return json({ items, next_cursor: null, provider: { state: 'ok', detail: null }, server: { state: 'ok', detail: null }, unreadable: [] });
  });
  return calls;
}

test('one Email entry: All inboxes lists both sources with badges and opens each message in its own reader', async ({ page }) => {
  await mockHub(page);
  await page.goto('/#email');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  const sidebar = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(sidebar.getByRole('link', { name: 'Inbox', exact: true })).toHaveCount(0);
  const rail = page.getByRole('navigation', { name: 'Mailboxes' });
  await expect(rail.getByRole('button', { name: 'shop@example.com, Gmail' })).toBeVisible();
  await expect(rail.getByRole('button', { name: 'admin@example.com, Own server' })).toBeVisible();
  const list = page.locator('.hubRow');
  await expect(list).toHaveCount(3);
  await expect(page.locator('.hubRow .hubSource', { hasText: 'Gmail' }).first()).toBeVisible();
  await expect(page.locator('.hubRow .hubSource', { hasText: 'Own server' })).toHaveCount(1);
  // A Gmail message opens in the provider reader (plain text, AI actions); an own-server one in the Email Center reader.
  await page.getByRole('button', { name: /Do you have 205\/55R16 in stock/ }).click();
  await expect(page.getByText('Do you have 205/55R16 in stock?', { exact: false }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open in shop@example.com' })).toBeVisible();
  // On a phone the reader replaces the list: start again from the list.
  await page.reload();
  await page.locator('.hubRow.hubRow .mailRowOpen').filter({ has: page.locator('.hubSource', { hasText: 'Own server' }) }).click();
  await expect(page.locator('.mailReader h2')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open in admin@example.com' })).toBeVisible();
});

test('search, no match and a failed list with Try again', async ({ page }) => {
  const calls = await mockHub(page, { messagesFailOnce: true });
  await page.goto('/#email/all');
  await expect(page.getByText('The messages could not be loaded')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.locator('.hubRow')).toHaveCount(3);
  await page.getByRole('searchbox', { name: 'Search all inboxes' }).fill('invoice');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.hubRow')).toHaveCount(1);
  expect(calls.unifiedQueries.at(-1)).toContain('q=invoice');
  await page.getByRole('searchbox', { name: 'Search all inboxes' }).fill('nothing here');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'No message matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search and filters' }).first().click();
  await expect(page.locator('.hubRow')).toHaveCount(3);
});

test('a mailbox in the list opens its own back end; Write offers every allowed address', async ({ page }) => {
  await mockHub(page);
  await page.goto('/#email');
  const rail = page.getByRole('navigation', { name: 'Mailboxes' });
  await rail.getByRole('button', { name: 'shop@example.com, Gmail' }).click();
  await expect(page).toHaveURL(/#email\/mailbox\/provider%3Aemb_shop$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Sara Customer/ })).toBeVisible();
  await rail.getByRole('button', { name: 'admin@example.com, Own server' }).click();
  await expect(page.locator('.mailRowOpen').first()).toBeVisible();
  await rail.getByRole('button', { name: 'All inboxes' }).click();
  await page.getByRole('button', { name: 'Write' }).click();
  const drawer = page.getByRole('dialog', { name: 'Write from' });
  await expect(drawer.getByRole('radio')).toHaveCount(2);
  await drawer.getByRole('radio', { name: /admin@example.com/ }).check();
  await drawer.getByRole('button', { name: 'Start writing' }).click();
  await expect(page.locator('form.mailComposer, .mailComposer').first()).toBeVisible();
});

test('the old Inbox link opens the provider inbox inside Email; Connections lists every connection', async ({ page }) => {
  await mockHub(page);
  await page.goto('/#inbox');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Sara Customer/ })).toBeVisible();
  await page.getByRole('navigation', { name: 'Mailboxes' }).getByRole('button', { name: 'Connections' }).click();
  await expect(page.getByRole('heading', { name: 'Gmail, Outlook and Zoho' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Your own mail server' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Settings for shop@example.com' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Settings for admin@example.com' })).toBeVisible();
  await page.getByRole('button', { name: 'Connect or manage' }).click();
  await expect(page.getByRole('dialog', { name: 'Mailboxes' })).toBeVisible();
});

test('with no mailbox the page lands on how to connect; viewers are told who reads mail', async ({ page }) => {
  await mockHub(page, { empty: true });
  await page.goto('/#email/all');
  await expect(page.getByRole('heading', { name: 'Nothing in any inbox' })).toBeVisible();
  await expect(page.getByText('Connect a Gmail, Outlook or Zoho mailbox')).toBeVisible();
  await page.getByRole('button', { name: 'Connections' }).last().click();
  await expect(page.getByRole('heading', { name: 'Gmail, Outlook and Zoho' })).toBeVisible();
});

test('viewers see who may read email', async ({ page }) => {
  await mockHub(page, { role: 'viewer' });
  await page.goto('/#email');
  await expect(page.getByRole('heading', { name: 'Email is for owners and members' })).toBeVisible();
});

const SIZES = [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
for (const locale of ['en', 'ar'] as const) {
  test(`@gate the Email page fits every gate size and passes axe (${locale})`, async ({ page }) => {
    await useSavedLocale(page, locale);
    await mockHub(page);
    const axe = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
    for (const size of SIZES) {
      await page.setViewportSize(size);
      for (const hash of ['#email/all', '#email/connections']) {
        await page.goto('about:blank');
        await page.goto(`/${hash}`);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await expect(page.getByRole('navigation', { name: locale === 'ar' ? 'صناديق البريد' : 'Mailboxes' })).toBeVisible();
        if (hash === '#email/all') await expect(page.locator('.hubRow')).toHaveCount(3);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${hash} at ${size.width}x${size.height}`).toBeLessThanOrEqual(1);
        expect(await axe(), `${hash} at ${size.width}x${size.height}`).toEqual([]);
        if (process.env.ANUM_SHOTS) await page.screenshot({ path: `${process.env.ANUM_SHOTS}/email-${hash.slice(7)}-${locale}-${size.width}x${size.height}.png` });
      }
    }
  });
}
