import { expect, test, type Page } from '@playwright/test';
import { makeTask, mockApi } from './tasks-mock';
import { useSavedLocale } from './support/workspaceApi';

// The Tasks module end to end against a stateful API mock (docs/tasks.md): server-side filters
// and keyset paging, run history with retry, edit, archive and restore, bulk actions, the
// run-log export, the activity feed, and the empty, error and permission-denied states.
const history = (page: Page) => page.getByRole('region', { name: /Task history|Archived tasks/ });
const detail = (page: Page) => page.getByRole('region', { name: /Task detail/ }).or(page.locator('section.taskDetail'));
const toolbar = (page: Page) => page.getByRole('toolbar', { name: 'Task actions' });

test('filters, search and paging run on the server', async ({ page }) => {
  const many = Array.from({ length: 55 }, (_, i) => makeTask(i + 1, { title: i % 2 ? `Invoice ${i + 1}` : `Report ${i + 1}` }));
  const { seen } = await mockApi(page, { tasks: many });
  await page.goto('/#tasks');
  const list = history(page);
  await expect(list.getByRole('button', { name: /Report 55/ })).toBeVisible();
  await expect(list.getByRole('heading', { name: '50 of 55 tasks' })).toBeVisible();
  await list.getByRole('button', { name: 'Load more tasks' }).click();
  await expect(list.getByRole('heading', { name: '55 of 55 tasks' })).toBeVisible();
  expect(seen.lists.some((q) => q.get('cursor') === 'task_006')).toBe(true);

  await list.getByLabel('Search tasks').fill('invoice');
  await expect.poll(() => seen.lists.some((q) => q.get('q') === 'invoice')).toBe(true);
  await expect(list.getByRole('button', { name: /Report/ })).toHaveCount(0);
  await list.getByLabel('Status').selectOption('failed');
  await expect(list.getByRole('heading', { name: 'No matching tasks' })).toBeVisible();
  await expect.poll(() => seen.lists.some((q) => q.getAll('status').join() === 'failed' && q.get('q') === 'invoice')).toBe(true);
  await list.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(list.getByRole('button', { name: /Report 55/ })).toBeVisible();
  await expect(list.getByLabel('Search tasks')).toHaveValue('');
});

test('retrying a failed task adds a run and keeps the history', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#tasks');
  await history(page).getByRole('button', { name: /Quarterly invoice summary/ }).click();
  await expect(page.getByRole('heading', { name: 'Quarterly invoice summary' })).toBeVisible();
  const tab = (name: RegExp) => page.getByRole('tab', { name });
  await tab(/^Runs/).click();
  await expect(page.getByText('Failure in run_task_001_1')).toBeVisible();
  await toolbar(page).getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByText('The run is completed.')).toBeVisible();
  const runs = page.getByRole('list', { name: 'Runs, newest first' });
  await expect(runs.getByRole('button', { name: /Run 2 \(latest\)/ })).toHaveAttribute('aria-pressed', 'true');
  await tab(/^Answer/).click();
  await expect(page.getByRole('region', { name: 'Answer' }).getByText('Answer from run_task_001_2')).toBeVisible();
  await tab(/^Runs/).click();
  await runs.getByRole('button', { name: /Run 1/ }).click();
  await expect(page.getByText('Failure in run_task_001_1')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Execution steps' }).getByText('workspace.summarize').first()).toBeVisible();
  await tab(/^Activity/).click();
  const activity = page.getByRole('region', { name: 'Activity' });
  await expect(activity.getByText('Retry requested')).toBeVisible();
});

test('edit a task, archive it and restore it from the archived list', async ({ page, isMobile }) => {
  const { seen } = await mockApi(page);
  await page.goto('/#tasks');
  await history(page).getByRole('button', { name: /Draft plan/ }).click();
  await toolbar(page).getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('textbox', { name: 'Title', exact: true })).toBeFocused();
  await page.getByRole('textbox', { name: 'Title', exact: true }).fill('Weekly plan');
  await page.getByRole('textbox', { name: 'Instructions', exact: true }).fill('Plan the week with owners');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('heading', { name: 'Weekly plan' })).toBeVisible();
  expect(seen.updates).toEqual([{ title: 'Weekly plan', prompt: 'Plan the week with owners' }]);

  await toolbar(page).getByRole('button', { name: 'Archive' }).click();
  // The detail badge also reads "Task archived" while the task is still open: check the notice.
  await expect(page.getByRole('status').filter({ hasText: /Task archived\./ })).toBeVisible();
  // On a narrow screen the detail replaces the list until Back.
  if (isMobile) await page.getByRole('button', { name: 'Back to the list' }).click();
  await expect(history(page).getByRole('button', { name: /Weekly plan/ })).toHaveCount(0);
  await history(page).getByLabel(/Show archived/).check();
  await expect(page.getByRole('region', { name: 'Archived tasks' })).toBeVisible();
  await history(page).getByRole('button', { name: /Weekly plan/ }).click();
  await expect(detail(page).getByText('archived', { exact: true })).toBeVisible();
  await expect(toolbar(page).getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await toolbar(page).getByRole('button', { name: 'Restore' }).click();
  await expect(page.getByText('Task restored to the active list.')).toBeVisible();
});

