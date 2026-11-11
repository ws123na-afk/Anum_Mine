import { expect, test } from '@playwright/test';
import { mockBusinessAccounts } from './support/businessAccountsApi';

// Business, the Accounts tab (docs/business.md#accounts-and-contacts) against a mocked API: an account is created,
// edited, marked do not contact and deleted; its contacts are added, edited, marked and deleted; the list sorts on
// the server and exports a CSV; a kept account's refusal and a member's limits show the server's own words.

test('an owner creates, edits, marks and deletes an account and its contacts, sorts the list and exports it', async ({ page }) => {
  const mock = await mockBusinessAccounts(page);
  await page.goto('/#business-accounts');
  await expect(page.getByRole('heading', { name: 'No client accounts yet' })).toBeVisible();
  await expect(page.getByText('Add the first one here, or add a lead in Leads')).toBeVisible();
  await page.getByRole('button', { name: 'New account' }).first().click();
  const form = page.getByRole('form', { name: 'New account' });
  await form.getByLabel('Company name').fill('   ');
  await form.getByRole('button', { name: 'Add' }).click();
  await expect(form.getByRole('alert')).toContainText('Enter the company name.');
  await form.getByLabel('Company name').fill('Acme Trading');
  await form.getByLabel('Website').fill('acme.example');
  await form.getByLabel('City').fill('Riyadh');
  await form.getByLabel('Country').fill('SA');
  await form.getByRole('button', { name: 'Add' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Acme Trading was added.' })).toBeVisible();
  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(/#business-accounts\/bacct_1$/);
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === '/accounts')?.body).toMatchObject({ name: 'Acme Trading', website: 'acme.example', city: 'Riyadh', country: 'SA' });

  // Edit the account.
  await drawer.getByRole('button', { name: 'Edit account' }).click();
  const edit = page.getByRole('form', { name: 'Edit account' });
  await expect(edit.getByLabel('Company name')).toHaveValue('Acme Trading');
  await edit.getByLabel('Industry').fill('Retail');
  await edit.getByRole('button', { name: 'Save' }).click();
  await expect(drawer.getByText('Account saved.')).toBeVisible();
  await expect(drawer.getByText('Retail', { exact: true })).toBeVisible();

  // Contacts: add, edit, do not contact with a reason, clear it (owner), delete.
  await expect(drawer.getByText('This account has no contacts yet.')).toBeVisible();
  await drawer.getByRole('button', { name: 'Add contact' }).click();
  const contactForm = page.getByRole('form', { name: 'Add contact' });
  await contactForm.getByLabel('Name', { exact: true }).fill('Sara Khan');
  await contactForm.getByLabel('E-mail').fill('sara@acme.example');
  await contactForm.getByLabel('Consent basis').selectOption('inbound_inquiry');
  await contactForm.getByRole('button', { name: 'Add' }).click();
  await expect(drawer.getByText('Sara Khan was added.')).toBeVisible();
  const sara = drawer.locator('li', { hasText: 'Sara Khan' });
  await expect(sara).toContainText('Asked us first');
  await sara.getByRole('button', { name: 'Edit contact' }).click();
  const editContact = page.getByRole('form', { name: 'Edit contact' });
  await expect(editContact.getByLabel('E-mail')).toHaveValue('sara@acme.example');
  await editContact.getByLabel('Role or title').fill('Finance');
  await editContact.getByRole('button', { name: 'Save' }).click();
  await expect(drawer.getByText('Contact saved.')).toBeVisible();
  await expect(sara).toContainText('Finance');
  await sara.getByRole('button', { name: 'Do not contact' }).click();
  const reason = page.getByRole('form', { name: 'Do not contact' });
  await reason.getByLabel('Say why (the reason is kept with the account).').fill('Asked us to stop');
  await reason.getByRole('button', { name: 'Do not contact' }).click();
  await expect(drawer.getByText(/Sara Khan is marked do not contact/)).toBeVisible();
  await expect(sara.getByText('Do not contact', { exact: true })).toBeVisible();
  await expect(sara).toContainText('Asked us to stop');
  expect(mock.calls.find((call) => /^\/contacts\/bcont_\d+\/do-not-contact$/.test(call.path))?.body).toEqual({ on: true, reason: 'Asked us to stop' });
  await sara.getByRole('button', { name: 'Clear do not contact' }).click();
  await page.getByRole('form', { name: 'Clear do not contact' }).getByLabel(/Say why/).fill('Asked again to hear from us');
  await page.getByRole('form', { name: 'Clear do not contact' }).getByRole('button', { name: 'Clear do not contact' }).click();
  await expect(drawer.getByText(/The do-not-contact mark on Sara Khan was cleared/)).toBeVisible();
  await sara.getByRole('button', { name: 'Delete contact' }).click();
  const confirmContact = page.getByRole('alertdialog', { name: 'Delete contact' });
  await expect(confirmContact).toContainText('Delete Sara Khan?');
  await confirmContact.getByRole('button', { name: 'Delete' }).click();
  await expect(drawer.getByText('Contact deleted.')).toBeVisible();
  await expect(drawer.getByText('This account has no contacts yet.')).toBeVisible();

  // The account itself: do not contact with its reason.
  await drawer.getByRole('group', { name: 'Actions' }).getByRole('button', { name: 'Do not contact' }).click();
  await page.getByRole('form', { name: 'Do not contact' }).getByLabel(/Say why/).fill('Company closed');
  await page.getByRole('form', { name: 'Do not contact' }).getByRole('button', { name: 'Do not contact' }).click();
  await expect(drawer.getByText('Do not contact: Company closed')).toBeVisible();
  await drawer.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('row', { name: /Acme Trading/ })).toContainText('Do not contact');

  // A second account, then the list sorts on the server and exports as a file.
  await page.getByRole('button', { name: 'New account' }).first().click();
  await page.getByRole('form', { name: 'New account' }).getByLabel('Company name').fill('Zed Logistics');
  await page.getByRole('form', { name: 'New account' }).getByLabel('City').fill('Dubai');
  await page.getByRole('form', { name: 'New account' }).getByRole('button', { name: 'Add' }).click();
  await page.getByRole('dialog', { name: 'Zed Logistics' }).getByRole('button', { name: 'Close' }).click();
  await expect(page.getByText('2 accounts. Sorted by Account, ascending')).toBeVisible();
  const rows = page.getByRole('table', { name: 'Client accounts' }).locator('tbody tr');
  await expect(rows.first()).toContainText('Acme Trading');
  // Wide screens sort from the column header; below 1024 px the Place column is hidden and
  // the toolbar's Sort by control does it.
  const narrow = (page.viewportSize()?.width ?? 1440) < 1024;
  if (narrow) await page.getByLabel('Sort by', { exact: true }).selectOption('place');
  else await page.getByRole('button', { name: 'Sort by Place' }).click();
  await expect(page.getByText('Sorted by Place, ascending')).toBeVisible();
  await expect(rows.first()).toContainText('Zed Logistics');
  if (!narrow) await expect(page.getByRole('columnheader', { name: /Place/ })).toHaveAttribute('aria-sort', 'ascending');
  if (narrow) await page.getByRole('button', { name: 'Reverse order' }).click();
  else await page.getByRole('button', { name: 'Sort by Place' }).click();
  await expect(page.getByText('Sorted by Place, descending')).toBeVisible();
  await expect(rows.first()).toContainText('Acme Trading');
  if (!narrow) await expect(page.getByRole('columnheader', { name: /Place/ })).toHaveAttribute('aria-sort', 'descending');
  expect(mock.calls.some((call) => call.path === '/accounts' && call.query?.includes('sort=place') && call.query?.includes('desc=true'))).toBe(true);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toBe('anum-accounts.csv');
  await expect(page.getByText('Saved anum-accounts.csv.')).toBeVisible();
  expect(mock.calls.some((call) => call.path === '/accounts/export' && call.query?.includes('sort=place') && call.query?.includes('desc=true'))).toBe(true);

  // Delete the second account: a confirmation first, then the list and the URL go back.
  await page.getByRole('button', { name: 'Zed Logistics' }).click();
  await page.getByRole('dialog', { name: 'Zed Logistics' }).getByRole('button', { name: 'Delete account' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete account' });
  await expect(confirm).toContainText('Delete Zed Logistics?');
  await confirm.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Zed Logistics was deleted.')).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Zed Logistics' })).toHaveCount(0);
  await expect(page).toHaveURL(/#business-accounts$/);
  await expect(page.getByText('1 account.')).toBeVisible();
  expect(mock.calls.some((call) => call.method === 'DELETE' && /^\/accounts\/bacct_\d+$/.test(call.path))).toBe(true);
});

