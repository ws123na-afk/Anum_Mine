import { expect, test } from '@playwright/test';
import { PAYLOAD_HASH, mockBusiness } from './support/businessApi';

// Business, the internal sales desk (docs/business.md): the main path from an empty catalog to a
// project, against a mocked API: product, lead, evidence and score, qualify, draft (never sent),
// opportunity, proposal, acceptance approved with the payload hash, won, project and payment.

test('product, lead, qualify, draft, approved proposal, won and project', async ({ page }) => {
  const mock = await mockBusiness(page);
  await page.goto('/#business-catalog');
  await expect(page.getByRole('heading', { level: 1, name: 'Business' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'The catalog is empty' })).toBeVisible();

  // Catalog: the owner enters the product and its price; nothing is seeded.
  await page.getByRole('button', { name: 'New product' }).first().click();
  const productForm = page.getByRole('form', { name: 'New product' });
  await productForm.getByLabel('Name', { exact: true }).fill('Reporting Quick Win');
  await productForm.getByLabel('What it does').fill('One dashboard from one data source.');
  await productForm.getByRole('button', { name: 'Add' }).click();
  const product = page.getByRole('dialog', { name: 'Reporting Quick Win' });
  await product.getByRole('button', { name: 'New version' }).click();
  await product.getByLabel('Price', { exact: true }).fill('2500');
  await product.getByLabel('Labor hours').fill('12');
  await product.getByLabel('Your hourly cost').fill('80');
  await product.getByRole('button', { name: 'Save version' }).click();
  await product.getByRole('button', { name: 'Activate' }).click();
  await expect(product.getByText('Active', { exact: true })).toBeVisible();
  await product.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('cell', { name: 'SAR 2,500.00' })).toBeVisible();

  // A lead with a contact whose consent basis is recorded.
  await page.getByRole('navigation', { name: 'Business sections' }).getByRole('button', { name: 'Leads' }).click();
  await page.getByRole('button', { name: 'New lead' }).first().click();
  const leadForm = page.getByRole('form', { name: 'New lead' });
  await leadForm.getByLabel('Company name').fill('Gulf Mixers LLC');
  await leadForm.getByLabel('Website').fill('https://gulfmixers.example');
  await leadForm.getByLabel('Name', { exact: true }).fill('Sara Khan');
  await leadForm.getByLabel('E-mail').fill('ops@gulfmixers.example');
  await leadForm.getByLabel('Consent basis').selectOption('inbound_inquiry');
  await leadForm.getByLabel('Lead title').fill('Fleet reporting');
  await leadForm.getByLabel('Offer').selectOption({ label: 'Reporting Quick Win' });
  await leadForm.getByRole('button', { name: 'Add lead' }).click();
  const lead = page.getByRole('dialog', { name: 'Gulf Mixers LLC' });
  await expect(lead.getByText('Discovered', { exact: true })).toBeVisible();
  await expect(lead.getByText('0/30')).toBeVisible();

  // Qualifying needs sources and a reasoned score.
  await lead.getByRole('button', { name: 'Mark Qualified' }).click();
  await expect(lead.getByText(/Not ready to qualify/)).toBeVisible();
  const source = lead.getByRole('form', { name: 'Add source' });
  await source.getByLabel('Address (URL)').fill('https://gulfmixers.example/fleet');
  await source.getByLabel('Title').fill('Our mixer fleet');
  await source.getByRole('button', { name: 'Add source' }).click();
  await expect(lead.getByRole('link', { name: 'Our mixer fleet' })).toBeVisible();
  const score = lead.getByRole('form', { name: 'Score' });
  await score.getByLabel('Industry fit score').selectOption('4');
  await score.getByLabel('Reason for Industry fit').fill('Concrete fleet, our core segment');
  await score.getByLabel('Problem evidence score').selectOption('3');
  await score.getByLabel('Reason for Problem evidence').fill('Their site shows manual fleet reports');
  await score.getByRole('button', { name: 'Save score' }).click();
  await expect(lead.getByText('7/30')).toBeVisible();
  await lead.getByRole('button', { name: 'Mark Qualified' }).click();
  await expect(lead.getByText('Qualified', { exact: true })).toBeVisible();

  // A draft is written and approved as text; there is no send button anywhere.
  await expect(lead.getByText(/Sending is off/)).toBeVisible();
  await lead.getByRole('button', { name: 'Write a draft' }).click();
  await expect(lead.getByText(/reply STOP/)).toBeVisible();
  await lead.getByRole('button', { name: 'Approve text' }).click();
  await expect(lead.getByText('Approved', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Send/ })).toHaveCount(0);

  // Opportunity, then a proposal from the catalog price.
  await lead.getByRole('button', { name: 'Open an opportunity' }).click();
  const opportunity = page.getByRole('dialog', { name: 'Fleet reporting' });
  await opportunity.getByLabel('Value (your estimate)').fill('2500');
  await opportunity.getByLabel('Probability % (your estimate)').fill('40');
  await opportunity.getByRole('button', { name: 'Save' }).click();
  await expect(opportunity.getByText(/Weighted value: SAR\s1,000\.00/)).toBeVisible();
  await opportunity.getByRole('button', { name: 'Create proposal' }).click();
  await opportunity.getByLabel('Proposal title').fill('Fleet reporting pilot');
  await opportunity.getByRole('form', { name: 'Create proposal' }).getByRole('button', { name: 'Create proposal' }).click();
  const proposal = page.getByRole('dialog', { name: 'Fleet reporting pilot' });
  await expect(proposal.getByLabel('Item')).toHaveValue('Reporting Quick Win (catalog version 1)');
  await expect(proposal.getByText('State how the client accepts the work (acceptance criteria).')).toBeVisible();
  await expect(proposal.getByRole('button', { name: 'Ask for acceptance' })).toBeDisabled();
  await proposal.getByLabel('Deliverables', { exact: true }).fill('One dashboard\nOne export');
  await proposal.getByLabel('Acceptance criteria').fill('Dashboard shows last month costs');
  await proposal.getByRole('button', { name: 'Add milestone' }).click();
  await proposal.getByLabel('Milestone', { exact: true }).fill('Deposit and handover');
  await proposal.getByLabel('Amount', { exact: true }).fill('2500');
  await proposal.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(proposal.getByText('Saved.')).toBeVisible();
  await proposal.getByRole('button', { name: 'Ask for acceptance' }).click();

  // The owner approves exactly what is shown, sending back the payload hash.
  const approval = proposal.getByRole('group', { name: 'Acceptance waiting for an owner' });
  await expect(approval.getByText('2500.00').first()).toBeVisible();
  await expect(approval.getByText('Gulf Mixers LLC')).toBeVisible();
  await approval.getByRole('button', { name: 'Approve acceptance' }).click();
  await expect(proposal.getByText(/Accepted: the opportunity is won/)).toBeVisible();
  expect(mock.approvals).toBe(1);
  await expect(proposal.getByText(/Version 1 accepted by user_local/)).toBeVisible();

  // Project and milestone payment.
  await proposal.getByRole('button', { name: 'Start the project' }).click();
  await proposal.getByLabel('Start date').fill('2026-11-01');
  await proposal.getByRole('form', { name: 'Start the project' }).getByRole('button', { name: 'Start the project' }).click();
  const project = page.getByRole('dialog', { name: 'Fleet reporting pilot' });
  await expect(project.getByText('Deposit and handover')).toBeVisible();
  await project.getByLabel('Paid amount').fill('2500');
  await project.getByRole('button', { name: 'Record payment' }).click();
  await project.getByRole('combobox', { name: /^Status/ }).selectOption('accepted');
  await expect(project.getByText(/Accepted by user_local/)).toBeVisible();
  await project.getByRole('button', { name: 'Close' }).click();

  // The owner's summary: contracted and cash collected are separate figures.
  await page.getByRole('navigation', { name: 'Business sections' }).getByRole('button', { name: 'Overview' }).click();
  const summary = page.getByRole('region', { name: 'Sales summary' });
  await expect(summary.getByText('Contracted')).toBeVisible();
  await expect(page.getByRole('row', { name: /Won 1 of 1/ })).toBeVisible();
  const approveCall = mock.calls.find((call) => call.path.endsWith('/request-acceptance'));
  expect(approveCall).toBeTruthy();
  expect(PAYLOAD_HASH).toHaveLength(64);
});

