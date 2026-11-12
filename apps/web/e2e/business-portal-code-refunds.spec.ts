import { expect, test } from '@playwright/test';
import { mockInvoices } from './support/businessInvoicesApi';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// The owner's side of two Business depth items against a mocked API: the switch for signing in to the portal with an
// emailed code (docs/business-portal.md#signing-in-with-an-emailed-code) and refunds made in the Stripe dashboard
// shown on the invoice's card payment (docs/business.md#refunds-made-in-the-provider-dashboard).

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const now = () => new Date().toISOString();
const scope = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' };

test('an owner turns on code sign-in and copies the sign-in address', async ({ page }) => {
  const mock = await mockInvoices(page);
  await page.goto('/#business-accounts/bacc_1');
  const drawer = page.getByRole('dialog', { name: 'Acme Trading' });
  const block = drawer.getByRole('group', { name: /Sign in with an emailed code/ });
  await expect(block.getByText('Off', { exact: true })).toBeVisible();
  // The box follows the server's answer, so it is clicked and then checked.
  await block.getByRole('checkbox', { name: 'Let customers sign in with an emailed code' }).click();
  await expect(block.getByText('On', { exact: true })).toBeVisible();
  await expect(block.getByRole('checkbox')).toBeChecked();
  await expect(block.getByText(/\/portal#signin=tenant_local\.workspace_foundation$/)).toBeVisible();
  await expect(block.getByRole('button', { name: 'Copy sign-in address' })).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'PUT' && call.path === '/portal/sign-in')?.body).toEqual({ enabled: true });
});

test('a member sees the switch but only an owner changes it', async ({ page }) => {
  await mockInvoices(page, { role: 'member' });
  await page.goto('/#business-accounts/bacc_1');
  const block = page.getByRole('dialog', { name: 'Acme Trading' }).getByRole('group', { name: /Sign in with an emailed code/ });
  await expect(block.getByText('Only a workspace owner turns this on or off.')).toBeVisible();
  await expect(block.getByRole('checkbox')).toHaveCount(0);
});

function refundedInvoice() {
  return {
    amount_paid: 750, status: 'sent',
    payments: [{ id: 'bpay_card', ...scope, invoice_id: 'x', amount: 1050, paid_on: day(-5), method: 'card', reference: 'Stripe pi_1', note: '', recorded_by: 'system:stripe',
      provider: 'stripe', provider_payment_id: 'pi_1', provider_amount: 1100, created_at: now(), updated_at: now() }],
    refunds: [
      { id: 'binref_1', ...scope, invoice_id: 'x', payment_id: 'bpay_card', provider: 'stripe', provider_refund_id: 're_1', provider_payment_id: 'pi_1', currency: 'SAR',
        provider_amount: 350, amount: 300, refunded_on: day(-1), status: 'recorded', note: '', recorded_by: 'system:stripe', reversed_at: null, created_at: now(), updated_at: now() },
      { id: 'binref_2', ...scope, invoice_id: 'x', payment_id: 'bpay_card', provider: 'stripe', provider_refund_id: 're_2', provider_payment_id: 'pi_1', currency: 'SAR',
        provider_amount: 20, amount: 20, refunded_on: day(-1), status: 'reversed', note: '', recorded_by: 'system:stripe', reversed_at: now(), created_at: now(), updated_at: now() },
    ],
  };
}

test('a dashboard refund shows on its card payment, which can no longer be removed', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent(refundedInvoice());
  await page.goto(`/#business-invoices/${id}`);
  const refunds = page.getByRole('dialog').getByRole('group', { name: 'Refunds through Stripe' });
  await expect(refunds.getByText(/SAR\s?350\.00 refunded on/)).toBeVisible();
  await expect(refunds.getByText(/SAR\s?50\.00 of it was money the card took above the balance/)).toBeVisible();
  await expect(refunds.getByText('Stripe reported this refund failed, so it no longer counts.')).toBeVisible();
  await expect(refunds.getByText(/owed again\. Issue a credit note/)).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button', { name: 'Remove payment' })).toHaveCount(0);
});

test('the refunds and the switch pass the layout and axe checks in English and Arabic', async ({ page }) => {
  for (const locale of ['en', 'ar'] as const) {
    const tab = await page.context().newPage();
    const mock = await mockInvoices(tab);
    const id = mock.seedSent(refundedInvoice());
    await useSavedLocale(tab, locale);
    await tab.goto(`/#business-invoices/${id}`);
    await expect(tab.getByRole('dialog').getByRole('group', { name: locale === 'ar' ? /مبالغ مستردة عبر/ : 'Refunds through Stripe' })).toBeVisible();
    await sweep(tab, `${locale} invoice refunds`);
    await tab.goto('/#business-accounts/bacc_1');
    await expect(tab.getByRole('dialog').getByRole('group', { name: locale === 'ar' ? /الدخول برمز يصل بالبريد/ : /Sign in with an emailed code/ })).toBeVisible();
    await sweep(tab, `${locale} code sign-in switch`);
    await tab.close();
  }
});
