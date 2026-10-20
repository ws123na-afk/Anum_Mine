import { expect, test } from '@playwright/test';
import { mockInvoices, PORTAL_TOKEN } from './support/businessInvoicesApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// Credit notes, customers' payment reports, the invoice PDF and the Accounts tab (docs/business.md#credit-notes,
// docs/business-portal.md) against the mocked API: an owner downloads the PDF, credits part of an invoice and takes
// it back, confirms or rejects what a customer reported, a member can decide a report but not credit, and the
// Accounts tab lists clients with their portal links, with its empty, failed and Arabic states.

test('the PDF downloads with the invoice number as its file name', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  const download = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'Download PDF' }).click();
  expect((await download).suggestedFilename()).toBe('INV-0001.pdf');
  await expect(drawer.getByText('Saved INV-0001.pdf.')).toBeVisible();
  expect(mock.calls.some((call) => call.method === 'GET' && call.path === `/invoices/${id}/pdf`)).toBe(true);
});

test('a refused PDF shows the API sentence instead of a silent failure', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.route(`http://localhost:8000/api/v1/business/invoices/${id}/pdf`, (route) => route.fulfill({
    status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'Business is for workspace owners and members.', correlation_id: 'c', details: [] } }),
  }));
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: 'Download PDF' }).click();
  await expect(drawer.getByText(/Business is for workspace owners and members/)).toBeVisible();
});

test('an owner credits part of an invoice, is stopped at the balance, and takes a credit note back', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('heading', { name: 'Credit notes' })).toBeVisible();
  await expect(drawer.getByText(/No credit note/)).toBeVisible();
  await drawer.getByRole('button', { name: 'Issue a credit note' }).click();
  const form = page.getByRole('form', { name: 'Issue a credit note' });
  await expect(form.getByLabel('Amount, tax included')).toHaveValue('1150.00');
  await form.getByLabel('Amount, tax included').fill('1150.01');
  await form.getByLabel('Reason').fill('Agreed discount');
  await form.getByRole('button', { name: 'Issue credit note' }).click();
  await expect(form.getByText(/at most the balance of SAR.1,150\.00/)).toBeVisible();
  await form.getByLabel('Amount, tax included').fill('230');
  await form.getByLabel('Reason').fill('ab');
  await form.getByRole('button', { name: 'Issue credit note' }).click();
  await expect(form.getByText(/at least three characters/)).toBeVisible();
  await form.getByLabel('Reason').fill('Agreed discount');
  await form.getByRole('button', { name: 'Issue credit note' }).click();

  await expect(drawer.getByText('Credit note issued.')).toBeVisible();
  const list = drawer.locator('section', { has: page.getByRole('heading', { name: 'Credit notes' }) });
  await expect(list).toContainText('INV-0001-CN1');
  await expect(list).toContainText('SAR 230.00');
  await expect(list).toContainText('SAR 200.00 before tax and SAR 30.00 tax');
  await expect(list).toContainText('Agreed discount');
  await expect(drawer.locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 920.00');
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === `/invoices/${id}/credit-notes`)?.body).toEqual({ amount: 230, reason: 'Agreed discount' });
  // With a credit note the invoice cannot be voided until it is taken back.
  await expect(drawer.getByRole('button', { name: 'Void invoice' })).toHaveCount(0);
  await expect(drawer.getByText(/has credit notes\. Void them first/)).toBeVisible();

  await drawer.getByRole('button', { name: 'Void credit note' }).click();
  const reason = page.getByRole('form', { name: 'Void credit note INV-0001-CN1?' });
  await reason.getByRole('button', { name: 'Void credit note' }).click();
  await expect(reason.getByRole('alert')).toBeVisible();
  await reason.getByLabel('Reason').fill('Entered twice');
  await reason.getByRole('button', { name: 'Void credit note' }).click();
  await expect(drawer.getByText('Credit note voided.')).toBeVisible();
  await expect(list).toContainText('Void');
  await expect(list).toContainText('Entered twice');
  await expect(drawer.getByRole('button', { name: 'Void invoice' })).toBeVisible();
});

