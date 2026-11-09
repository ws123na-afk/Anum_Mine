import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { useSavedLocale } from './support/workspaceApi';

// The business journal (docs/business-books.md#the-journal): the ledger posted from the records with Post now, what is not
// posted and why, a manual entry refused while it does not balance and reversed with a reason, the general ledger of an
// account; the statements (balance sheet, P&L reconciled to the records, cash flow per currency, equity, trial balance,
// comparatives, policy notes, export, a failure and Try again); the chart of accounts; closing a month and reopening it
// with a reason; accountants; a member's read-only view; Arabic right to left; the gate at five sizes. Figures are SYNTHETIC.
async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function noOverflow(page: Page, where: string) {
  expect(await horizontalOverflow(page), `${where}: horizontal page overflow`).toBeLessThanOrEqual(0);
  expect(await clippedControls(page), `${where}: clipped controls`).toEqual([]);
}

const books = (page: Page, name: string) => page.getByRole('navigation', { name: 'Books sections' }).getByRole('button', { name, exact: true });

test('the ledger shows posted entries with their lines, posts now, and takes a balanced manual entry that is reversed with a reason', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books');
  await books(page, 'Ledger').click();
  await expect(page.getByText('Everything up to today is posted.')).toBeVisible();
  const payment = page.getByRole('listitem').filter({ hasText: 'Payment for invoice INV-2026-0007' });
  await expect(payment.getByRole('row', { name: /1000 Bank and cash/ })).toContainText('375.00');
  await expect(payment.getByText(/100\.00 at 3\.75 on .*Synthetic central bank reference/)).toBeVisible();
  await expect(payment.getByRole('row', { name: /7000 Exchange gains and losses/ })).toContainText('5.00');
  await page.getByRole('button', { name: 'Post now' }).first().click();
  await expect(page.getByText(/Posted 0, reversed 0; 0 items cannot be posted yet\./)).toBeVisible();
  expect(mock.calls.some((call) => call.method === 'POST' && call.path.endsWith('/ledger/post'))).toBe(true);

  await page.getByRole('button', { name: 'New journal entry' }).click();
  const dialog = page.getByRole('dialog', { name: 'New journal entry' });
  await dialog.getByLabel('What it is for').fill('Synthetic opening cash');
  await dialog.getByLabel('Account of line 1').selectOption({ label: '1000 Bank and cash' });
  await dialog.getByLabel('Debit').first().fill('500');
  await dialog.getByLabel('Account of line 2').selectOption({ label: '3000 Owner capital' });
  await dialog.getByLabel('Credit').nth(1).fill('450');
  await dialog.getByRole('button', { name: 'Post entry' }).click();
  await expect(dialog.getByText('Debits and credits must be equal.')).toBeVisible();
  await dialog.getByLabel('Credit').nth(1).fill('500');
  await dialog.getByRole('button', { name: 'Post entry' }).click();
  await expect(dialog).toHaveCount(0);
  const posted = mock.calls.find((call) => call.method === 'POST' && call.path.endsWith('/ledger/journal'));
  expect(posted?.key).toBeTruthy();
  const opening = page.getByRole('listitem').filter({ hasText: 'Synthetic opening cash' });
  await opening.getByRole('button', { name: 'Reverse Synthetic opening cash' }).click();
  const reason = page.getByRole('dialog', { name: 'Reverse this entry' });
  await reason.getByLabel(/Reason/).fill('typed in error');
  await reason.getByRole('button', { name: 'Reverse' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'Reversed: typed in error' })).toBeVisible();
  // Entries from a source document are changed through the document, never reversed here.
  await expect(page.getByRole('listitem').filter({ hasText: 'Invoice INV-2026-0007 issued' }).getByRole('button', { name: /^Reverse/ })).toHaveCount(0);

  await page.getByRole('button', { name: 'General ledger' }).click();
  const drawer = page.getByRole('dialog', { name: 'General ledger' });
  await drawer.getByLabel('Account').selectOption({ label: '1000 Bank and cash' });
  await expect(drawer.getByText('Closing balance')).toBeVisible();
  await expect(drawer.getByRole('row', { name: /Opening balance/ }).first()).toContainText('2,000.00');
  await noSeriousAxe(page);
});

