import { expect, test } from '@playwright/test';
import { mockSchedules } from './support/invoiceSchedulesApi';
import { useSavedLocale } from './support/workspaceApi';

// Recurring invoices and the planned emails (docs/business.md#recurring-invoices): Business, Invoices, Recurring.
// Every name is SYNTHETIC; the API is mocked at http://localhost:8000.

test('a recurring invoice is set up, opened, paused and its history opens the invoice', async ({ page }) => {
  const mock = await mockSchedules(page);
  await page.goto('/#business-invoices');
  await page.getByRole('group', { name: 'Invoice views' }).getByRole('button', { name: 'Recurring' }).click();
  await expect(page).toHaveURL(/#business-invoices\/recurring$/);
  const main = page.locator('#main');
  await expect(main.getByRole('button', { name: 'Monthly support retainer' })).toBeVisible();
  await expect(main.getByText('2 recurring invoices')).toBeVisible();

  await main.getByRole('button', { name: 'New recurring invoice' }).first().click();
  const form = page.getByRole('form', { name: 'New recurring invoice' });
  await form.getByRole('button', { name: 'Save recurring invoice' }).click();
  await expect(form.getByText('Choose the client to invoice.')).toBeVisible();
  await form.getByLabel('Client account').selectOption({ label: 'Acme Trading' });
  await form.getByLabel('Name').fill('Website care plan');
  await form.getByLabel('Repeats').selectOption('weeks');
  await form.getByLabel('Every how many weeks').fill('2');
  await form.getByRole('group', { name: 'Line 1' }).getByLabel('Description').fill('Care plan');
  await form.getByRole('group', { name: 'Line 1' }).getByLabel('Unit price').fill('500');
  await form.getByLabel(/sent for an owner's approval/).check();
  await form.getByRole('button', { name: 'Save recurring invoice' }).click();
  const created = mock.calls.find((call) => call.method === 'POST' && call.path === '/invoice-schedules');
  expect(created?.body).toMatchObject({ account_id: 'bacc_1', frequency: 'weeks', interval: 2, issue_mode: 'request_approval', title: 'Website care plan' });
  expect((created?.body.lines as unknown[])[0]).toMatchObject({ description: 'Care plan', unit_price: 500 });

  const drawer = page.getByRole('dialog', { name: 'Website care plan' });
  await expect(drawer.getByText('Asks for approval')).toBeVisible();
  await drawer.getByRole('button', { name: 'Close' }).click();

  await main.getByRole('button', { name: 'Monthly support retainer' }).click();
  const open = page.getByRole('dialog', { name: 'Monthly support retainer' });
  await expect(open.getByRole('heading', { name: 'Invoices it drafted' })).toBeVisible();
  await open.getByRole('button', { name: 'Draft what is due now' }).click();
  await expect(open.getByText(/Nothing is due yet/)).toBeVisible();
  await open.getByRole('button', { name: 'Pause' }).click();
  await expect(open.getByText('Paused. Nothing is drafted until you resume it.')).toBeVisible();
  expect(mock.calls.some((call) => call.path === '/invoice-schedules/binsch_1/pause' && call.body.version === 3)).toBe(true);
  await expect(open.getByRole('button', { name: 'Resume' })).toBeVisible();
  await open.getByRole('button', { name: 'End' }).click();
  await page.getByRole('alertdialog', { name: 'End this recurring invoice?' }).getByRole('button', { name: 'End' }).click();
  await expect(open.getByText('Ended. The invoices it drafted stay.')).toBeVisible();
  await open.getByRole('button', { name: 'INV-2026-0001' }).click();
  await expect(page).toHaveURL(/#business-invoices\/binv_1$/);
});

test('an owner turns the planned reminders on and plans today\'s; a member reads why they cannot', async ({ page }) => {
  const mock = await mockSchedules(page, { empty: true });
  await page.goto('/#business-invoices/recurring');
  const main = page.locator('#main');
  await expect(main.getByRole('heading', { name: 'No recurring invoices yet' })).toBeVisible();
  const card = main.getByRole('region', { name: 'Planned emails' });
  await card.getByLabel('Plan payment reminders for overdue invoices').click();
  await expect(card.getByText('Saved.')).toBeVisible();
  await expect(card.getByLabel('Plan payment reminders for overdue invoices')).toBeChecked();
  expect(mock.calls.find((call) => call.method === 'PUT')?.body).toMatchObject({ auto_reminders: true, email_scheduled: false });
  await card.getByRole('button', { name: "Plan today's emails now" }).click();
  await expect(card.getByText('Planned. Each email waits in Approvals.')).toBeVisible();
  await expect(card.getByText(/Planned 1 reminders and 0 invoice emails/)).toBeVisible();
});

test('a member sees the switches but cannot change them, in Arabic too', async ({ page }) => {
  await mockSchedules(page, { role: 'member' });
  await useSavedLocale(page, 'ar');
  await page.goto('/#business-invoices/recurring');
  const main = page.locator('#main');
  await expect(main.getByText('المالكون وحدهم يشغلون هذه الخيارات أو يوقفونها.')).toBeVisible();
  await expect(main.getByLabel('خطط تذكيرات الدفع للفواتير المتأخرة')).toBeDisabled();
  await expect(main.getByRole('button', { name: 'Monthly support retainer' })).toBeVisible();
});
