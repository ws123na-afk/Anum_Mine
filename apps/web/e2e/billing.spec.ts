import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { mockBilling } from './support/billingApi';

// Customer billing (docs/billing.md): a billing admin sees the plan and usage, previews and confirms
// an upgrade, a downgrade that does not fit is blocked, an invoice opens and its PDF downloads; a
// member sees who can; the platform owner records a payment with a proof file.
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const serious = async (page: Page, selector?: string) => {
  const builder = new AxeBuilder({ page });
  if (selector) builder.include(selector);
  return (await builder.analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
};

test('a billing admin sees the plan, previews and confirms an upgrade, and downloads an invoice PDF', async ({ page }) => {
  await mockWorkspaceApi(page);
  const sent = await mockBilling(page);
  await page.goto('/#billing');
  await expect(page.getByRole('heading', { name: 'Plan', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Starter' })).toBeVisible();
  await expect(page.getByText('SAR 299.00 per month').first()).toBeVisible();
  await expect(page.getByRole('meter', { name: 'Seats' })).toHaveAttribute('aria-valuenow', '60');
  await expect(page.getByText('RIBLSARI').first()).toBeVisible();
  expect(await serious(page, '.blPage')).toEqual([]);
  expect(await noOverflow(page)).toBe(true);

  // A downgrade the usage does not fit is blocked before anything is sent.
  await page.getByRole('button', { name: 'Change plan' }).click();
  const dialog = page.getByRole('dialog', { name: 'Change plan' });
  await dialog.getByRole('button', { name: 'Choose Solo' }).click();
  await expect(dialog.getByText('Seats: 3 in use, the plan allows 1')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Confirm change' })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Back to plans' }).click();

  // The upgrade: the preview's totals, then the change and its proration invoice.
  await dialog.getByRole('button', { name: 'Choose Business' }).click();
  await expect(dialog.getByText('SAR 287.50')).toBeVisible();
  await expect(dialog.getByText('An invoice for the total is issued as soon as you confirm.')).toBeVisible();
  expect(await serious(page, '.dialogPanel')).toEqual([]);
  await dialog.getByRole('button', { name: 'Confirm change' }).click();
  await expect(page.getByText('The plan is now Business. Invoice INV-000002 was issued.')).toBeVisible();
  expect(sent.changes).toEqual([{ plan_id: 'plan_business' }]);
  expect(sent.previews).toEqual(['plan_solo', 'plan_business']);

  // Invoices: search by number, open one, download its PDF through the signed-in request.
  const invoices = page.getByRole('table', { name: 'Invoices' });
  await expect(invoices).toContainText('INV-000002');
  await page.getByLabel('Search by number').fill('000001');
  await expect(invoices).not.toContainText('INV-000002');
  await page.getByRole('button', { name: 'Open invoice INV-000001' }).click();
  const drawer = page.getByRole('dialog', { name: 'Invoice' });
  await expect(drawer.getByText('VAT 15% (SA)')).toBeVisible();
  await expect(drawer.getByText('SAR 343.85').first()).toBeVisible();
  await expect(drawer.getByRole('button', { name: /Pay .* by card/ })).toHaveCount(0);
  expect(await serious(page, '.drawerPanel')).toEqual([]);
  const download = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'Download PDF' }).click();
  expect((await download).suggestedFilename()).toBe('INV-000001.pdf');
  expect(sent.pdfs).toHaveLength(1);
  await drawer.getByRole('button', { name: 'Close' }).click();

  // The billing contact form.
  await page.getByLabel('Contact name').fill('Omar Saleh');
  await page.getByRole('button', { name: 'Save billing details' }).click();
  await expect(page.getByText('Billing details saved.')).toBeVisible();
  expect(sent.accounts[0]).toMatchObject({ contact_name: 'Omar Saleh', country: 'SA', contact_email: 'billing@acme.example' });
});

test('a member sees who can open billing', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page, { role: 'member' });
  await page.goto('/#billing');
  await expect(page.getByRole('heading', { name: 'Billing is for organisation billing admins' })).toBeVisible();
  await expect(page.getByText('organisation administrator (realm role org_admin) can see and change billing')).toBeVisible();
});

test('the platform owner records a payment with a proof file', async ({ page }) => {
  await mockWorkspaceApi(page);
  const sent = await mockBilling(page);
  await page.goto('/#platformBilling');
  await expect(page.getByRole('heading', { name: 'Billing console', level: 1 })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Revenue summary' })).toContainText('SAR 299.00');
  const table = page.getByRole('table', { name: 'Subscriptions' });
  await expect(table).toContainText('Falcon Logistics');
  await page.getByLabel('Search organisations').fill('acme');
  await expect(table).not.toContainText('Falcon Logistics');
  expect(await noOverflow(page)).toBe(true);
  await page.getByRole('button', { name: 'Open the billing of Acme Trading' }).click();
  await expect(page.getByRole('heading', { name: 'Acme Trading' })).toBeVisible();
  await page.getByRole('button', { name: 'Open invoice INV-000001' }).click();
  const drawer = page.getByRole('dialog', { name: 'Invoice' });
  await drawer.getByRole('button', { name: 'Record payment' }).click();
  const form = page.getByRole('dialog', { name: 'Record a payment for INV-000001' });
  await expect(form.getByLabel('Amount (SAR)')).toHaveValue('343.85');
  await form.getByLabel('Reference').fill('TRX-778');
  await form.getByLabel('Proof of payment (optional)').setInputFiles({ name: 'receipt.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%receipt\n') });
  expect(await serious(page, '.dialogPanel')).toEqual([]);
  await form.getByRole('button', { name: 'Record payment' }).click();
  await expect(page.getByText('Payment recorded on INV-000001.')).toBeVisible();
  expect(sent.payments).toEqual([{ amount_minor: 34385, method: 'bank_transfer', reference: 'TRX-778', proof: { name: 'receipt.pdf', content_type: 'application/pdf', data_base64: Buffer.from('%PDF-1.4\n%receipt\n').toString('base64') } }]);
  await expect(drawer.getByText('Proof: receipt.pdf')).toBeVisible();
});

test('billing reads in Arabic, right to left, without overflow', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockBilling(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#billing');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'الخطة', exact: true })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
});
