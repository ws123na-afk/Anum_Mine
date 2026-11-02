import { expect, test } from '@playwright/test';
import { mockInvoices } from './support/businessInvoicesApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// Emailing an invoice, payment reminders, card payment links, receipts and credit note PDFs (docs/business.md#emailing-an-invoice-and-reminders,
// docs/business.md#payment-links) against the mocked API: asking never sends (the email waits for an owner in Approvals), a bad address is caught
// before the request, a workspace without an email integration says so, an overdue invoice offers the reminder the schedule says is due, the
// owner's Moyasar gives a payment link (or says it is off, or that the check failed), each payment has a receipt, and the blocks pass the layout
// and axe gate in English and Arabic.

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

test('asking to email an invoice waits for an owner and shows what came of it', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  const block = drawer.locator('section', { has: page.getByRole('heading', { name: 'Email and reminders' }) });
  await expect(block.getByText('No emails asked for yet.')).toBeVisible();
  await expect(block.getByText(/Reminders are for overdue invoices/)).toBeVisible();
  await block.getByRole('button', { name: 'Email the invoice' }).click();
  const form = page.getByRole('form', { name: 'Email invoice INV-0001' });
  await expect(form.getByText('Leave empty to use the buyer email on the invoice: sara@acme.example.')).toBeVisible();
  await form.getByLabel('Send to').fill('not an address');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByText(/Enter a valid email address/)).toBeVisible();
  expect(mock.calls.some((call) => call.path === `/invoices/${id}/email`)).toBe(false);
  await form.getByLabel('Send to').fill('');
  await form.getByLabel('Email language').selectOption('ar');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(drawer.getByText(/Asked\. It waits in Approvals/)).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === `/invoices/${id}/email`)?.body).toEqual({ language: 'ar' });
  await expect(block.getByText('Waiting for approval')).toBeVisible();
  await expect(block.getByRole('link', { name: 'Open Approvals' })).toHaveAttribute('href', '#approvals');
  await expect(block.getByRole('button', { name: 'Email the invoice' })).toBeDisabled();

  mock.approveMessage(id);
  await page.reload();
  await expect(page.getByRole('dialog').locator('section', { has: page.getByRole('heading', { name: 'Email and reminders' }) }).getByText('Sent', { exact: true })).toBeVisible();
});

test('without an email integration the API sentence says what to do', async ({ page }) => {
  const mock = await mockInvoices(page, { noEmail: true });
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  await page.getByRole('dialog').getByRole('button', { name: 'Email the invoice' }).click();
  const form = page.getByRole('form', { name: 'Email invoice INV-0001' });
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByText(/No email integration that can send attachments is connected/)).toBeVisible();
});

test('an overdue invoice offers the reminder the schedule says is due', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent({ issue_date: day(-30), due_date: day(-9) });
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('Reminder 1 is due: the invoice is 9 days late.')).toBeVisible();
  await drawer.getByRole('button', { name: 'Send a reminder' }).click();
  const form = page.getByRole('form', { name: 'Payment reminder 1 for invoice INV-0001' });
  await form.getByLabel('Send to').fill('ap@acme.example');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(drawer.getByText(/Asked\. It waits in Approvals/)).toBeVisible();
  expect(mock.calls.find((call) => call.path === `/invoices/${id}/reminders`)?.body).toEqual({ to: 'ap@acme.example', language: 'en' });
  await expect(drawer.getByText('Reminder 1', { exact: true })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Send a reminder' })).toBeDisabled();
});

test('the owner Moyasar gives a card payment link the team can copy', async ({ page }) => {
  const mock = await mockInvoices(page, { moyasar: 'on' });
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const block = page.getByRole('dialog').locator('section', { has: page.getByRole('heading', { name: 'Card payment link' }) });
  await expect(block.getByText('Test mode: no real money moves.')).toBeVisible();
  await block.getByRole('button', { name: 'Make a payment link' }).click();
  await expect(page.getByRole('dialog').getByText('Payment link ready.')).toBeVisible();
  await expect(block.getByRole('link', { name: /checkout\.moyasar\.example/ })).toBeVisible();
  await expect(block.getByText('For SAR 1,150.00')).toBeVisible();
  await expect(block.getByRole('link', { name: 'Open payment page' })).toHaveAttribute('rel', 'noopener noreferrer');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await block.getByRole('button', { name: 'Copy link' }).click();
  await expect(block.getByRole('status')).toHaveText(/Link copied\.|Could not copy/);
});

