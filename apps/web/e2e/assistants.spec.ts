import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Department assistants (docs/team-work.md#department-assistants): the table with each assistant's
// queue, overdue and finished work, "New assistant" from a template, edit, turn off, delete, routing
// a message, and the refused and failed states.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

const directory = (page: Page) => page.getByRole('list', { name: 'Assistant directory' });
const detail = (page: Page) => page.locator('.asDetail');

test('the directory shows each assistant with its queue, the figures add them up and the detail explains its limits', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await expect(page.getByRole('heading', { level: 1, name: 'Assistants' })).toBeVisible();
  const card = directory(page).getByRole('button', { name: /Sales assistant/ });
  await expect(card).toContainText('Sales');
  await expect(card).toContainText('Suggest, a person approves');
  await expect(card).toContainText('1 open');
  const band = page.getByRole('region', { name: 'Department assistants at a glance' });
  await expect(band).toContainText('Switched on');
  await expect(band).toContainText('Of 1 assistant');
  await expect(band).toContainText('In their queues');
  await expect(detail(page)).toContainText('Takes new messages from: WhatsApp');
  await expect(detail(page)).toContainText('Does not send, post or call outside ANUM without a person\'s approval');
  await expect(detail(page).getByRole('button', { name: 'Draft a quote for Al Noor fleet' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a new assistant starts from a department template in the wizard', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await page.getByRole('button', { name: 'New assistant' }).click();
  const form = page.getByRole('form', { name: 'New assistant' });
  await expect(form.getByRole('listitem').filter({ hasText: 'Sales' }).getByRole('button')).toBeDisabled();
  await form.getByRole('listitem').filter({ hasText: 'Support' }).getByRole('button', { name: 'Use template' }).click();
  await form.getByRole('button', { name: 'Next' }).click();
  await form.getByRole('checkbox', { name: 'Email' }).click();
  await form.getByRole('button', { name: 'Next' }).click();
  await expect(form.getByRole('radio', { name: /Suggest, a person approves/ })).toBeVisible();
  await form.getByRole('button', { name: 'Next' }).click();
  await form.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('support assistant was saved.')).toBeVisible();
  await expect(detail(page)).toContainText('Takes new messages from: Email');
});

test('edit, turn off and delete ask the API and say what happened', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await page.getByRole('button', { name: 'Edit Sales assistant' }).click();
  const form = page.getByRole('form', { name: 'Edit assistant' });
  await form.getByLabel('Name', { exact: true }).fill('Quotes desk');
  for (let step = 0; step < 3; step += 1) await form.getByRole('button', { name: 'Next' }).click();
  await form.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Quotes desk was saved.')).toBeVisible();
  await page.getByRole('button', { name: 'Turn off Quotes desk' }).click();
  await expect(directory(page).getByRole('button', { name: /Quotes desk/ })).toContainText('Off');
  await page.getByRole('button', { name: 'Delete Quotes desk' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this assistant?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('heading', { name: 'No assistants yet' })).toBeVisible();
});

test('the Work tab lists work routed to assistants and opens an item', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistants');
  await page.getByRole('tab', { name: 'Work' }).click();
  const table = page.getByRole('table');
  await expect(table.getByRole('row', { name: /Draft a quote for Al Noor fleet/ })).toContainText('Sales assistant');
  await expect(table.getByRole('row')).toHaveCount(2);
  await page.getByLabel('Status').selectOption('done');
  await expect(page.getByRole('heading', { name: 'No work for assistants here' })).toBeVisible();
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
  await expect(page.getByRole('list', { name: 'Assistant directory' })).toHaveCount(0);
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