test('credit that settles an invoice shows it settled by credit and then reopens when taken back', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: 'Issue a credit note' }).click();
  const form = page.getByRole('form', { name: 'Issue a credit note' });
  await form.getByLabel('Reason').fill('Cancelled by agreement');
  await form.getByRole('button', { name: 'Issue credit note' }).click();
  await expect(drawer.getByText('Credit note issued. Nothing is owed on this invoice now.')).toBeVisible();
  await expect(drawer.getByText('Settled by credit').first()).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Issue a credit note' })).toHaveCount(0);
  await expect(drawer.getByText('Nothing is owed, so there is nothing left to credit.')).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Record a payment' })).toHaveCount(0);
  await drawer.getByRole('button', { name: 'Void credit note' }).click();
  await page.getByRole('form', { name: 'Void credit note INV-0001-CN1?' }).getByLabel('Reason').fill('Entered by mistake');
  await page.getByRole('form', { name: 'Void credit note INV-0001-CN1?' }).getByRole('button', { name: 'Void credit note' }).click();
  await expect(drawer.getByRole('button', { name: 'Record a payment' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Issue a credit note' })).toBeVisible();
});

test('a customer payment report is confirmed into a payment, or rejected with a reason', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  mock.seedNotice(id, { amount: 400, reference: 'TRX-31' });
  mock.seedNotice(id, { amount: 100, reference: 'TRX-77', payer_name: 'Omar Ali' });
  await page.goto('/#business-invoices');
  await expect(page.getByText('2 payment reports from customers are waiting for you to confirm or reject them.')).toBeVisible();
  await expect(page.getByRole('row', { name: /INV-0001/ })).toContainText('2 reports');
  await page.getByRole('button', { name: 'INV-0001' }).click();
  const drawer = page.getByRole('dialog');
  const block = drawer.locator('section', { has: page.getByRole('heading', { name: 'Payments the customer reported' }) });
  await expect(block).toContainText('A report is not a payment and changes no balance.');
  await expect(block).toContainText('Reported by Sara Khan');
  await expect(drawer.locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 1,150.00');

  await block.getByRole('listitem').filter({ hasText: 'TRX-31' }).getByRole('button', { name: 'Confirm payment' }).click();
  const confirm = page.getByRole('form', { name: 'Confirm this payment?' });
  await expect(confirm).toContainText('SAR 400.00');
  await confirm.getByLabel('Note (optional)').fill('Seen on the statement');
  await confirm.getByRole('button', { name: 'Confirm payment' }).click();
  await expect(drawer.getByText('Payment recorded from the customer\'s report.')).toBeVisible();
  await expect(drawer.getByText('Partly paid')).toBeVisible();
  await expect(drawer.getByText('TRX-31').first()).toBeVisible();
  expect(mock.calls.find((call) => call.path.endsWith('/confirm'))?.body).toEqual({ note: 'Seen on the statement' });

  await block.getByRole('listitem').filter({ hasText: 'TRX-77' }).getByRole('button', { name: 'Reject' }).click();
  const reject = page.getByRole('form', { name: 'Reject this report?' });
  await reject.getByRole('button', { name: 'Reject' }).click();
  await expect(reject.getByRole('alert')).toBeVisible();
  await reject.getByLabel('Reason (the customer sees it)').fill('Nothing arrived on that date');
  await reject.getByRole('button', { name: 'Reject' }).click();
  await expect(drawer.getByText('Report rejected.')).toBeVisible();
  await expect(block.getByRole('listitem').filter({ hasText: 'TRX-77' })).toContainText('Rejected');
  await expect(block.getByRole('listitem').filter({ hasText: 'TRX-77' })).toContainText('Nothing arrived on that date');
  await expect(block.getByRole('button', { name: 'Confirm payment' })).toHaveCount(0);
  await expect(drawer.locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 750.00');
});

test('a report that no longer fits the balance is refused with advice and stays waiting', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  mock.seedNotice(id, { amount: 900, reference: 'TRX-90' });
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: 'Issue a credit note' }).click();
  const credit = page.getByRole('form', { name: 'Issue a credit note' });
  await credit.getByLabel('Amount, tax included').fill('500');
  await credit.getByLabel('Reason').fill('Agreed discount');
  await credit.getByRole('button', { name: 'Issue credit note' }).click();
  await expect(drawer.getByText('Credit note issued.')).toBeVisible();
  await drawer.getByRole('button', { name: 'Confirm payment' }).click();
  await page.getByRole('form', { name: 'Confirm this payment?' }).getByRole('button', { name: 'Confirm payment' }).click();
  await expect(page.getByRole('form', { name: 'Confirm this payment?' }).getByText(/Reject this notice and record the payment by hand/)).toBeVisible();
});

