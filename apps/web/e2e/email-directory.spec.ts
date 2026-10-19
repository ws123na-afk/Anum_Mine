import { expect, test, type Page } from '@playwright/test';
import { mockMailApi } from './support/mailApi';
import { mockMailDirectory, type DirectoryOptions } from './support/mailDirectoryApi';
import { mockWorkspaceApi } from './support/workspaceApi';

// Email Center phase 3 part A (docs/email.md#email-center): several mailboxes with a mailbox shared
// to read, sharing the caller's own mailbox, sender identities in the composer, the address book
// (empty, error, search, paging, add, edit, delete) and contact suggestions while typing recipients.

async function openEmail(page: Page, options: DirectoryOptions = {}) {
  await mockWorkspaceApi(page);
  const mail = await mockMailApi(page);
  const directory = await mockMailDirectory(page, options);
  await page.goto('/#email');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  await expect(page.locator('.mailRow').first()).toBeVisible();
  return { ...mail, directory: directory.calls };
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const drawer = (page: Page, name: string) => page.getByRole('dialog', { name });

test('the owner shares the mailbox to read, then to send, then stops', async ({ page }) => {
  const { directory } = await openEmail(page);
  await page.getByRole('button', { name: 'Mailbox settings' }).click();
  const settings = drawer(page, 'Mailbox settings');
  const sharing = settings.getByRole('region', { name: 'Sharing' });
  await expect(sharing).toContainText('Not shared with anyone yet.');
  await sharing.getByLabel('Person').selectOption({ label: 'nadia@example.com' });
  await sharing.getByRole('button', { name: 'Share' }).click();
  await expect(sharing).toContainText('Shared with nadia@example.com.');
  await expect(sharing).toContainText('Shared with 1 person.');
  expect(directory.grants).toEqual([{ grantee_user_id: 'member_one', access: 'read' }]);
  await sharing.getByLabel('Access for nadia@example.com').selectOption('send');
  await expect(sharing).toContainText('Changed what nadia@example.com can do.');
  expect(directory.grantPatches).toEqual([{ access: 'send' }]);
  page.once('dialog', (dialog) => void dialog.accept());
  await sharing.getByRole('button', { name: 'Stop sharing with nadia@example.com' }).click();
  await expect(sharing).toContainText('No longer shared with nadia@example.com.');
  await expect(sharing).toContainText('Not shared with anyone yet.');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a send-as address with a signature becomes the composer\'s From', async ({ page }) => {
  await openEmail(page);
  await page.getByRole('button', { name: 'Mailbox settings' }).click();
  const identities = drawer(page, 'Mailbox settings').getByRole('region', { name: 'Send as' });
  await expect(identities).toContainText('This mailbox sends as admin@example.com only.');
  await identities.getByLabel('Address').fill('sales@elsewhere.example');
  await identities.getByRole('button', { name: 'Add address' }).click();
  await expect(identities.getByRole('alert')).toContainText('Send-as addresses must be on example.com.');
  await identities.getByLabel('Address').fill('sales@example.com');
  await identities.getByLabel('Name shown to recipients (optional)').fill('Sales team');
  await identities.getByLabel('Signature (optional)').fill('Sales team\nExample Ltd');
  await identities.getByLabel('Use for new messages').check();
  await identities.getByRole('button', { name: 'Add address' }).click();
  await expect(identities).toContainText('Added sales@example.com.');
  await expect(identities.getByRole('listitem')).toContainText('Sales team <sales@example.com>');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Write', exact: true }).first().click();
  const from = page.getByRole('article', { name: 'New message' }).getByRole('combobox', { name: 'From', exact: true });
  await expect(from).toBeVisible();
  await expect(from.locator('option')).toHaveText(['Admin <admin@example.com> (mailbox address)', 'Sales team <sales@example.com>']);
});

test('the address book: empty, add, search, edit, delete', async ({ page }) => {
  const { directory } = await openEmail(page);
  await page.getByRole('button', { name: 'Contacts' }).click();
  const book = drawer(page, 'Contacts');
  await expect(book.getByRole('heading', { name: 'No contacts yet' })).toBeVisible();
  await book.getByRole('button', { name: 'Add contact' }).first().click();
  await book.getByLabel('Email address').fill('sara@example');
  await book.getByRole('button', { name: 'Save contact' }).click();
  await expect(book.getByRole('alert')).toContainText('Type a full email address');
  await book.getByLabel('Name').fill('Sara Example');
  await book.getByLabel('Email address').fill('Sara@Example.com');
  await book.getByLabel('Company').fill('Client Co');
  await book.getByRole('button', { name: 'Save contact' }).click();
  await expect(book).toContainText('Saved Sara Example <sara@example.com>.');
  await expect(book.getByRole('listitem')).toContainText('sara@example.com');
  expect(directory.contacts.at(-1)).toMatchObject({ name: 'Sara Example', email: 'Sara@Example.com', company: 'Client Co' });
  const search = book.getByRole('search', { name: 'Search contacts' });
  await search.getByRole('searchbox').fill('nobody');
  await search.getByRole('button', { name: 'Search' }).click();
  await expect(book.getByRole('heading', { name: 'No contact matches "nobody"' })).toBeVisible();
  await book.getByRole('button', { name: 'Clear search' }).click();
  await book.getByRole('button', { name: 'Edit Sara Example <sara@example.com>' }).click();
  await book.getByLabel('Notes').fill('Prefers mornings');
  await book.getByRole('button', { name: 'Save contact' }).click();
  await expect.poll(() => directory.contactPatches.length).toBe(1);
  page.once('dialog', (dialog) => void dialog.accept());
  await book.getByRole('button', { name: 'Delete Sara Example <sara@example.com>' }).click();
  await expect(book).toContainText('Deleted Sara Example <sara@example.com>.');
  await expect(book.getByRole('heading', { name: 'No contacts yet' })).toBeVisible();
});

test('a long address book pages with Show more', async ({ page }) => {
  await openEmail(page, { contacts: 30 });
  await page.getByRole('button', { name: 'Contacts' }).click();
  const book = drawer(page, 'Contacts');
  await expect(book.getByText('25 of 30')).toBeVisible();
  await book.getByRole('button', { name: 'Show more' }).click();
  await expect(book.getByText('30 of 30')).toBeVisible();
  await expect(book.getByRole('button', { name: 'Show more' })).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('when the address book cannot load, it says so and offers Try again', async ({ page }) => {
  await openEmail(page, { failContacts: true, contacts: 2 });
  await page.getByRole('button', { name: 'Contacts' }).click();
  const book = drawer(page, 'Contacts');
  await expect(book.getByRole('alert')).toContainText('Contacts could not load');
  await book.getByRole('button', { name: 'Try again' }).click();
  await expect(book.getByText('2 of 2')).toBeVisible();
});

test('typing a recipient suggests contacts; Enter picks one', async ({ page }) => {
  const { directory } = await openEmail(page, { contacts: 3 });
  await page.getByRole('button', { name: 'Write', exact: true }).first().click();
  const to = page.getByRole('article', { name: 'New message' }).getByRole('combobox', { name: 'To' });
  await to.pressSequentially('buyer0');
  const list = page.getByRole('listbox', { name: 'Suggestions from your contacts' });
  await expect(list.getByRole('option')).toHaveCount(3);
  await to.press('ArrowDown');
  await expect(list.getByRole('option').nth(1)).toHaveAttribute('aria-selected', 'true');
  await to.press('Enter');
  await expect(to).toHaveValue('buyer01@shop.example, ');
  await expect(list).toBeHidden();
  expect(directory.contactQueries.at(-1)).toContain('q=buyer0');
});

test('a contact\'s Write opens a new message to them', async ({ page }) => {
  const mail = await openEmail(page, { contacts: 1 });
  await page.getByRole('button', { name: 'Contacts' }).click();
  await drawer(page, 'Contacts').getByRole('button', { name: 'Write to Buyer 00 <buyer00@shop.example>' }).click();
  const composer = page.getByRole('article', { name: 'New message' });
  await expect(composer.getByRole('combobox', { name: 'To' })).toHaveValue('buyer00@shop.example');
  expect(mail.calls.patches.at(-1)).toMatchObject({ to: ['buyer00@shop.example'] });
});

test('a mailbox shared to read can be opened, changes nothing, and can be left', async ({ page, isMobile }) => {
  const { directory } = await openEmail(page, { shared: 'read' });
  const picker = page.getByRole('combobox', { name: 'Mailbox' });
  await expect(picker.locator('option')).toHaveText(['All inboxes', 'admin@example.com', 'team@example.com (shared with you)']);
  await picker.selectOption('mailbox_shared');
  await expect(page.getByRole('note')).toContainText('Shared with you to read by owner_two.');
  const list = page.getByRole('region', { name: 'Inbox' });
  await expect(list.locator('.mailRow').first()).toContainText('Team rota for next week');
  await expect(list.getByRole('checkbox')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Write', exact: true })).toHaveCount(0);
  if (!isMobile) expect(await noHorizontalOverflow(page)).toBe(true);
  await page.getByRole('button', { name: 'Mailbox settings' }).click();
  const settings = drawer(page, 'Mailbox settings');
  await expect(settings).toContainText('Only the owner of this mailbox can change its password');
  await expect(settings.getByRole('region', { name: 'Sharing' })).toHaveCount(0);
  page.once('dialog', (dialog) => void dialog.accept());
  await settings.getByRole('button', { name: 'Leave this mailbox' }).click();
  await expect(page.getByText('You left team@example.com.')).toBeVisible();
  expect(directory.grantDeletes).toEqual(['mailshare_mine']);
  await expect(page.getByRole('combobox', { name: 'Mailbox' })).toHaveCount(0);
});