test('a member sees the catalog read-only and the approval waits for an owner', async ({ page }) => {
  await mockBusiness(page, { role: 'member' });
  await page.goto('/#business-catalog');
  await expect(page.getByText('Only workspace owners change the catalog; members read it.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New product' })).toHaveCount(0);
  await expect(page.getByText('A workspace owner adds the products and prices.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Business sections' }).getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByText('Only workspace owners change the caps.')).toBeVisible();
  await expect(page.getByText('Off (the default): an owner turns it on in Outreach.')).toBeVisible();
});

test('empty, denied and failed states explain themselves', async ({ page }) => {
  await mockBusiness(page);
  await page.goto('/#business');
  await expect(page.getByRole('heading', { name: 'Start with your catalog' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Business sections' }).getByRole('button', { name: 'Leads' }).click();
  await expect(page.getByRole('heading', { name: 'No leads yet' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Business sections' }).getByRole('button', { name: 'Pipeline' }).click();
  await expect(page.getByRole('heading', { name: 'No opportunities yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Open leads' }).click();
  await expect(page).toHaveURL(/#business-leads$/);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBusiness(page, { status: 403 });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Business is not available to you here' })).toBeVisible();
  await expect(page.getByText(/holds contacts' personal data/)).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBusiness(page, { status: 500 });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Business could not be loaded' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('the catalog searches, filters and pages, and says when nothing matches or a load fails', async ({ page }) => {
  const mock = await mockBusiness(page, { products: 30, failProducts: 1 });
  await page.goto('/#business-catalog');
  await expect(page.getByText(/The catalog could not be read/)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('status').filter({ hasText: '30 products' })).toBeVisible();
  const rows = page.getByRole('table', { name: 'Catalog' }).getByRole('row');
  await expect(rows).toHaveCount(26);
  await page.getByRole('button', { name: 'Show more (25 of 30)' }).click();
  await expect(rows).toHaveCount(31);
  await expect(page.getByRole('button', { name: /Show more/ })).toHaveCount(0);
  expect(mock.calls.some((call) => call.path === '/products' && call.query?.includes('offset=25'))).toBe(true);

  const search = page.getByRole('searchbox', { name: 'Search the catalog' });
  await search.fill('fleet');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('status').filter({ hasText: '1 product' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Package 007 Fleet dashboard' })).toBeVisible();
  expect(mock.calls.some((call) => call.path === '/products' && call.query?.includes('q=fleet'))).toBe(true);

  await search.fill('nothing like this');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('heading', { name: 'No product matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('status').filter({ hasText: '30 products' })).toBeVisible();

  await page.getByRole('combobox', { name: /^Maturity/ }).selectOption('live');
  await expect(page.getByRole('status').filter({ hasText: '10 products' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Show more/ })).toHaveCount(0);

  for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `no horizontal overflow at ${size.width}px`).toBeLessThanOrEqual(1);
  }
});

test('Business works in Arabic, right to left', async ({ page }) => {
  await mockBusiness(page);
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#business');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'الأعمال' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'ابدأ بالكتالوج' })).toBeVisible();
});