test('a member decides a report but cannot issue a credit note', async ({ page }) => {
  const mock = await mockInvoices(page, { role: 'member' });
  const id = mock.seedSent();
  mock.seedNotice(id, { reference: 'TRX-31' });
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('button', { name: 'Confirm payment' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Issue a credit note' })).toHaveCount(0);
  await expect(drawer.getByText('Only an owner can issue or void a credit note.')).toBeVisible();
});

test('a sent invoice with no customer report says so', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  await expect(page.getByRole('dialog').getByText('The customer has not reported a payment.')).toBeVisible();
});

test('the invoice drawer with credit notes and reports reads in Arabic', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  mock.seedNotice(id);
  await page.addInitScript(() => window.localStorage.setItem('anum.locale', 'ar'));
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(drawer.getByRole('button', { name: 'تنزيل PDF' })).toBeVisible();
  await expect(drawer.getByRole('heading', { name: 'الدفعات التي أبلغ عنها العميل' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'تأكيد الدفعة' })).toBeVisible();
  await drawer.getByRole('button', { name: 'إصدار إشعار دائن' }).click();
  await expect(page.getByRole('form', { name: 'إصدار إشعار دائن' })).toBeVisible();
});

// The UI quality gate on the states the page-level gate cannot reach: the invoice drawer with credit notes and
// reports, the credit dialog and the confirm dialog. The sizes are set here, so the mobile project leaves them
// out (@gate, playwright.config.ts).
for (const size of GATE_SIZES) {
  test.describe(`invoice credit notes and reports at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('drawer and dialogs: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        const mock = await mockInvoices(tab);
        const sent = mock.seedSent({ credit_notes: [{ id: 'bcrn_x', tenant_id: 't', workspace_id: 'w', invoice_id: 'x', account_id: 'bacc_1', seq: 1, number: 'INV-0001-CN1', status: 'issued', currency: 'SAR', amount: 115, subtotal: 100, tax: 15,
          reason: 'A long reason that should wrap inside the block without pushing the drawer wider than the screen', issue_date: '2026-10-01', issued_by: 'user_owner', voided_by: null, voided_at: null, void_reason: '' }], amount_credited: 115 });
        mock.seedNotice(sent, { reference: 'A long transfer reference number that must wrap inside the list', note: 'Paid from the company account' });
        await useSavedLocale(tab, locale);
        await tab.goto(`/#business-invoices/${sent}`);
        const drawer = tab.getByRole('dialog');
        await expect(drawer.locator('dl[aria-label]').first()).toBeVisible();
        await sweep(tab, `${locale} drawer with credit notes and reports`);
        await drawer.getByRole('button', { name: locale === 'ar' ? 'إصدار إشعار دائن' : 'Issue a credit note' }).click();
        await sweep(tab, `${locale} credit dialog`);
        await tab.getByRole('form', { name: locale === 'ar' ? 'إصدار إشعار دائن' : 'Issue a credit note' }).getByRole('button', { name: locale === 'ar' ? 'إلغاء' : 'Cancel' }).click();
        await drawer.getByRole('button', { name: locale === 'ar' ? 'تأكيد الدفعة' : 'Confirm payment' }).click();
        await sweep(tab, `${locale} confirm dialog`);
        await tab.close();
      }
    });
  });
}

// ---- the Accounts tab ----------------------------------------------------------------------------