test('an account with records is kept, and a second website domain is refused, in the server\'s words', async ({ page }) => {
  await mockBusinessAccounts(page, { accounts: [{ name: 'Acme Trading', city: 'Riyadh', website: 'https://acme.example', inUse: 'invoices', contacts: [{ name: 'Sara Khan', email: 'sara@acme.example' }] }] });
  await page.goto('/#business-accounts/bacct_1');
  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  await drawer.getByRole('button', { name: 'Delete account' }).click();
  await page.getByRole('alertdialog', { name: 'Delete account' }).getByRole('button', { name: 'Delete' }).click();
  await expect(drawer.getByRole('alert')).toContainText('This account has invoices, which are kept as records.');
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: 'Add contact' }).click();
  const contactForm = page.getByRole('form', { name: 'Add contact' });
  await contactForm.getByLabel('Name', { exact: true }).fill('Sara Again');
  await contactForm.getByLabel('E-mail').fill('sara@acme.example');
  await contactForm.getByRole('button', { name: 'Add' }).click();
  await expect(contactForm.getByRole('alert')).toContainText('A contact with this e-mail address already exists.');
  await contactForm.getByRole('button', { name: 'Cancel' }).click();
  await drawer.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: 'New account' }).first().click();
  const form = page.getByRole('form', { name: 'New account' });
  await form.getByLabel('Company name').fill('Acme Two');
  await form.getByLabel('Website').fill('https://acme.example/about');
  await form.getByRole('button', { name: 'Add' }).click();
  await expect(form.getByRole('alert')).toContainText('An account with the website acme.example already exists.');
});