test('instructions are locked once a task ran', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#tasks');
  await history(page).getByRole('button', { name: /Launch checklist/ }).click();
  await toolbar(page).getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('textbox', { name: 'Instructions', exact: true })).toBeDisabled();
  await expect(page.getByText(/Instructions are fixed once a task has run/)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('textbox', { name: 'Title', exact: true })).toHaveCount(0);
});

test('bulk archive reports what it could not do', async ({ page }) => {
  const { seen } = await mockApi(page, { tasks: [makeTask(1, { title: 'Done one' }), makeTask(2, { title: 'Busy one', status: 'running' })] });
  await page.goto('/#tasks');
  await history(page).getByLabel('Select all loaded').check();
  const bar = page.getByRole('toolbar', { name: 'Bulk actions' });
  await expect(bar.getByText('2 selected')).toBeVisible();
  await bar.getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByText('Archived 1 task; could not archive 1 task: Cancel the task or let it finish before archiving it')).toBeVisible();
  expect(seen.bulk).toEqual([{ action: 'archive', task_ids: ['task_002', 'task_001'] }]);
  await expect(bar.getByText('1 selected')).toBeVisible(); // the refused task stays selected
});

test('export downloads the run log as JSON and Markdown', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#tasks');
  await history(page).getByRole('button', { name: /Launch checklist/ }).click();
  const json = page.waitForEvent('download');
  await toolbar(page).getByRole('button', { name: 'Export JSON' }).click();
  expect((await json).suggestedFilename()).toBe('task_002-run-log.json');
  const markdown = page.waitForEvent('download');
  await toolbar(page).getByRole('button', { name: 'Export Markdown' }).click();
  expect((await markdown).suggestedFilename()).toBe('task_002-run-log.md');
  await expect(page.getByText('Saved task_002-run-log.md.')).toBeVisible();
});

test('a viewer sees why actions are unavailable and a refusal names who can act', async ({ page }) => {
  await mockApi(page, { role: 'viewer' });
  await page.goto('/#tasks');
  await expect(page.getByRole('button', { name: 'Run task' })).toBeDisabled();
  await expect(page.getByText(/Running and retrying tasks needs the member or owner role\. You can read every task/)).toBeVisible();
  await history(page).getByRole('button', { name: /Quarterly invoice summary/ }).click();
  await expect(toolbar(page).getByRole('button', { name: 'Retry' })).toBeDisabled();
  await expect(toolbar(page).getByRole('button', { name: 'Archive' })).toBeDisabled();
  await expect(page.getByText(/Only the task's creator or a workspace owner can edit or archive it\./)).toBeVisible();
  // Export stays available: reading a task's history needs only read access.
  await expect(toolbar(page).getByRole('button', { name: 'Export JSON' })).toBeEnabled();
});

test('a member editing someone else\'s task is told who can', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#tasks');
  await history(page).getByRole('button', { name: /Launch checklist/ }).click();
  await expect(toolbar(page).getByRole('button', { name: 'Edit' })).toBeDisabled();
  await expect(toolbar(page).getByRole('button', { name: 'Cancel' })).toHaveCount(0);
  await expect(page.getByText(/Only the task's creator or a workspace owner can edit or archive it\./)).toBeVisible();
});

test('a failed list load says what failed and retries', async ({ page }) => {
  const api = await mockApi(page, { failSearch: true });
  await page.goto('/#tasks');
  await history(page).getByLabel('Search tasks').fill('launch');
  const alert = history(page).getByRole('alert');
  await expect(alert).toContainText('Could not load tasks: The server could not reach its database');
  api.recover();
  await alert.getByRole('button', { name: 'Try again' }).click();
  await expect(history(page).getByRole('button', { name: /Launch checklist/ })).toBeVisible();
  await expect(history(page).getByRole('alert')).toHaveCount(0);
});

test('the task history, actions and run history read in Arabic, right to left', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#tasks');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('region', { name: 'سجل المهام' }).getByRole('button', { name: /Quarterly invoice summary/ }).click();
  const actions = page.getByRole('toolbar', { name: 'إجراءات المهمة' });
  await actions.getByRole('button', { name: 'إعادة المحاولة' }).click();
  await page.getByRole('tab', { name: /^مرات التشغيل/ }).click();
  await expect(page.getByRole('list', { name: 'مرات التشغيل، الأحدث أولًا' }).getByRole('button', { name: /التشغيل 2 \(الأحدث\)/ })).toBeVisible();
  await page.getByRole('tab', { name: /^النشاط/ }).click();
  await expect(page.getByRole('region', { name: 'النشاط' }).getByText('طُلبت إعادة المحاولة')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});

test('an empty workspace points at the composer', async ({ page }) => {
  await mockApi(page, { tasks: [] });
  await page.goto('/#tasks');
  await expect(page.getByRole('heading', { name: 'No tasks yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Write instructions' }).click();
  await expect(page.getByLabel('Task instructions')).toBeFocused();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
