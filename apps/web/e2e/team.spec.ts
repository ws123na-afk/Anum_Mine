import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Team work, department assistants, notifications and the platform console against a mocked API
// (docs/team-work.md, docs/customer-organisations.md).
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('Ask ANUM assigns work from a sentence, after a preview, and opens it', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await expect(page.getByRole('heading', { level: 1, name: 'Team' })).toBeVisible();
  await page.getByLabel('Say who should do what, and by when').fill('ANUM, ask Ahmed to call the supplier by Thursday');
  await page.getByRole('button', { name: 'Check' }).click();
  await expect(page.getByText('Ahmed Saleh').first()).toBeVisible();
  await page.getByRole('button', { name: 'Assign and notify' }).click();
  await expect(page.getByText('"Call the supplier" was assigned to Ahmed Saleh, who has been notified.')).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Call the supplier' })).toBeVisible();
  await expect(page.getByText('assigned through ANUM')).toBeVisible();
});

test('an unknown person is explained and nothing is created', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await page.getByLabel('Say who should do what, and by when').fill('ask Khalid to count tyres');
  await page.getByRole('button', { name: 'Check' }).click();
  await expect(page.getByText(/Nobody in this workspace is called Khalid/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Assign and notify' })).toBeDisabled();
});

test('my tasks: list, detail, checklist, comment and status change', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  const row = page.getByRole('button', { name: /Count winter tyre stock/ });
  await expect(row).toBeVisible();
  await expect(row.getByText(/Overdue since/)).toBeVisible();
  await row.click();
  const detail = page.getByRole('complementary', { name: 'Count winter tyre stock' });
  await expect(detail.getByRole('heading', { name: 'Count winter tyre stock' })).toBeFocused();
  await detail.getByRole('checkbox', { name: 'Warehouse B' }).click();
  await expect(detail.getByRole('checkbox', { name: 'Warehouse B' })).toBeChecked();
  await detail.getByLabel('Add a comment').fill('Warehouse B done too.');
  await detail.getByRole('button', { name: 'Post' }).click();
  await expect(detail.getByText('Warehouse B done too.')).toBeVisible();
  await detail.getByRole('combobox', { name: /^Status/ }).selectOption('in_progress');
  await expect(detail.getByText('In progress', { exact: true }).first()).toBeVisible();
  await detail.getByRole('button', { name: 'Back to the list' }).click();
  await expect(page.getByRole('complementary', { name: 'Count winter tyre stock' })).toHaveCount(0);
});

test('new work form validates, then creates', async ({ page }) => {
  await mockWorkspaceApi(page, { emptyWork: true });
  await page.goto('/#team');
  await expect(page.getByRole('heading', { name: 'Nothing assigned to you' })).toBeVisible();
  await page.getByRole('button', { name: 'New work item' }).first().click();
  await page.getByRole('button', { name: 'Create and notify' }).click();
  await expect(page.getByText('Give the work a title.')).toBeVisible();
  await page.getByLabel('Title', { exact: true }).fill('Order 40 tyres from Bridgestone');
  await page.getByRole('combobox', { name: /^Assigned to/ }).selectOption('user:user_ahmed');
  await page.getByLabel('Checklist').fill('Get quote\nConfirm delivery date');
  await page.getByRole('button', { name: 'Create and notify' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Order 40 tyres from Bridgestone' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Confirm delivery date' })).toBeVisible();
});

test('a refused change says who may make it', async ({ page }) => {
  await mockWorkspaceApi(page, { denyEdit: true });
  await page.goto('/#team');
  await page.getByRole('button', { name: /Count winter tyre stock/ }).click();
  const detail = page.getByRole('complementary', { name: 'Count winter tyre stock' });
  await expect(detail.getByText(/Only the creator, the assignee or a workspace owner can change this item/)).toBeVisible();
  await expect(detail.getByRole('combobox', { name: /^Status/ })).toBeDisabled();
  await detail.getByLabel('Add a comment').fill('Can I help?');
  await detail.getByRole('button', { name: 'Post' }).click();
  await expect(detail.getByRole('alert')).toContainText('workspace owner can change it');
});

