import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { deleteHash, mockMailApi, sendHash, type MailMockOptions } from './support/mailApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Email Center (docs/email.md#email-center): folders with unread counts, search and filters, flags
// and moves on a selection, delete for good only through an approval with the payload hash, the
// reader in a sandboxed frame, and every page state (off, denied, offline, empty, no match).

async function openEmail(page: Page, options: MailMockOptions = {}) {
  await mockWorkspaceApi(page);
  const mock = await mockMailApi(page, options);
  await page.goto('/#email');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  return mock;
}

/** Folders are a list beside the messages on desktop and a select on phones. */
async function pickFolder(page: Page, name: string, isMobile: boolean) {
  if (isMobile) await page.getByRole('combobox', { name: 'Folder' }).selectOption(name);
  else await page.getByRole('navigation', { name: 'Folders' }).getByRole('button', { name: new RegExp(`^${name}`) }).click();
}
const navBadge = (page: Page) => page.locator('.sidebar .navItem', { hasText: 'Email' }).locator('.navCount');

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('folders show unread counts and the sidebar shows the inbox count', async ({ page, isMobile }) => {
  await openEmail(page);
  const list = page.getByRole('region', { name: 'Inbox' });
  await expect(list.locator('.mailRow').first()).toContainText('Tyre order for next week');
  if (!isMobile) await expect(navBadge(page)).toHaveText('10');
  await expect(list.getByText('25 of 30')).toBeVisible();
  await list.getByRole('button', { name: 'Load more' }).click();
  await expect(list.locator('.mailRow')).toHaveCount(30);
  await expect(list.getByRole('button', { name: 'Load more' })).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('search and the Starred filter go to the mail server', async ({ page }) => {
  const { calls } = await openEmail(page);
  const search = page.getByRole('search', { name: 'Search this folder' });
  await search.getByRole('searchbox').fill('  invoice  ');
  await search.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('.mailRow')).toHaveCount(1);
  await expect(page.getByText('1 message matches.')).toBeVisible();
  expect(calls.listQueries.at(-1)).toContain('q=invoice');
  await search.getByRole('searchbox').fill('nothing here');
  await search.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Nothing in Inbox matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search and filters' }).first().click();
  await search.getByText('Starred').click();
  await expect(page.locator('.mailRow')).toHaveCount(1);
  expect(calls.listQueries.at(-1)).toContain('flagged=true');
});

test('a selection is starred, read and archived; counts move at once', async ({ page, isMobile }) => {
  const { calls } = await openEmail(page);
  const rows = page.locator('.mailRow');
  await rows.nth(0).getByRole('checkbox').check();
  await rows.nth(3).getByRole('checkbox').click({ modifiers: ['Shift'] });
  const bar = page.getByRole('toolbar', { name: 'Actions for the selected messages' });
  await expect(bar).toContainText('4 selected');
  await bar.getByRole('button', { name: 'Star' }).click();
  await expect(page.getByText('Starred 4 messages.')).toBeVisible();
  expect(calls.flags.at(-1)).toMatchObject({ folder: 'INBOX', flag: 'flagged', value: true });
  await bar.getByRole('button', { name: 'Mark read' }).click();
  if (!isMobile) await expect(navBadge(page)).toHaveText('8');
  await bar.getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByText('Archived 4 messages.')).toBeVisible();
  expect(calls.moves.at(-1)).toMatchObject({ folder: 'INBOX', to: 'archive' });
  expect(calls.moves.at(-1)?.uids).toHaveLength(4);
  await expect(rows).toHaveCount(21);
});

test('a failed move says what failed and keeps the messages', async ({ page }) => {
  await openEmail(page, { failNextAction: 503 });
  const first = page.locator('.mailRow').first();
  await first.getByRole('checkbox').check();
  await page.getByRole('toolbar', { name: 'Actions for the selected messages' }).getByRole('button', { name: 'Trash' }).click();
  await expect(page.getByRole('alert')).toContainText('The mail server cannot be reached right now');
  await expect(page.locator('.mailRow')).toHaveCount(25);
});

test('delete for good waits for the owner to approve with the payload hash', async ({ page, isMobile }) => {
  const { calls } = await openEmail(page);
  await pickFolder(page, 'Trash', isMobile);
  const list = page.getByRole('region', { name: 'Trash' });
  await expect(list.locator('.mailRow')).toHaveCount(1);
  await list.locator('.mailRow').first().getByRole('checkbox').check();
  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('toolbar', { name: 'Actions for the selected messages' }).getByRole('button', { name: 'Delete for good' }).click();
  const card = page.getByRole('group', { name: 'Delete for good needs approval' });
  await expect(card).toContainText('1 message in Trash will be deleted from the mail server');
  await expect(card.locator('code')).toHaveAttribute('title', deleteHash);
  expect(calls.deletes).toEqual([{ folder: 'Trash', uids: [7] }]);
  await card.getByRole('button', { name: 'Approve and delete' }).click();
  await expect(page.getByText('Deleted for good.')).toBeVisible();
  expect(calls.approvals).toEqual([{ payload_hash: deleteHash }]);
  await expect(page.getByRole('heading', { name: 'No messages in Trash.' })).toBeVisible();
});

