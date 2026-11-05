import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { useSavedLocale } from './support/workspaceApi';

// Business books (docs/business-books.md#web): the P&L with tax apart, figures not converted for want of a rate and the
// rates used; a bill recorded, paid by hand and with a member's allocation (which becomes the bill's payment, never a
// second cost); a member who cannot void, adjust or set rates; the first setup; a failed report and Try again; allocations
// classified, a budget and an intercompany entry; Arabic right to left; and the responsive gate at five sizes in both languages.
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

test('the P&L keeps tax apart, lists what is not converted and the rates used, and exports CSV', async ({ page }) => {
  await mockBooks(page);
  await page.goto('/#business-books');
  await expect(page.getByRole('heading', { name: 'Business', level: 1 })).toBeVisible();
  await expect(page.getByText(/Nothing here records money twice/)).toBeVisible();
  const pnl = page.locator('.bkStatement');
  await expect(pnl.getByRole('row', { name: /Credit notes/ }).locator('td.bkNeg')).toContainText('500.00');
  await expect(pnl.getByRole('row', { name: /^Gross profit/ })).toContainText('15,400.00');
  await expect(pnl.getByRole('row', { name: /^Net profit/ })).toContainText('5,720.00');
  await expect(page.getByText(/Not converted \(no exchange rate/)).toBeVisible();
  await expect(page.getByText(/EUR\s*90\.00/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tax (VAT)' })).toBeVisible();
  await expect(page.getByText(/not a tax return/)).toBeVisible();
  await expect(page.getByText('1 USD = 3.75 SAR')).toBeVisible();
  await expect(page.getByText(/Synthetic central bank reference \(1 figure\)/)).toBeVisible();
  await expect(page.getByText(/Paid by members from their own money/)).toBeVisible();
  await expect(page.getByText('1 allocation is waiting for review in Finance and is not counted.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'INV-2026-0007' })).toHaveAttribute('href', '#business-invoices/inv_1');
  await page.getByLabel('Basis').selectOption('cash');
  await expect(page.getByRole('heading', { name: /Profit and loss, Cash/ })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^business-pnl-.*\.csv$/);
  await noSeriousAxe(page);
});

test('a bill is recorded, part paid by hand and paid with a member allocation that is never a second cost', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books/bills');
  await expect(page.getByText('2 bills')).toBeVisible();
  await page.getByRole('button', { name: 'New bill' }).first().click();
  const form = page.getByRole('dialog', { name: 'New bill' });
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText('Choose the vendor.')).toBeVisible();
  await form.getByRole('combobox', { name: /^Vendor/ }).selectOption('bven_fuel');
  await expect(form.getByLabel('Category')).toHaveValue('fuel');
  await form.getByLabel('What it is for').fill('Diesel for the generator');
  await form.getByLabel('Amount before tax').fill('150');
  await form.getByLabel('Tax', { exact: true }).fill('0');
  await form.getByRole('button', { name: 'Save' }).click();
  const drawer = page.getByRole('dialog', { name: /Example Fuel Station/ });
  await expect(drawer.getByText('No payment recorded.')).toBeVisible();
  const created = mock.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/bills'));
  expect(created).toHaveLength(1);
  expect(created[0].key).toMatch(/^bill-/);
  await drawer.getByRole('button', { name: 'Record payment' }).click();
  const pay = page.getByRole('dialog', { name: /Record a payment/ });
  await pay.getByRole('radio', { name: /By a member, allocated/ }).check();
  await pay.getByLabel('Allocation').selectOption({ index: 1 });
  await pay.getByRole('button', { name: 'Record payment' }).click();
  await expect(page.getByText('Payment recorded.')).toBeVisible();
  await expect(drawer.getByText('#4910c0')).toBeVisible();
  const payment = mock.calls.find((c) => c.method === 'POST' && c.path.endsWith('/payments'));
  expect(payment?.body).toEqual({ kind: 'allocation', allocation_id: 'biz_alloc_fuel', note: '' });
  expect(mock.calls.some((c) => c.path.startsWith('/api/v1/finance'))).toBe(false);
  await drawer.getByRole('button', { name: 'Close' }).first().click();

  await page.getByRole('button', { name: 'Example Landlord' }).click();
  const rent = page.getByRole('dialog', { name: /Example Landlord/ });
  await expect(rent.getByText('20 days late')).toBeVisible();
  await rent.getByRole('button', { name: 'Record payment' }).click();
  const byHand = page.getByRole('dialog', { name: /Record a payment/ });
  await byHand.getByLabel('Amount (SAR)').fill('5000');
  await byHand.getByRole('button', { name: 'Record payment' }).click();
  await expect(byHand.getByText(/at most SAR/)).toBeVisible();
  await byHand.getByLabel('Amount (SAR)').fill('150');
  await byHand.getByLabel('Method').selectOption('cash');
  await byHand.getByRole('button', { name: 'Record payment' }).click();
  await expect(rent.getByText('Part paid')).toBeVisible();
  await rent.getByRole('button', { name: /^Void SAR/ }).click();
  const reason = page.getByRole('dialog', { name: 'Void this payment' });
  await reason.getByLabel(/Reason/).fill('typed twice');
  await reason.getByRole('button', { name: 'Void' }).click();
  await expect(page.getByText('Payment voided. The bill is owed again.')).toBeVisible();
  await expect(rent.getByText('Unpaid')).toBeVisible();
});

test('a member reads everything but cannot adjust, set rates, void or record intercompany entries', async ({ page }) => {
  await mockBooks(page, { role: 'member' });
  await page.goto('/#business-books/journal');
  await expect(page.getByText('Only workspace owners can change this. You can read everything.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New adjustment' })).toHaveCount(0);
  await books(page, 'Intercompany').click();
  await expect(page.getByRole('button', { name: 'New intercompany entry' })).toHaveCount(0);
  await books(page, 'Settings').click();
  await expect(page.getByLabel('Base currency')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Add rate' })).toHaveCount(0);
  await books(page, 'Bills').click();
  await page.getByRole('button', { name: 'Example Landlord' }).click();
  await expect(page.getByRole('button', { name: 'Void bill' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Record payment' })).toBeVisible();
});

test('first setup, an empty period, a failed report and Try again', async ({ page }) => {
  await mockBooks(page, { setUp: false, empty: true });
  await page.goto('/#business-books');
  await expect(page.getByRole('heading', { name: 'Set up the books first' })).toBeVisible();
  await page.getByRole('button', { name: 'Open settings' }).click();
  await page.getByLabel('Base currency').selectOption('AED');
  await page.getByLabel('Financial year starts in').selectOption('4');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Settings saved.')).toBeVisible();
  await books(page, 'Profit and loss').click();
  await expect(page.getByRole('heading', { name: 'No money in this period' })).toBeVisible();
  await page.getByRole('button', { name: 'Add a bill' }).click();
  await expect(page.getByRole('heading', { name: 'No bills yet' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { failReport: 1 });
  await page.goto('about:blank');
  await page.goto('/#business-books');
  await expect(page.getByText(/The books could not be read/)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.locator('.bkStatement')).toBeVisible();
});

test('allocations are classified, a budget is set and an intercompany entry is recorded in both businesses', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books/allocations');
  await expect(page.getByText(/never their bank, card or messages/)).toBeVisible();
  await expect(page.locator('.bzTable').getByText('Waiting for review')).toBeVisible();
  await page.getByRole('button', { name: 'Fuel' }).click();
  const tag = page.getByRole('dialog', { name: 'How this allocation counts' });
  await tag.getByLabel('Counts as').selectOption('excluded');
  await tag.getByLabel('Cost centre').selectOption('bcc_ops');
  await tag.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.bzTable').getByText('Not counted')).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'PUT')?.body).toEqual({ section: 'excluded', cost_centre_id: 'bcc_ops', note: '', version: null });

  await books(page, 'Budgets').click();
  await page.getByRole('button', { name: 'New budget' }).click();
  const budget = page.getByRole('dialog', { name: 'New budget' });
  await budget.getByLabel('Name').fill('Fuel');
  await budget.getByLabel('Amount (SAR)').fill('500');
  await budget.getByLabel('Category').fill('fuel');
  await budget.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('button', { name: 'Fuel', exact: true })).toBeVisible();

  await books(page, 'Intercompany').click();
  await page.getByRole('button', { name: 'New intercompany entry' }).first().click();
  const entry = page.getByRole('dialog', { name: 'New intercompany entry' });
  await entry.getByRole('combobox', { name: /^Other business/ }).selectOption('ws_tyre');
  await entry.getByLabel('Amount', { exact: true }).fill('2000');
  await entry.getByLabel(/What it is for/).fill('Shared office recharge');
  await entry.getByRole('button', { name: 'Record in both businesses' }).click();
  await expect(page.getByText('Shared office recharge')).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'POST' && c.path.endsWith('/intercompany'))?.body).toMatchObject({ workspace_id: 'ws_tyre', kind: 'recharge', direction: 'receivable', amount: '2000' });
});

test('Arabic reads right to left with the same rules', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockBooks(page);
  await page.goto('/#business-books');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('navigation', { name: 'أقسام الدفاتر' })).toBeVisible();
  await expect(page.getByText(/لا يُسجَّل أي مبلغ مرتين/)).toBeVisible();
  await expect(page.locator('.bkStatement').getByRole('row', { name: /صافي الربح/ })).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: the P&L, bills with a bill open, allocations and settings at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(240_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#business-books');
      await page.reload();
      await expect(page.locator('.bkStatement')).toBeVisible();
      await noOverflow(page, `${where} profit and loss`);
      await page.goto('/#business-books/bills');
      await expect(page.locator('.bzTable tbody tr').first()).toBeVisible();
      await noOverflow(page, `${where} bills`);
      await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
      await expect(page.locator('.drawerPanel .bzFacts')).toBeVisible();
      await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState !== 'running'));
      await noOverflow(page, `${where} bill`);
      await page.keyboard.press('Escape');
      await page.goto('/#business-books/allocations');
      await expect(page.locator('.bzTable tbody tr').first()).toBeVisible();
      await noOverflow(page, `${where} allocations`);
      await page.goto('/#business-books/settings');
      await expect(page.locator('#bk-rates-set')).toBeVisible();
      await noOverflow(page, `${where} settings`);
    }
  }
});