test('a member adds and edits but cannot delete or clear a mark, and a failed list offers a retry', async ({ page }) => {
  await mockBusinessAccounts(page, { role: 'member', failListOnce: true, accounts: [{ name: 'Acme Trading', city: 'Riyadh', contacts: [{ name: 'Omar Ali', dnc: true }] }] });
  await page.goto('/#business-accounts');
  await expect(page.getByRole('alert')).toContainText('could not reach its database');
  await page.getByRole('button', { name: 'Try again' }).click();
  await page.getByRole('button', { name: 'Acme Trading' }).click();
  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  await expect(drawer.getByRole('button', { name: 'Edit account' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Delete account' })).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: 'Delete contact' })).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: 'Clear do not contact' })).toHaveCount(0);
  await expect(drawer.getByText('Only workspace owners delete accounts and contacts or clear a do-not-contact mark.')).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Add contact' })).toBeVisible();
  for (const size of [{ width: 1366, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `no horizontal overflow at ${size.width}px`).toBeLessThanOrEqual(1);
  }
});

test('the Accounts tab works in Arabic, right to left', async ({ page }) => {
  await mockBusinessAccounts(page, { accounts: [{ name: 'شركة النور', city: 'الرياض' }] });
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#business-accounts');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('button', { name: 'حساب جديد' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'تصدير CSV' })).toBeVisible();
  await page.getByRole('button', { name: 'شركة النور' }).click();
  const drawer = page.getByRole('dialog', { name: 'شركة النور' });
  await expect(drawer.getByRole('button', { name: 'تعديل الحساب' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'حذف الحساب' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'إضافة جهة اتصال' })).toBeVisible();
});
