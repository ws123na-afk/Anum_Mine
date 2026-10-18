import { expect, test } from '@playwright/test';
import { CHANGE_HASH, PROJECT_ID, mockBusinessChanges } from './support/businessChangesApi';

// Change orders, the usage ledger and profitability (docs/business.md#change-orders), against a mocked
// API: a change order is drafted, sent and approved by an owner with its content hash; the contract
// total follows; a labour cost turns the margin from "-" into a figure; revenue and cash stay apart.

test('owner drafts, sends and approves a change order; costs give a margin', async ({ page }) => {
  const mock = await mockBusinessChanges(page);
  await page.goto(`/#business-projects/${PROJECT_ID}`);
  const project = page.getByRole('dialog', { name: 'Fleet reporting pilot' });
  const profit = project.getByRole('region', { name: 'Profitability' });
  await expect(profit.getByText('Revenue before tax')).toBeVisible();
  await expect(profit.getByRole('definition').filter({ hasText: /^-$/ }).first()).toBeVisible();  // nothing collected or recorded yet
  await expect(project.getByRole('heading', { name: 'No change orders' })).toBeVisible();
  await expect(project.getByRole('heading', { name: 'No costs recorded' })).toBeVisible();

  await project.getByRole('button', { name: 'New change order' }).click();
  const form = page.getByRole('form', { name: 'New change order' });
  await form.getByLabel('What changes').fill('Fuel dashboard');
  await form.getByLabel('Asked by').fill('Sara Khan');
  await form.getByLabel('Item').fill('Fuel dashboard');
  await form.getByLabel('Unit price').fill('800');
  await form.getByLabel(/^Timeline change/).fill('10');
  await form.getByRole('button', { name: 'Save draft' }).click();
  const changes = project.getByRole('list', { name: 'Change orders' });
  await expect(changes.getByText('Change order 1: Fuel dashboard')).toBeVisible();
  await expect(changes.getByText(/Price impact \+SAR\s?800\.00/)).toBeVisible();
  await expect(changes.getByText('Timeline: 10 days later')).toBeVisible();

  await changes.getByRole('button', { name: 'Send for approval' }).click();
  const approval = changes.getByRole('group', { name: 'Approval of change order 1' });
  await expect(approval.getByText(/binds this exact change to version 1/)).toBeVisible();
  await approval.getByRole('button', { name: 'Approve change' }).click();
  await expect(project.getByText('Change order 1 approved. The contract has a new version.')).toBeVisible();
  await expect(changes.getByText('Approved', { exact: true })).toBeVisible();
  const approve = mock.calls.find((call) => call.path.endsWith('/approve'));
  expect((approve?.body as { content_hash: string }).content_hash).toBe(CHANGE_HASH);
  await expect(project.getByText('Change order 1: Fuel dashboard').last()).toBeVisible();  // the new milestone
  await expect(profit.getByText(/Contract total \(version 2\)/)).toBeVisible();
  await expect(profit.getByText(/SAR\s?3,300\.00/).first()).toBeVisible();

  const ledger = project.getByRole('form', { name: 'Record a cost' });
  await ledger.getByLabel('Description').fill('Build the dashboard');
  await ledger.getByLabel('Hours').fill('7.5');
  await ledger.getByLabel('Hourly cost').fill('80');
  await expect(ledger.getByText(/Amount: SAR\s?600\.00/)).toBeVisible();
  await ledger.getByRole('button', { name: 'Record a cost' }).click();
  await expect(project.getByText('Cost recorded.')).toBeVisible();
  await expect(project.getByRole('row', { name: /Build the dashboard/ })).toBeVisible();
  await expect(profit.getByText(/SAR\s?2,700\.00/)).toBeVisible();  // 3,300 revenue minus 600 labour

  // The AI import refuses a month with no recorded usage and says why.
  await project.getByRole('form', { name: 'AI usage from the model budget' }).getByRole('button', { name: 'Import AI usage' }).click();
  await expect(project.getByText(/No model usage was recorded for this workspace/)).toBeVisible();
});

test('a member sends a change order but an owner decides it', async ({ page }) => {
  await mockBusinessChanges(page, { role: 'member' });
  await page.goto(`/#business-projects/${PROJECT_ID}`);
  const project = page.getByRole('dialog', { name: 'Fleet reporting pilot' });
  await project.getByRole('button', { name: 'New change order' }).click();
  const form = page.getByRole('form', { name: 'New change order' });
  await form.getByRole('button', { name: 'Save draft' }).click();
  await expect(form.getByText('Say what changes (at least 2 characters).')).toBeVisible();
  await form.getByLabel('What changes').fill('Training day');
  await form.getByLabel('Item').fill('Training');
  await form.getByLabel('Unit price').fill('400');
  await form.getByRole('button', { name: 'Save draft' }).click();
  await project.getByRole('button', { name: 'Send for approval' }).click();
  await expect(project.getByText('A workspace owner approves or rejects it.')).toBeVisible();
  await expect(project.getByRole('button', { name: 'Approve change' })).toHaveCount(0);
  await expect(project.getByRole('button', { name: /^Delete / })).toHaveCount(0);
});

test('a failed change order list says what failed and offers a retry', async ({ page }) => {
  await mockBusinessChanges(page, { failChanges: true });
  await page.goto(`/#business-projects/${PROJECT_ID}`);
  const project = page.getByRole('dialog', { name: 'Fleet reporting pilot' });
  await expect(project.getByText(/Loading the change orders failed: Database unavailable/)).toBeVisible();
  await expect(project.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
});