test('the Accounts tab lists clients and holds the customer portal block', async ({ page }) => {
  const mock = await mockInvoices(page);
  await page.goto('/#business-accounts');
  await expect(page.getByRole('heading', { name: 'Business' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Accounts' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('1 account')).toBeVisible();
  const row = page.getByRole('row', { name: /Acme Trading/ });
  await expect(row).toContainText('Riyadh, SA');
  await row.getByRole('button', { name: 'Acme Trading' }).click();

  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  await expect(drawer.getByRole('heading', { name: 'Contacts' })).toBeVisible();
  await expect(drawer.getByText('Sara Khan').first()).toBeVisible();
  await expect(drawer.getByText('Do not contact').first()).toBeVisible();
  await expect(drawer.getByRole('heading', { name: 'Customer portal' })).toBeVisible();
  await expect(drawer.getByText('No link has been created for this client.')).toBeVisible();
  await expect(page).toHaveURL(/#business-accounts\/bacc_1$/);

  await drawer.getByRole('button', { name: 'Create a portal link' }).click();
  const create = page.getByRole('form', { name: 'Create a portal link' });
  await create.getByRole('button', { name: 'Create link' }).click();
  const shown = page.getByRole('dialog', { name: 'Portal link for Sara Khan' });
  await expect(shown.getByLabel('Portal link')).toHaveValue(new RegExp(`/portal#access=${PORTAL_TOKEN}$`));
  await shown.getByRole('button', { name: 'I have copied it' }).click();
  await expect(page.getByText(PORTAL_TOKEN)).toHaveCount(0);
  await expect(drawer.getByText('Sara Khan').first()).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === '/portal/access')?.body).toMatchObject({ account_id: 'bacc_1' });
});

test('a member sees the accounts and who has a link but cannot create one', async ({ page }) => {
  await mockInvoices(page, { role: 'member' });
  await page.goto('/#business-accounts/bacc_1');
  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  await expect(drawer.getByRole('heading', { name: 'Customer portal' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Create a portal link' })).toHaveCount(0);
  await expect(drawer.getByText('Only an owner can create or turn off a link.')).toBeVisible();
});

test('the Accounts tab searches, shows the empty state, and offers a retry after a failure', async ({ page }) => {
  await mockInvoices(page);
  await page.goto('/#business-accounts');
  await page.getByRole('searchbox', { name: 'Search accounts by name' }).fill('zzz');
  await page.route('http://localhost:8000/api/v1/business/accounts?*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, limit: 25, offset: 0 }) }));
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('heading', { name: 'Nothing matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('heading', { name: 'Nothing matches' })).toHaveCount(0);
});

test('the Accounts tab says there are no accounts and gives the next step', async ({ page }) => {
  await mockInvoices(page);
  await page.route('http://localhost:8000/api/v1/business/accounts?*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, limit: 25, offset: 0 }) }));
  await page.goto('/#business-accounts');
  await expect(page.getByRole('heading', { name: 'No client accounts yet' })).toBeVisible();
  await expect(page.getByText(/An account is created with its first lead/)).toBeVisible();
});

test('the Accounts tab shows a failed list with a retry that works', async ({ page }) => {
  await mockInvoices(page);
  let failing = true;
  await page.route('http://localhost:8000/api/v1/business/accounts?*', (route) => {
    if (failing) return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'internal_error', message: 'Database unavailable', correlation_id: 'c', details: [] } }) });
    return route.fallback();
  });
  await page.goto('/#business-accounts');
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
  failing = false;
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.getByRole('row', { name: /Acme Trading/ })).toBeVisible();
});

test('the Accounts tab reads in Arabic, right to left', async ({ page }) => {
  await mockInvoices(page);
  await page.addInitScript(() => window.localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#business-accounts');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('button', { name: 'الحسابات' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('حساب واحد')).toBeVisible();
});

for (const size of GATE_SIZES) {
  test.describe(`accounts at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('the list and the account drawer: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        await mockInvoices(tab);
        await useSavedLocale(tab, locale);
        await tab.goto('/#business-accounts');
        await expect(tab.getByRole('row', { name: /Acme Trading/ })).toBeVisible();
        await sweep(tab, `${locale} accounts list`);
        await tab.goto('/#business-accounts/bacc_1');
        await expect(tab.getByRole('dialog').getByRole('heading', { name: locale === 'ar' ? 'بوابة العميل' : 'Customer portal' })).toBeVisible();
        await sweep(tab, `${locale} account drawer`);
        await tab.close();
      }
    });
  });
}