test('board, workload and activity tabs', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  // The board and the workload share the Team dashboard tab.
  await page.getByRole('navigation', { name: 'Team sections' }).getByRole('button', { name: 'Team dashboard' }).click();
  await expect(page.getByRole('heading', { level: 3, name: /In progress/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Draft a quote for Al Noor fleet/ })).toBeVisible();
  await expect(page.getByRole('meter', { name: 'Omar Owner: 85% of weekly capacity' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Team sections' }).getByRole('button', { name: 'Activity' }).click();
  await expect(page.getByText(/Ahmed Saleh created "Count winter tyre stock"/)).toBeVisible();
  await page.getByRole('navigation', { name: 'Team sections' }).getByRole('button', { name: 'People' }).click();
  await expect(page.getByText(/No team profile yet/)).toBeVisible();
});

test('the notifications menu lists my team notifications and opens the work item', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistant');
  const bell = page.getByRole('button', { name: /^Notifications, \d+ waiting$/ });
  await bell.click();
  await expect(page.getByRole('heading', { level: 3, name: 'Team notifications' })).toBeVisible();
  const item = page.getByRole('button', { name: /New work: Count winter tyre stock/ });
  await expect(item).toBeVisible();
  // Read the count once the team notifications have loaded (they count towards it).
  const before = Number((await bell.getAttribute('aria-label'))?.match(/\d+/)?.[0]);
  await item.click();
  await expect(page).toHaveURL(/#team$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Count winter tyre stock' })).toBeVisible();
  // Reading it lowers the count by one.
  await expect(page.getByRole('button', { name: `Notifications, ${before - 1} waiting` })).toBeVisible();
});

test('platform console: onboard a customer company and see the one-time invitation', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#organisations');
  // The organisations table (the mail health pane below lists the same organisations).
  const orgsTable = page.locator('.orgsTable');
  await expect(orgsTable.getByRole('rowheader', { name: /^Gulf Tyres/ })).toBeVisible();
  await expect(orgsTable.getByRole('row', { name: /^Gulf Tyres/ }).getByText('Needs attention')).toBeVisible();
  await page.getByRole('button', { name: 'Onboard a customer company' }).click();
  await page.getByLabel('Organisation name').fill('Acme Tyres');
  await expect(page.getByLabel('Organisation id')).toHaveValue('acme_tyres');
  await page.getByLabel('First admin\'s email').fill('nope');
  await page.getByRole('button', { name: 'Create organisation' }).click();
  await expect(page.getByText('Enter the first admin\'s email address.')).toBeVisible();
  await page.getByLabel('First admin\'s email').fill('admin@acme.example');
  await page.getByRole('button', { name: 'Create organisation' }).click();
  await expect(page.getByRole('heading', { name: 'Invitation for the first admin of Acme Tyres' })).toBeVisible();
  await expect(page.getByLabel('Invitation link')).toHaveValue(/#invitation=anum_inv_example&workspace=acme_tyres_main&tenant=acme_tyres$/);
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(orgsTable.getByRole('rowheader', { name: /^Acme Tyres/ })).toBeVisible();
});

test('platform console lists deleted organisations as read-only receipts', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#organisations');
  const pane = page.getByRole('region', { name: 'Deleted organisations' });
  await expect(pane.getByRole('rowheader', { name: /^Old Traders/ })).toBeVisible();
  await expect(pane.getByText('52 rows, 2 files')).toBeVisible();
  await expect(pane.getByText('3 removed, 1 kept for their other organisations')).toBeVisible();
  await expect(pane.getByRole('row', { name: /^Leaving Co/ }).getByText('Being deleted').first()).toBeVisible();
  await pane.getByText('Rows per table (2)').click();
  await expect(pane.getByText('work_items')).toBeVisible();
  // Read only: nothing in the pane changes anything.
  await expect(pane.getByRole('button')).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('deleted organisations: empty state', async ({ page }) => {
  await mockWorkspaceApi(page, { deletedOrgs: 'empty' });
  await page.goto('/#organisations');
  await expect(page.getByRole('heading', { name: 'No organisation you onboarded has been deleted.' })).toBeVisible();
});

test('deleted organisations: error state with a retry', async ({ page }) => {
  await mockWorkspaceApi(page, { deletedOrgs: 'fail' });
  await page.goto('/#organisations');
  await expect(page.getByText(/Couldn't load deleted organisations/)).toBeVisible();
});

test('platform console explains who may use it', async ({ page }) => {
  await mockWorkspaceApi(page, { denyOrganisations: true });
  await page.goto('/#organisations');
  await expect(page.getByRole('heading', { name: 'Platform operators only' })).toBeVisible();
  await expect(page.getByText(/realm role platform_operator/).first()).toBeVisible();
});

test('the switcher offers my other organisations', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await page.getByRole('button', { name: /tenant_local \/ workspace_foundation/ }).click();
  await expect(page.getByRole('list', { name: 'Your other organisations' }).getByRole('button', { name: /Gulf Tyres/ })).toBeVisible();
});

test('Arabic: the team page is right to left without overflow', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#team');
  await expect(page.getByRole('heading', { level: 1, name: 'الفريق' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'اطلب من أنعم إسناد عمل' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