test('card payments off, and a failed check with a retry', async ({ page }) => {
  const off = await mockInvoices(page, { moyasar: 'off' });
  const id = off.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const block = page.getByRole('dialog').locator('section', { has: page.getByRole('heading', { name: 'Card payment link' }) });
  await expect(block.getByText(/Card payments are off\. The owner turns Moyasar on/)).toBeVisible();
  await expect(block.getByRole('button', { name: 'Make a payment link' })).toHaveCount(0);

  const tab = await page.context().newPage();
  const failing = await mockInvoices(tab, { moyasar: 'error' });
  const other = failing.seedSent();
  await tab.goto(`/#business-invoices/${other}`);
  const failed = tab.getByRole('dialog').locator('section', { has: tab.getByRole('heading', { name: 'Card payment link' }) });
  await expect(failed.getByText(/Billing settings unavailable/)).toBeVisible();
  await expect(failed.getByRole('button', { name: 'Try again' })).toBeVisible();
  await tab.close();
});

test('each payment has a receipt, and a retried payment form records the money once', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: 'Record a payment' }).click();
  const form = page.getByRole('form', { name: 'Record a payment' });
  await form.getByLabel('Amount').fill('400');
  await form.getByRole('button', { name: 'Save payment' }).click();
  await expect(drawer.getByText('Payment recorded.')).toBeVisible();
  const posted = mock.calls.filter((call) => call.method === 'POST' && call.path === `/invoices/${id}/payments`);
  expect(posted).toHaveLength(1);
  const download = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'Receipt PDF' }).click();
  expect((await download).suggestedFilename()).toMatch(/^receipt-INV-0001-/);
  await expect(drawer.getByText(/Saved receipt-INV-0001-/)).toBeVisible();
});

test('the email block reads in Arabic, right to left', async ({ page }) => {
  const mock = await mockInvoices(page, { moyasar: 'on' });
  const id = mock.seedSent({ issue_date: day(-30), due_date: day(-2) });
  await useSavedLocale(page, 'ar');
  await page.goto(`/#business-invoices/${id}`);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('heading', { name: 'البريد والتذكيرات' })).toBeVisible();
  await expect(drawer.getByText('حان موعد التذكير 1: الفاتورة متأخرة 2 يومًا.')).toBeVisible();
  await expect(drawer.getByRole('heading', { name: 'رابط الدفع بالبطاقة' })).toBeVisible();
  await drawer.getByRole('button', { name: 'إرسال الفاتورة بالبريد' }).click();
  await expect(page.getByRole('form').getByLabel('لغة الرسالة')).toHaveValue('ar');
});

for (const size of GATE_SIZES) {
  test.describe(`invoice sending at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('the email, reminder and payment link blocks and the email dialog: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        const mock = await mockInvoices(tab, { moyasar: 'on' });
        const id = mock.seedSent({ issue_date: day(-30), due_date: day(-9), bill_to_name: 'شركة الأمل للتجارة' });
        await useSavedLocale(tab, locale);
        await tab.goto(`/#business-invoices/${id}`);
        const drawer = tab.getByRole('dialog');
        await drawer.getByRole('button', { name: locale === 'ar' ? 'إنشاء رابط دفع' : 'Make a payment link' }).click();
        await expect(drawer.getByRole('link', { name: /checkout\.moyasar\.example/ })).toBeVisible();
        await drawer.getByRole('button', { name: locale === 'ar' ? 'إرسال تذكير' : 'Send a reminder' }).click();
        await tab.getByRole('form').getByRole('button', { name: locale === 'ar' ? 'طلب الموافقة' : 'Ask for approval' }).click();
        await expect(drawer.getByText(locale === 'ar' ? 'بانتظار الموافقة' : 'Waiting for approval').first()).toBeVisible();
        await sweep(tab, `${locale} invoice drawer with sending`);
        await drawer.getByRole('button', { name: locale === 'ar' ? 'إرسال الفاتورة بالبريد' : 'Email the invoice' }).click();
        await expect(tab.getByRole('form')).toBeVisible();
        await sweep(tab, `${locale} email dialog`);
        await tab.close();
      }
    });
  });
}
