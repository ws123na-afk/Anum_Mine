import { expect, test } from '@playwright/test';
import { mockInvoices } from './support/businessInvoicesApi';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// Refunds of a card payment asked for from ANUM (docs/business.md#refunds-from-anum) and the list of provider refunds
// ANUM could not match (docs/business.md#refunds-anum-could-not-match), against a mocked API.

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };

function cardPaid() {
  return {
    amount_paid: 1150, status: 'paid',
    payments: [{ id: 'bpay_card', ...scope, invoice_id: 'x', amount: 1150, paid_on: day(-3), method: 'card', reference: 'Stripe pi_1', note: '', recorded_by: 'system:stripe',
      provider: 'stripe', provider_payment_id: 'pi_1', provider_amount: 1150, created_at: now(), updated_at: now() }],
  };
}

const review = (id: string, extra: Record<string, unknown> = {}) => ({
  id, ...scope, invoice_id: null, invoice_number: '', provider: 'stripe', provider_refund_id: `re_unknown_${id}`, provider_payment_id: 'pi_never_recorded', currency: 'SAR',
  amount: 25, status: 'open', note: '', checked_by: null, checked_at: null, created_at: now(), updated_at: now(), ...extra,
});

test('a member asks for a refund to the card, it waits for an owner, then it is refunded and recorded', async ({ page }) => {
  const mock = await mockInvoices(page, { role: 'member' });
  const id = mock.seedSent(cardPaid());
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByRole('button', { name: 'Refund to the card' }).click();
  const form = page.getByRole('dialog', { name: 'Refund a card payment' });
  await expect(form.getByText(/At most SAR\s?1,150\.00 can be refunded/)).toBeVisible();
  await expect(form.getByLabel('Amount to refund')).toHaveValue('1150.00');
  await form.getByLabel('Amount to refund').fill('2000');
  await form.getByLabel('Why the money goes back').fill('Returned item');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByRole('alert')).toContainText('at most SAR');
  await form.getByLabel('Amount to refund').fill('300');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form).toHaveCount(0);
  const asked = drawer.getByRole('group', { name: 'Refunds asked for from ANUM' });
  await expect(asked.getByText('Waiting for approval')).toBeVisible();
  await expect(asked.getByText(/Approval code abababababab/)).toBeVisible();
  await expect(asked.getByText('Returned item')).toBeVisible();
  const call = mock.calls.find((entry) => entry.method === 'POST' && entry.path.endsWith('/refund-requests'));
  expect(call?.body).toEqual({ amount: 300, reason: 'Returned item' });
  // The held 300 is no longer offered; an owner approves in Approvals and the refund lands on the payment.
  mock.approveRefund(id);
  await page.reload();
  await expect(page.getByRole('dialog').getByRole('group', { name: 'Refunds asked for from ANUM' }).getByText('Refunded', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('group', { name: 'Refunds through Stripe' }).getByText(/SAR\s?300\.00 refunded on/)).toBeVisible();
});

test('a request waiting for approval can be withdrawn', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent(cardPaid());
  await page.goto(`/#business-invoices/${id}`);
  await page.getByRole('dialog').getByRole('button', { name: 'Refund to the card' }).click();
  const form = page.getByRole('dialog', { name: 'Refund a card payment' });
  await form.getByLabel('Why the money goes back').fill('Duplicate charge');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await page.getByRole('button', { name: 'Withdraw request' }).click();
  await page.getByRole('alertdialog', { name: 'Withdraw this refund request?' }).getByRole('button', { name: 'Withdraw request' }).click();
  await expect(page.getByText('The refund request was withdrawn.')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Refunds asked for from ANUM' }).getByText('Withdrawn', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Refund to the card' })).toBeVisible();
});

test('an owner checks a refund ANUM could not match, and opens it again', async ({ page }) => {
  const mock = await mockInvoices(page);
  mock.reviews.push(review('binrev_1'), review('binrev_2', { amount: 40 }));
  await page.goto('/#business-invoices');
  await expect(page.getByText('Card refunds to check: 2.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Open the list' }).click();
  await expect(page).toHaveURL(/#business-invoices\/refunds$/);
  const table = page.getByRole('region', { name: 'Refunds to check' });
  await expect(table.getByRole('row')).toHaveCount(3);
  await table.getByRole('button', { name: 'Mark checked' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Mark this refund checked' });
  await dialog.getByLabel('What you found (optional)').fill('A test payment in Stripe');
  await dialog.getByRole('button', { name: 'Mark checked' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  await expect(table.getByRole('row')).toHaveCount(2);
  await page.getByLabel('Show').selectOption('checked');
  await expect(table.getByText('A test payment in Stripe')).toBeVisible();
  await table.getByRole('button', { name: 'Open again' }).click();
  await expect(page.getByText('Nothing to check')).toBeVisible();
  expect(mock.calls.filter((entry) => entry.path.startsWith('/refund-reviews/')).map((entry) => entry.path)).toEqual(['/refund-reviews/binrev_1/check', '/refund-reviews/binrev_1/reopen']);
});

test('members read the list but only an owner marks a refund checked; an empty list says what shows there', async ({ page }) => {
  const mock = await mockInvoices(page, { role: 'member' });
  await page.goto('/#business-invoices/refunds');
  await expect(page.getByText('Nothing to check')).toBeVisible();
  mock.reviews.push(review('binrev_3'));
  await page.reload();
  await expect(page.getByText('Only a workspace owner marks a refund checked.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mark checked' })).toHaveCount(0);
});

test('the refund form, the requests and the review list pass the layout and axe checks in English and Arabic', async ({ page }) => {
  for (const locale of ['en', 'ar'] as const) {
    const tab = await page.context().newPage();
    const mock = await mockInvoices(tab);
    mock.reviews.push(review('binrev_9'));
    const id = mock.seedSent(cardPaid());
    await useSavedLocale(tab, locale);
    await tab.goto(`/#business-invoices/${id}`);
    await tab.getByRole('dialog').getByRole('button', { name: locale === 'ar' ? 'استرداد إلى البطاقة' : 'Refund to the card' }).click();
    const form = tab.getByRole('dialog', { name: locale === 'ar' ? 'استرداد دفعة بالبطاقة' : 'Refund a card payment' });
    await expect(form).toBeVisible();
    await sweep(tab, `${locale} refund form`);
    await form.getByLabel(locale === 'ar' ? 'سبب إعادة المبلغ' : 'Why the money goes back').fill(locale === 'ar' ? 'إرجاع قطعة' : 'Returned item');
    await form.getByRole('button', { name: locale === 'ar' ? 'اطلب الموافقة' : 'Ask for approval' }).click();
    await expect(tab.getByRole('dialog').getByRole('group', { name: locale === 'ar' ? 'طلبات استرداد من داخل ANUM' : 'Refunds asked for from ANUM' })).toBeVisible();
    await sweep(tab, `${locale} refund requests`);
    await tab.goto('/#business-invoices/refunds');
    await expect(tab.getByRole('region', { name: locale === 'ar' ? 'مبالغ مستردة للمراجعة' : 'Refunds to check' })).toBeVisible();
    await sweep(tab, `${locale} refund reviews`);
    await tab.close();
  }
});
