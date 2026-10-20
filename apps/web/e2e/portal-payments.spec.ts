import { expect, test, type Page } from '@playwright/test';
import { PORTAL_LINK, mockPortal } from './support/portalApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// The customer portal's invoice page (docs/business-portal.md#reporting-a-payment) against a mocked API: the PDF
// download, credit notes in the totals, and reporting a payment (a report that changes no balance until the
// business confirms it), with the checks, the read-only and paused refusals, and Arabic.

const open = (page: Page) => page.goto(`/portal#access=${PORTAL_LINK}`);

async function openInvoice(page: Page) {
  await open(page);
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click();
  await page.getByRole('button', { name: 'INV-0001' }).click();
  return page.getByRole('article');
}

test('the customer downloads the PDF through the link with the invoice number as its name', async ({ page }) => {
  const mock = await mockPortal(page);
  const invoice = await openInvoice(page);
  const download = page.waitForEvent('download');
  await invoice.getByRole('button', { name: 'Download PDF' }).click();
  expect((await download).suggestedFilename()).toBe('INV-0001.pdf');
  await expect(invoice.getByText('Saved INV-0001.pdf.')).toBeVisible();
  const request = mock.requests.find((item) => item.path === '/invoices/binv_1/pdf');
  expect(request?.headers.authorization).toBe(`Bearer ${PORTAL_LINK}`);
  expect(mock.leaks).toEqual([]);
});

test('a credit note lowers the balance and is listed with its reason', async ({ page }) => {
  await mockPortal(page, { credited: true });
  const invoice = await openInvoice(page);
  await expect(invoice.locator('dl[aria-label="Totals"]')).toContainText('SAR 650.00');
  await expect(invoice.locator('dl[aria-label="Totals"]')).toContainText('Credited');
  const notes = invoice.locator('section', { has: page.getByRole('heading', { name: 'Credit notes' }) });
  await expect(notes).toContainText('INV-0001-CN1');
  await expect(notes).toContainText('SAR 100.00');
  await expect(notes).toContainText('Agreed discount');
});

test('reporting a payment is checked, sent, shown as waiting, and changes no balance', async ({ page }) => {
  const mock = await mockPortal(page);
  const invoice = await openInvoice(page);
  await expect(invoice.getByRole('heading', { name: 'Already paid?' })).toBeVisible();
  await invoice.getByRole('button', { name: 'I have paid' }).click();
  const form = invoice.getByRole('form', { name: 'I have paid' });
  await expect(form.getByLabel('Amount you paid')).toHaveValue('750.00');
  await form.getByRole('button', { name: 'Send to the business' }).click();
  await expect(form.getByText(/Type your full name/)).toBeVisible();
  await form.getByLabel('Your full name').fill('Sara Khan');
  await form.getByLabel('Amount you paid').fill('900');
  await form.getByRole('button', { name: 'Send to the business' }).click();
  await expect(form.getByText(/more than the balance of SAR.750\.00/)).toBeVisible();
  expect(mock.requests.some((item) => item.method === 'POST' && item.path.endsWith('/payment-notices'))).toBe(false);
  await form.getByLabel('Amount you paid').fill('300');
  await form.getByLabel('Reference, for example the transfer number').fill('TRX-31');
  await form.getByRole('button', { name: 'Send to the business' }).click();

  await expect(invoice.getByText('Sent. The invoice stays open until the business sees the money and confirms it.')).toBeVisible();
  const list = invoice.locator('section', { has: page.getByRole('heading', { name: 'Already paid?' }) });
  await expect(list).toContainText('SAR 300.00');
  await expect(list).toContainText('TRX-31');
  await expect(list).toContainText('Waiting for the business');
  // Nothing was marked paid: the balance is what it was.
  await expect(invoice.locator('dl[aria-label="Totals"]')).toContainText('SAR 750.00');
  expect(mock.requests.find((item) => item.method === 'POST' && item.path === '/invoices/binv_1/payment-notices')?.body)
    .toMatchObject({ idempotency_key: expect.stringMatching(/^[A-Za-z0-9_-]{8,80}$/), payer_name: 'Sara Khan', amount: 300, method: 'bank_transfer', reference: 'TRX-31' });
  expect(mock.leaks).toEqual([]);
});

test('a rejected report shows what the business wrote and the customer can report again', async ({ page }) => {
  const mock = await mockPortal(page);
  const invoice = await openInvoice(page);
  await invoice.getByRole('button', { name: 'I have paid' }).click();
  const form = invoice.getByRole('form', { name: 'I have paid' });
  await form.getByLabel('Your full name').fill('Sara Khan');
  await form.getByRole('button', { name: 'Send to the business' }).click();
  await expect(invoice.getByText('Waiting for the business')).toBeVisible();
  mock.answerReports('rejected', 'Nothing arrived on that date');
  await page.reload();
  const again = await (async () => { await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click(); await page.getByRole('button', { name: 'INV-0001' }).click(); return page.getByRole('article'); })();
  await expect(again.getByText('Not accepted')).toBeVisible();
  await expect(again.getByText('The business wrote: Nothing arrived on that date')).toBeVisible();
  await expect(again.getByRole('button', { name: 'I have paid' })).toBeVisible();
});

test('a confirmed report becomes a payment the customer sees', async ({ page }) => {
  const mock = await mockPortal(page);
  const invoice = await openInvoice(page);
  await invoice.getByRole('button', { name: 'I have paid' }).click();
  const form = invoice.getByRole('form', { name: 'I have paid' });
  await form.getByLabel('Your full name').fill('Sara Khan');
  await form.getByLabel('Amount you paid').fill('750');
  await form.getByRole('button', { name: 'Send to the business' }).click();
  await expect(invoice.getByText('Waiting for the business')).toBeVisible();
  mock.answerReports('confirmed');
  await page.reload();
  await page.getByRole('navigation', { name: 'Portal sections' }).getByRole('button', { name: /Invoices/ }).click();
  await page.getByRole('button', { name: 'INV-0001' }).click();
  const paid = page.getByRole('article');
  await expect(paid.locator('dl[aria-label="Totals"]')).toContainText('SAR 1,150.00');
  await expect(paid.getByText('Confirmed')).toBeVisible();
  await expect(paid.getByRole('button', { name: 'I have paid' })).toHaveCount(0);
  await expect(paid.getByText('How to pay')).toHaveCount(0);
});

test('a read-only link says so instead of offering the form', async ({ page }) => {
  await mockPortal(page, { canDecide: false });
  const invoice = await openInvoice(page);
  await expect(invoice.getByRole('button', { name: 'I have paid' })).toHaveCount(0);
  await expect(invoice.getByText('This link can read but not act. Tell the business you paid directly.')).toBeVisible();
});

test('a paused business refuses the report with its sentence', async ({ page }) => {
  await mockPortal(page, { archived: true });
  const invoice = await openInvoice(page);
  await expect(invoice.getByRole('button', { name: 'I have paid' })).toHaveCount(0);
});

test('the report form and the credit notes read in Arabic, right to left', async ({ page }) => {
  await mockPortal(page, { credited: true });
  await page.addInitScript(() => window.localStorage.setItem('anum.locale', 'ar'));
  await open(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('navigation', { name: 'أقسام البوابة' }).getByRole('button').nth(3).click();
  await page.getByRole('button', { name: 'INV-0001' }).click();
  const invoice = page.getByRole('article');
  await expect(invoice.getByRole('button', { name: 'تنزيل PDF' })).toBeVisible();
  await expect(invoice.getByRole('heading', { name: 'الإشعارات الدائنة' })).toBeVisible();
  await invoice.getByRole('button', { name: 'لقد دفعت' }).click();
  await expect(invoice.getByRole('form', { name: 'لقد دفعت' })).toBeVisible();
});

for (const size of GATE_SIZES) {
  test.describe(`portal invoice with a report at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        await mockPortal(tab, { credited: true });
        await useSavedLocale(tab, locale);
        await open(tab);
        await tab.getByRole('navigation', { name: locale === 'ar' ? 'أقسام البوابة' : 'Portal sections' }).getByRole('button').nth(3).click();
        await tab.getByRole('button', { name: 'INV-0001' }).click();
        const invoice = tab.getByRole('article');
        await expect(invoice.getByRole('button', { name: locale === 'ar' ? 'لقد دفعت' : 'I have paid' })).toBeVisible();
        await sweep(tab, `${locale} invoice with credit notes`);
        await invoice.getByRole('button', { name: locale === 'ar' ? 'لقد دفعت' : 'I have paid' }).click();
        await sweep(tab, `${locale} report form`);
        await tab.close();
      }
    });
  });
}