test('an item without an exchange rate waits with its reason and the statements say they are incomplete', async ({ page }) => {
  await mockBooks(page, { ledger: 'issues' });
  await page.goto('/#business-books/ledger');
  await expect(page.getByText('Not in the journal yet: 1 changes to post and 1 items that cannot be posted.')).toBeVisible();
  await expect(page.getByText(/No exchange rate for USD on or before 2026-10-20/)).toBeVisible();
  await expect(page.getByText(/ANUM never guesses a rate/)).toBeVisible();
  await books(page, 'Statements').click();
  await expect(page.getByText(/Incomplete: 1 changes are not posted yet and 1 items cannot be posted\./)).toBeVisible();
  await expect(page.locator('#bk-bs').locator('..').getByText('Incomplete', { exact: true })).toBeVisible();
  await books(page, 'Close').click();
  await expect(page.getByText('Items that cannot be posted: 1')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close the period' })).toBeDisabled();
});

test('the statements balance, reconcile to the records, compare periods and export', async ({ page }) => {
  await mockBooks(page, { failStatements: 1 });
  await page.goto('/#business-books/statements');
  await expect(page.getByText(/The statements could not be read/)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.getByText('Assets equal liabilities and equity')).toBeVisible();
  await expect(page.locator('#bk-bs').locator('..').getByRole('row', { name: /^Total assets/ })).toContainText('3,155.00');
  await expect(page.locator('#bk-bs').locator('..').getByRole('row', { name: /^Result not yet closed/ })).toContainText('1,005.00');
  await expect(page.getByText(/The journal agrees with the P&L from the records up to/)).toBeVisible();
  await expect(page.getByRole('table', { name: 'In USD' }).getByRole('row', { name: /^Customers paid/ })).toContainText('100.00');
  await expect(page.getByText('Debits equal credits')).toBeVisible();
  await expect(page.getByText('Synthetic owner policy note.')).toBeVisible();
  await expect(page.getByText(/The functional and presentation currency is SAR/)).toBeVisible();
  await page.getByRole('checkbox', { name: 'Compare with the previous period and last year' }).check();
  await expect(page.locator('#bk-jpnl').locator('..').getByRole('columnheader', { name: /Previous period/ })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^business-statements-.*\.csv$/);
  await noSeriousAxe(page);
});

test('the chart of accounts adds an account in its range and keeps system accounts', async ({ page }) => {
  await mockBooks(page);
  await page.goto('/#business-books/accounts');
  await expect(page.getByRole('row', { name: /1000 Bank and cash/ })).toBeVisible();
  await page.getByRole('button', { name: 'New account' }).click();
  const dialog = page.getByRole('dialog', { name: 'New account' });
  await dialog.getByLabel('Kind').selectOption({ label: 'Operating expense' });
  await dialog.getByLabel('Code').fill('1200');
  await dialog.getByLabel('Account').fill('Synthetic rent');
  await dialog.getByRole('button', { name: 'Save account' }).click();
  await expect(dialog.getByText("The code's first digit does not match this kind of account.")).toBeVisible();
  await dialog.getByLabel('Code').fill('6200');
  await dialog.getByLabel(/Category posted here/).fill('office_rent');
  await dialog.getByRole('button', { name: 'Save account' }).click();
  await expect(page.getByRole('row', { name: /6200 Synthetic rent/ })).toBeVisible();
  await page.getByRole('button', { name: 'Edit account 1000' }).click();
  await expect(page.getByRole('checkbox', { name: /Archived/ })).toHaveCount(0);
  await noSeriousAxe(page);
});

test('an owner closes a month and reopens it with a reason; a member reads only', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books/close');
  await expect(page.getByText(/The books are closed through/)).toBeVisible();
  await page.getByRole('button', { name: 'Close the period' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Close the period' }).click();
  await expect(page.getByText(/^Closed through/)).toBeVisible();
  expect(mock.calls.some((call) => call.method === 'POST' && call.path.endsWith('/close'))).toBe(true);
  await page.getByRole('button', { name: /Reopen the period ending/ }).first().click();
  const reason = page.getByRole('dialog', { name: /Reopen from/ });
  await reason.getByLabel(/Reason/).fill('late supplier credit');
  await reason.getByRole('button', { name: 'Reopen' }).click();
  await expect(page.getByText('Reopened. The trail keeps who reopened it and why.')).toBeVisible();
  await expect(page.getByText(/late supplier credit/).first()).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { role: 'member' });
  await page.goto('about:blank');
  await page.goto('/#business-books/close');
  await expect(page.getByText('Only an owner or an accountant of this business can do this. You can read everything.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close the period' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Reopen/ })).toHaveCount(0);
  await books(page, 'Ledger').click();
  await expect(page.getByRole('button', { name: 'New journal entry' })).toHaveCount(0);
});

test('an owner names an accountant who may then close but not reopen', async ({ page }) => {
  await mockBooks(page);
  await page.goto('/#business-books/settings');
  const card = page.locator('section[aria-labelledby="bk-accountants"]');
  await expect(card.getByText('No accountants yet. Owners hold every accountant right.')).toBeVisible();
  await card.getByLabel('Owner or member').selectOption({ label: 'member@example.com (member_local)' });
  await card.getByRole('button', { name: 'Make accountant' }).click();
  await expect(card.getByText('member_local')).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { role: 'member', accountant: true });
  await page.goto('about:blank');
  await page.goto('/#business-books/close');
  await expect(page.getByRole('button', { name: 'Close the period' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Reopen/ })).toHaveCount(0);
  await expect(page.getByText('Only an owner can reopen a closed period.')).toBeVisible();
});

test('an empty journal offers Post now and Arabic reads right to left', async ({ page }) => {
  await mockBooks(page, { ledger: 'empty' });
  await page.goto('/#business-books/accounts');
  await expect(page.getByRole('heading', { name: 'The chart is made at the first posting' })).toBeVisible();
  await page.getByRole('button', { name: 'Post now' }).click();
  await expect(page.getByRole('row', { name: /1000 Bank and cash/ })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await useSavedLocale(page, 'ar');
  await mockBooks(page);
  await page.goto('about:blank');
  await page.goto('/#business-books/statements');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByText('الأصول تساوي الالتزامات وحقوق الملكية')).toBeVisible();
  await noSeriousAxe(page);
  await page.goto('/#business-books/ledger');
  await expect(page.getByRole('heading', { name: 'حالة الترحيل' })).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: ledger, statements, accounts and close at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(300_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#business-books/ledger');
      await page.reload();
      await expect(page.locator('.bkEntry').first()).toBeVisible();
      await noOverflow(page, `${where} ledger`);
      await page.goto('/#business-books/statements');
      await expect(page.locator('#bk-tb')).toBeVisible();
      await noOverflow(page, `${where} statements`);
      await page.goto('/#business-books/accounts');
      await expect(page.locator('.bzTable tbody tr').first()).toBeVisible();
      await noOverflow(page, `${where} accounts`);
      await page.goto('/#business-books/close');
      await expect(page.locator('#bk-close-history')).toBeVisible();
      await noOverflow(page, `${where} close`);
    }
  }
});
