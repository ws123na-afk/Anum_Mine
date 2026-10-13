import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Department assistants (docs/team-work.md#department-assistants): the table with each assistant's
// queue, overdue and finished work, "New assistant" from a template, edit, turn off, delete, routing
// a message, and the refused and failed states.
const table = (page: Page) => page.getByRole('table');
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the table shows each assistant with its queue and the band adds them up', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await expect(page.getByRole('heading', { level: 1, name: 'Assistants' })).toBeVisible();
  const row = table(page).getByRole('row', { name: /Sales assistant/ });
  await expect(row).toContainText('Sales · Suggest, a person approves');
  await expect(row.getByRole('cell').nth(1)).toHaveText('1');
  await expect(row).toContainText('Takes new messages from: WhatsApp');
  const band = page.getByRole('region', { name: 'Department assistants at a glance' });
  await expect(band).toContainText('Switched on1Of 1 assistant');
  await expect(band).toContainText('In their queues1');
  await expect(band).toContainText('Finished0');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a new assistant starts from a department template in a dialog', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await page.getByRole('button', { name: 'New assistant' }).click();
  const chooser = page.getByRole('dialog', { name: 'New assistant' });
  await expect(chooser.getByRole('listitem').filter({ hasText: 'Sales' }).getByRole('button')).toBeDisabled();
  await chooser.getByRole('listitem').filter({ hasText: 'Support' }).getByRole('button', { name: 'Use template' }).click();
  const form = page.getByRole('dialog', { name: 'New assistant' });
  const email = form.getByRole('checkbox', { name: 'Email' });
  await email.click();
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('support assistant was saved.')).toBeVisible();
  await expect(table(page).getByRole('row', { name: /support assistant/ })).toContainText('Takes new messages from: Email');
});

test('edit, turn off and delete ask the API and say what happened', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await page.getByRole('button', { name: 'Edit Sales assistant' }).click();
  const form = page.getByRole('dialog', { name: 'Edit assistant' });
  await form.getByLabel('Name', { exact: true }).fill('Quotes desk');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Quotes desk was saved.')).toBeVisible();
  await page.getByRole('button', { name: 'Turn off Quotes desk' }).click();
  await expect(table(page).getByRole('row', { name: /Quotes desk/ })).toContainText('Off');
  await page.getByRole('button', { name: 'Delete Quotes desk' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this assistant?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('heading', { name: 'No assistants yet' })).toBeVisible();
});

test('routing a message is folded away until opened', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await expect(page.getByLabel('From')).toBeHidden();
  await page.getByText('Route an inbound message').click();
  await page.getByLabel('From').fill('+966500000000');
  await page.getByLabel('Message', { exact: true }).fill('What is your price for 4 tyres?');
  await page.getByRole('button', { name: 'Route message' }).click();
  await expect(page.getByText('Routed to Sales assistant (price).')).toBeVisible();
});

test('a refused change names who can make it; a role without access is told why', async ({ page }) => {
  await mockWorkspaceApi(page, { denyAssistantChanges: true });
  await page.goto('/#assistants');
  await page.getByRole('button', { name: 'Turn off Sales assistant' }).click();
  await expect(page.getByRole('alert')).toContainText('Only a workspace owner can change department assistants.');
});

test('without access the page explains it instead of an empty table', async ({ page }) => {
  await mockWorkspaceApi(page, { denyAssistants: true });
  await page.goto('/#assistants');
  await expect(page.getByRole('heading', { name: 'Assistants are not available to your role' })).toBeVisible();
  await expect(page.getByText('Seeing department assistants needs a member or owner role in this workspace.')).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('the Assistants page reads right to left in Arabic', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#assistants');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'المساعدون' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^تعديل .*Sales assistant/ })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
