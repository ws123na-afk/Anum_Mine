import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { mockContacts } from './support/contactsApi';

// The Contacts directory (docs/contacts.md#web): the people list with search, filters and paging, the
// person page with linked records, consent per channel, do-not-contact, merge and the possible
// duplicates; owners export and clear marks; viewers get the API's 403. API mocked.

async function open(page: import('@playwright/test').Page, options: Parameters<typeof mockContacts>[1] = {}, hash = 'contacts') {
  await mockWorkspaceApi(page);
  const mock = await mockContacts(page, options);
  await page.goto(`/#${hash}`);
  return mock;
}

test('members see everyone once, search, filter and open a person', async ({ page }) => {
  const mock = await open(page, { role: 'member' });
  await expect(page.getByRole('heading', { level: 1, name: 'Contacts' })).toBeVisible();
  const list = page.getByRole('region', { name: 'People' });
  await expect(list.getByRole('heading', { name: '4 people' })).toBeVisible();
  await expect(list.getByText('+971501234567').first()).toBeVisible();
  await expect(list.getByText('Do not contact: WhatsApp')).toBeVisible();
  await expect(list.getByText('1 possible duplicate').first()).toBeVisible();
  // Members write but do not export: the export note says who does.
  await expect(page.getByRole('button', { name: 'Add person' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Export CSV' })).toHaveCount(0);
  await expect(page.getByText('Only workspace owners export the directory.')).toBeVisible();
  // Search and the do-not-contact filter go to the API.
  await list.getByLabel('Search').fill('omar');
  await list.getByLabel('Do not contact').selectOption('any');
  await list.getByRole('button', { name: 'Find' }).click();
  await expect(list.getByRole('heading', { name: '1 person matches' })).toBeVisible();
  const last = mock.calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/contacts').at(-1)!;
  expect(last.query).toContain('q=omar');
  expect(last.query).toContain('dnc=any');
  await list.getByRole('button', { name: 'Clear filters' }).click();
  await expect(list.getByRole('heading', { name: '4 people' })).toBeVisible();
  // The person page: linked records with their links, the channels and the caller's own mailbox contact.
  await list.getByRole('button', { name: 'Open Sara Ahmed' }).click();
  await expect(page).toHaveURL(/#contacts\?person=ct_sara$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Sara Ahmed' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open in Phone calls' })).toHaveAttribute('href', '#calls-contacts?contact=contact_omar');
  await expect(page.getByRole('link', { name: 'Open in Business' })).toHaveAttribute('href', '#business-accounts?account=bacct_1&contact=bcont_1');
  await expect(page.getByText('This record was deleted in its module.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open in Email' })).toHaveAttribute('href', '#email/server');
  await expect(page.getByText('Phone calls: consent to AI calls recorded')).toBeVisible();
  await expect(page.getByText('WhatsApp: the customer wrote first')).toBeVisible();
  await expect(page.getByText(/Opt-out list: marketing/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Possible duplicates' })).toBeVisible();
  // A member marks do not contact with a reason, and may not clear it.
  await page.getByRole('button', { name: 'Mark do not contact' }).first().click();
  const dialog = page.getByRole('alertdialog');
  await dialog.getByLabel('Channel').selectOption('email');
  await dialog.getByRole('button', { name: 'Mark do not contact' }).click();
  await expect(dialog.getByText(/Give a reason of 3 to 300 characters/)).toBeVisible();
  await dialog.getByLabel('Reason').fill('Asked us to stop writing');
  await dialog.getByRole('button', { name: 'Mark do not contact' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Marked do not contact (E-mail).' })).toBeVisible();
  const put = mock.calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/contacts/ct_sara/do-not-contact')!;
  expect(put.body).toEqual({ channel: 'email', reason: 'Asked us to stop writing' });
  await expect(page.getByText(/Do not contact \(E-mail\): Asked us to stop writing/)).toBeVisible();
  await expect(page.getByText('Only workspace owners clear a mark.').first()).toBeVisible();
  await page.getByRole('button', { name: 'All people' }).click();
  await expect(page).toHaveURL(/#contacts$/);
});

test('owners add a person, rebuild, decide a duplicate, clear a mark, merge, delete and export', async ({ page }) => {
  const mock = await open(page);
  await page.getByRole('button', { name: 'Add person' }).click();
  const form = page.getByRole('dialog', { name: 'Add a person' });
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText(/Give the person a name/)).toBeVisible();
  await form.getByLabel('Name').fill('Noor Saleh');
  await form.getByLabel('Phone numbers').fill('050 777 1234\nnope');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText(/"nope" is not a phone number/)).toBeVisible();
  await form.getByLabel('Phone numbers').fill('050 777 1234');
  await form.getByLabel('E-mail addresses').fill('Noor@Example.com');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Noor Saleh' })).toBeVisible();
  const post = mock.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/contacts')!;
  expect(post.body).toEqual({ name: 'Noor Saleh', organisation: '', notes: '', phones: ['050 777 1234'], emails: ['Noor@Example.com'] });
  // Rebuild now reports what it linked.
  await page.getByRole('button', { name: 'Rebuild now' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Linked 1 new source records: 1 new people, 0 possible duplicates.' })).toBeVisible();
  // Possible duplicates: keep one side merges the other into it.
  await page.getByRole('button', { name: 'All people' }).click();
  await page.getByRole('tab', { name: /Possible duplicates/ }).click();
  await expect(page).toHaveURL(/#contacts-duplicates$/);
  const dupes = page.getByRole('region', { name: 'Possible duplicates' });
  await expect(dupes.getByText('Same phone number')).toBeVisible();
  await dupes.getByRole('button', { name: 'Keep Sara Ahmed' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Merged S. Ahmad into Sara Ahmed.' })).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/contacts/ct_sara/merge')!.body).toEqual({ other_id: 'ct_s_ahmad' });
  await expect(dupes.getByRole('heading', { name: 'No possible duplicates' })).toBeVisible();
  // On a person: clear a mark (owners), merge by search, export, delete.
  await page.goto('/#contacts?person=ct_omar');
  await expect(page.getByRole('heading', { level: 2, name: 'Omar Khan' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear' }).click();
  const clear = page.getByRole('alertdialog');
  await clear.getByLabel('Reason').fill('They asked for news again');
  await clear.getByRole('button', { name: 'Clear the mark' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Mark cleared (WhatsApp).' })).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'DELETE' && c.path === '/api/v1/contacts/ct_omar/do-not-contact/whatsapp')!.body).toEqual({ reason: 'They asked for news again' });
  await page.getByRole('button', { name: 'Merge with...' }).click();
  const merge = page.getByRole('dialog', { name: /Merge Omar Khan/ });
  await merge.getByLabel('Find a person').fill('huda');
  await merge.getByRole('option', { name: /Huda Ali/ }).click();
  await merge.getByRole('button', { name: 'Merge Huda Ali into Omar Khan' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Merged Huda Ali into Omar Khan.' })).toBeVisible();
  await page.getByRole('button', { name: 'Export CSV' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved contacts.csv.' })).toBeVisible();
  await page.getByRole('button', { name: 'Delete person' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Deleted Omar Khan.' })).toBeVisible();
  await expect(page).toHaveURL(/#contacts$/);
  expect(mock.calls.some((c) => c.method === 'DELETE' && c.path === '/api/v1/contacts/ct_omar')).toBe(true);
});

test('empty, paging, permission denied and error states', async ({ page }) => {
  await open(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'No people yet' })).toBeVisible();
  await expect(page.getByText(/Press Rebuild now/)).toBeVisible();
  await page.goto('/#contacts-duplicates');
  await expect(page.getByRole('heading', { name: 'No possible duplicates' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.goto('about:blank');
  await open(page, { total: 60 });
  const list = page.getByRole('region', { name: 'People' });
  await expect(list.getByRole('heading', { name: '60 people' })).toBeVisible();
  await expect(list.getByText('Showing 4 of 60')).toBeVisible();
  await list.getByRole('button', { name: 'Show more' }).click();
  await expect(list.getByText('Person 4', { exact: true })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.goto('about:blank');
  await open(page, { status: 403 });
  await expect(page.getByRole('heading', { name: 'Members and owners only' })).toBeVisible();
  await expect(page.getByText(/viewers cannot see them/).first()).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.goto('about:blank');
  await open(page, { status: 500 });
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
});

const SHEET = 'Full name,Company,Mobile,Email,Do not contact\nSara Ahmed,Tyre Co,+971501234567,,\nRana Saleh,,0507770000,,email\nR. Saleh,,+971507770000,,\nBad Number,,12,,\n';

async function chooseSheet(page: import('@playwright/test').Page, name = 'people.csv', text = SHEET) {
  await page.getByLabel('File to import (CSV, Excel or vCard)', { exact: true }).setInputFiles({ name, mimeType: 'text/csv', buffer: Buffer.from(text) });
}

test('owners import a CSV file: preview, columns, a decision per row, all or nothing', async ({ page }) => {
  const mock = await open(page, {}, 'contacts-import');
  await expect(page.getByRole('tab', { name: 'Import people', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('heading', { name: 'Import people from a file' })).toBeVisible();
  // Not a file ANUM reads (an old Excel file): said at once, nothing sent.
  await chooseSheet(page, 'people.xls');
  await expect(page.getByRole('alert').filter({ hasText: 'Choose a CSV (.csv), Excel (.xlsx) or vCard (.vcf) file.' })).toBeVisible();
  expect(mock.calls.some((c) => c.path === '/api/v1/contacts/import/preview')).toBe(false);
  await chooseSheet(page);
  const section = page.getByRole('region', { name: 'Import people from a file' });
  await expect(section.getByText('File: people.csv')).toBeVisible();
  await expect(section.getByRole('heading', { name: 'What the import would do: 4 rows' })).toBeVisible();
  const preview = mock.calls.find((c) => c.path === '/api/v1/contacts/import/preview')!;
  expect(preview.body).toEqual({ csv: SHEET, file_name: 'people.csv' });
  await expect(section.getByRole('combobox', { name: /^Name/ })).toHaveValue('Full name');
  await expect(section.getByText('Holds +971501234567: Sara Ahmed')).toBeVisible();
  await expect(section.getByText(/Sara Ahmed is marked do not contact \(Phone\); the mark stays\./)).toBeVisible();
  await expect(section.getByText('Repeats row 3.')).toBeVisible();
  await expect(section.getByText('"12" is not a phone number with its country code (+971...).')).toBeVisible();
  await expect(section.getByLabel('What to do with row 5')).toBeDisabled();
  await expect(section.getByText('Add 1, update 1, skip 2.')).toBeVisible();
  // The owner adds the repeated row too, and filters the list.
  await section.getByLabel('What to do with row 4').selectOption('create');
  await expect(section.getByText('Add 2, update 1, skip 1.')).toBeVisible();
  await section.getByLabel('Show').selectOption('invalid');
  await expect(section.locator('.ctImportRow')).toHaveCount(1);
  await section.getByLabel('Show').selectOption('');
  // Changing a column checks the file again with that mapping.
  await section.getByLabel('Organisation').selectOption('');
  await section.getByRole('button', { name: 'Check again' }).click();
  await expect.poll(() => mock.calls.filter((c) => c.path === '/api/v1/contacts/import/preview').length).toBe(2);
  expect((mock.calls.filter((c) => c.path === '/api/v1/contacts/import/preview').at(-1)!.body as { mapping: { organisation: unknown } }).mapping.organisation).toBeNull();
  // A new check resets the choices to the suggestions.
  await expect(section.getByText('Add 1, update 1, skip 2.')).toBeVisible();
  await section.getByLabel('What to do with row 4').selectOption('create');
  await section.getByRole('button', { name: 'Import', exact: true }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Import these people?' });
  await expect(confirm.getByText(/adds 2 new people and updates 1 existing people, and skips 1 rows/)).toBeVisible();
  await confirm.getByRole('button', { name: 'Import' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Imported: 2 added, 1 updated, 1 skipped, 1 do-not-contact marks added, 1 possible duplicates to decide.' })).toBeVisible();
  const applied = mock.calls.find((c) => c.path === '/api/v1/contacts/import/apply')!.body as { decisions: unknown[]; fingerprint: string };
  expect(applied.fingerprint).toBe('f'.repeat(64));
  expect(applied.decisions).toEqual([
    { row: 2, action: 'update', target_id: 'ct_sara' }, { row: 3, action: 'create', target_id: null },
    { row: 4, action: 'create', target_id: null }, { row: 5, action: 'skip', target_id: null },
  ]);
  await page.getByRole('button', { name: 'Decide possible duplicates' }).click();
  await expect(page).toHaveURL(/#contacts-duplicates$/);
});

test('an Excel workbook is sent as base64 and a vCard file as text, through the same preview and import', async ({ page }) => {
  const mock = await open(page, {}, 'contacts-import');
  const workbook = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0xff, 0x10]);
  await page.getByLabel('File to import (CSV, Excel or vCard)', { exact: true }).setInputFiles({
    name: 'people.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: workbook });
  const section = page.getByRole('region', { name: 'Import people from a file' });
  await expect(section.getByText('File: people.xlsx')).toBeVisible();
  await expect(section.getByRole('heading', { name: 'What the import would do: 4 rows' })).toBeVisible();
  expect(mock.calls.find((c) => c.path === '/api/v1/contacts/import/preview')!.body).toEqual({ format: 'xlsx', file_base64: workbook.toString('base64'), file_name: 'people.xlsx' });
  await section.getByRole('button', { name: 'Import', exact: true }).click();
  await page.getByRole('alertdialog', { name: 'Import these people?' }).getByRole('button', { name: 'Import' }).click();
  await expect(page.getByRole('status').filter({ hasText: /^Imported:/ })).toBeVisible();
  const applied = mock.calls.find((c) => c.path === '/api/v1/contacts/import/apply')!.body as Record<string, unknown>;
  expect([applied.format, applied.file_base64, applied.csv]).toEqual(['xlsx', workbook.toString('base64'), undefined]);

  await section.getByRole('button', { name: 'Choose another file' }).click();
  const cards = 'BEGIN:VCARD\nVERSION:3.0\nFN:Rana Saleh\nTEL:+971507770000\nEND:VCARD\n';
  await page.getByLabel('File to import (CSV, Excel or vCard)', { exact: true }).setInputFiles({ name: 'contacts.vcf', mimeType: 'text/vcard', buffer: Buffer.from(cards) });
  await expect(section.getByText('File: contacts.vcf')).toBeVisible();
  await expect.poll(() => mock.calls.filter((c) => c.path === '/api/v1/contacts/import/preview').length).toBe(2);
  expect(mock.calls.filter((c) => c.path === '/api/v1/contacts/import/preview').at(-1)!.body).toEqual({ format: 'vcf', csv: cards, file_name: 'contacts.vcf' });
});

test('members are told who imports, and a refused file says why', async ({ page }) => {
  await open(page, { role: 'member' }, 'contacts-import');
  await expect(page.getByText(/Only workspace owners import into the directory/)).toBeVisible();
  await expect(page.getByLabel('File to import (CSV, Excel or vCard)', { exact: true })).toHaveCount(0);
  await page.goto('/#contacts');
  await expect(page.getByRole('tab', { name: 'Import people', exact: true })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await page.goto('about:blank');
  await open(page, {}, 'contacts-import');
  await chooseSheet(page, 'empty.csv', 'Full name,Mobile\n');
  await expect(page.getByRole('alert').filter({ hasText: 'The file has a header row but no people under it.' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  test(`the import preview has no serious axe violations (${locale})`, async ({ page }) => {
    await mockWorkspaceApi(page);
    await mockContacts(page);
    await useSavedLocale(page, locale);
    await page.goto('/#contacts-import');
    await page.locator('.ctImport input[type="file"]').setInputFiles({ name: 'people.csv', mimeType: 'text/csv', buffer: Buffer.from(SHEET) });
    await expect(page.locator('.ctImportRow').first()).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
  });
}

for (const locale of ['en', 'ar'] as const) {
  test(`the list, a person and the duplicates have no serious axe violations (${locale})`, async ({ page }) => {
    await mockWorkspaceApi(page);
    await mockContacts(page);
    await useSavedLocale(page, locale);
    for (const hash of ['contacts', 'contacts?person=ct_sara', 'contacts-duplicates']) {
      await page.goto(`/#${hash}`);
      await expect(page.locator('.ctRow, .ctChannel, .ctProposal').first()).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
      expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`), hash).toEqual([]);
    }
  });
}
