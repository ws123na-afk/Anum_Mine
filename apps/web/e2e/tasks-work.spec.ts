import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// My tasks and Team tasks on the Tasks page and the shared Tasks tabs (docs/tasks.md#my-tasks-and-team-tasks):
// people's work items grouped by urgency, the detail beside them, Mark done, New task, and the Approvals tab.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const tabs = (page: Page) => page.getByRole('tablist', { name: 'Tasks sections' });

test('My tasks groups work by urgency, opens the detail and marks a task done', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await tabs(page).getByRole('tab', { name: 'My tasks' }).click();
  await expect(page.getByRole('region', { name: 'My tasks at a glance' })).toContainText('Overdue1');
  const table = page.getByRole('table', { name: 'My open tasks' });
  await expect(table.getByRole('button', { name: /Overdue/ })).toHaveAttribute('aria-expanded', 'true');
  await expect(table.getByRole('row', { name: /Count winter tyre stock/ })).toContainText('late');
  await table.getByRole('button', { name: /Count winter tyre stock/ }).click();
  await expect(page.getByRole('complementary', { name: 'Count winter tyre stock' })).toBeVisible();
  if (await page.getByRole('button', { name: 'Back to the list' }).isVisible()) await page.getByRole('button', { name: 'Back to the list' }).click();
  await table.getByRole('checkbox', { name: 'Mark "Count winter tyre stock" done' }).click();
  await expect(page.getByText('Marked "Count winter tyre stock" done.')).toBeVisible();
  await expect(table.getByRole('button', { name: /Completed this week/ })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('the tab is remembered, filters narrow the table and Team tasks shows everyone', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await tabs(page).getByRole('tab', { name: 'Team tasks' }).click();
  const table = page.getByRole('table', { name: "Everyone's tasks" });
  await expect(table.getByRole('button', { name: /Count winter tyre stock/ })).toBeVisible();
  await page.getByLabel('Priority').selectOption('low');
  await expect(page.getByRole('heading', { name: 'Nothing matches these filters' }).or(page.getByRole('heading', { name: /match/ }))).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).first().click();
  await expect(table.getByRole('button', { name: /Count winter tyre stock/ })).toBeVisible();
  await page.reload();
  await expect(tabs(page).getByRole('tab', { name: 'Team tasks' })).toHaveAttribute('aria-selected', 'true');
});

test('Approvals is a Tasks tab: it opens the inbox, and the inbox tabs lead back', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await tabs(page).getByRole('tab', { name: /Approvals/ }).click();
  await expect(page).toHaveURL(/#approvals$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Approvals at a glance' })).toContainText('Pending');
  await tabs(page).getByRole('tab', { name: 'My tasks' }).click();
  await expect(page).toHaveURL(/#tasks$/);
  await expect(tabs(page).getByRole('tab', { name: 'My tasks' })).toHaveAttribute('aria-selected', 'true');
});

test('an empty list offers New task, which creates one', async ({ page }) => {
  await mockWorkspaceApi(page, { emptyWork: true });
  await page.addInitScript(() => sessionStorage.setItem('anum.tasks.tab', 'mine'));
  await page.goto('/#tasks');
  await expect(page.getByRole('heading', { name: 'Nothing on your list' })).toBeVisible();
  await page.getByRole('button', { name: 'New task' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New task' });
  await dialog.getByLabel('Title', { exact: true }).fill('Order 40 tyres');
  await dialog.getByRole('button', { name: 'Create and notify' }).click();
  await expect(page.getByRole('complementary', { name: 'Order 40 tyres' })).toBeVisible();
});

test('My tasks reads right to left in Arabic', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockWorkspaceApi(page);
  await page.addInitScript(() => sessionStorage.setItem('anum.tasks.tab', 'mine'));
  await page.goto('/#tasks');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(tabs(page).or(page.getByRole('tablist', { name: 'أقسام المهام' }))).toBeVisible();
  await expect(page.getByRole('tab', { name: 'مهامي' })).toHaveAttribute('aria-selected', 'true');
  expect(await noHorizontalOverflow(page)).toBe(true);
});