test('members ask to delete for good but cannot approve', async ({ page, isMobile }) => {
  await openEmail(page, { role: 'member' });
  await pickFolder(page, 'Junk', isMobile);
  await page.getByRole('region', { name: 'Junk' }).locator('.mailRow').first().getByRole('checkbox').check();
  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('toolbar', { name: 'Actions for the selected messages' }).getByRole('button', { name: 'Delete for good' }).click();
  const card = page.getByRole('group', { name: 'Delete for good needs approval' });
  await expect(card).toContainText('An owner of this workspace approves deletes.');
  await expect(card.getByRole('button', { name: 'Approve and delete' })).toHaveCount(0);
});

test('the reader opens a message and marks it read; Back returns to the list', async ({ page, isMobile }) => {
  await openEmail(page);
  await page.locator('.mailRowOpen').first().click();
  const reader = page.getByRole('article');
  await expect(reader.getByRole('heading', { name: 'Tyre order for next week' })).toBeFocused();
  await expect(reader).toContainText('Message body number 30.');
  if (!isMobile) await expect(navBadge(page)).toHaveText('9');
  await expect(reader.getByRole('button', { name: 'Reply', exact: true })).toBeVisible();
  if (isMobile) {
    await reader.getByRole('button', { name: 'Back to the list' }).click();
    await expect(page.locator('.mailRow').first()).toBeVisible();
  }
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('list shortcuts star and archive the focused message', async ({ page, isMobile }) => {
  test.skip(isMobile, 'single-key shortcuts are for keyboards; phones use the row buttons');
  const { calls } = await openEmail(page);
  await page.locator('.mailRowOpen').nth(1).focus();
  await page.keyboard.press('s');
  await expect.poll(() => calls.flags.at(-1)).toMatchObject({ flag: 'flagged', value: false });
  await page.keyboard.press('e');
  await expect.poll(() => calls.moves.length).toBe(1);
  expect(calls.moves[0]).toMatchObject({ to: 'archive' });
});

test('the page says when the Email Center is turned off', async ({ page }) => {
  await openEmail(page, { enabled: false });
  await expect(page.getByRole('heading', { name: 'The Email Center is turned off' })).toBeVisible();
  await expect(page.getByText('ANUM_MAILSERVER_ENABLED=true')).toBeVisible();
});

test('viewers are told who can read mail', async ({ page }) => {
  await openEmail(page, { role: 'viewer' });
  await expect(page.getByRole('heading', { name: 'Email is for owners and members' })).toBeVisible();
});

test('an offline mail server says so and offers Try again', async ({ page }) => {
  await openEmail(page, { offline: true });
  await expect(page.getByRole('alert').first()).toContainText('The mail server cannot be reached right now');
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
});

test('with no mailbox the page offers the connect form', async ({ page }) => {
  await openEmail(page, { noAccount: true });
  await expect(page.getByRole('heading', { name: 'Connect your mailbox' })).toBeVisible();
  await expect(page.getByLabel('App password')).toHaveAttribute('type', 'password');
  await expect(page.getByText('example.com')).toBeVisible();
});

test('a reply is written as a draft and sent only after the owner approves the payload hash', async ({ page }) => {
  const { calls } = await openEmail(page);
  await page.locator('.mailRowOpen').first().click();
  await page.getByRole('article').getByRole('button', { name: 'Reply', exact: true }).click();
  const composer = page.getByRole('article', { name: 'Reply' });
  await expect(composer.getByLabel('To')).toHaveValue('sara@example.com');
  await expect(composer.getByLabel('Subject')).toHaveValue('Re: Tyre order for next week');
  await composer.getByLabel('To').fill('sara@example.com, not an address');
  await composer.getByRole('button', { name: 'Send' }).click();
  await expect(composer.getByRole('alert')).toContainText('"not an address" is not an email address.');
  await composer.getByLabel('To').fill('sara@example.com');
  await composer.getByLabel('Message').fill('Thanks, the order is confirmed.');
  await composer.getByRole('button', { name: 'Send' }).click();
  const approval = composer.getByRole('group', { name: 'Approve sending' });
  await expect(approval.locator('code')).toHaveAttribute('title', sendHash);
  expect(calls.patches.at(-1)).toMatchObject({ to: ['sara@example.com'], body: 'Thanks, the order is confirmed.' });
  expect((calls.sends[0] as { idempotency_key: string }).idempotency_key).toMatch(/^send-/);
  await approval.getByRole('button', { name: 'Approve and send' }).click();
  await expect(composer.getByText('Sent to sara@example.com.')).toBeVisible();
  expect(calls.approvals).toEqual([{ payload_hash: sendHash }]);
});

// Accessibility gate (docs/web-localization.md#accessibility) for the list with a selection, the
// reader and the composer, in English, Arabic and the light theme.
for (const mode of ['en', 'ar', 'light'] as const) {
  test(`Email has no serious or critical axe violations (${mode})`, async ({ page }) => {
    await mockWorkspaceApi(page);
    if (mode === 'light') await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
    else await useSavedLocale(page, mode);
    await mockMailApi(page);
    await page.goto('/#email');
    await expect(page.locator('.mailRow').first()).toBeVisible();
    await page.locator('.mailRow').first().getByRole('checkbox').check();
    const axe = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
    expect(await axe()).toEqual([]);
    await page.locator('.mailRowOpen').first().click();
    await expect(page.locator('.mailReader h2')).toBeVisible();
    expect(await axe()).toEqual([]);
    await page.locator('.mailReaderActions button').first().click();
    await expect(page.locator('.mailComposer textarea')).toBeVisible();
    expect(await axe()).toEqual([]);
  });
}
