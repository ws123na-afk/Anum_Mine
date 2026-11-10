import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Tasks and Approvals when their lists cannot be read. They showed the shell's raw banner
// ("ANUM API request failed: 403: ...", "Failed to fetch") with no way to retry, and the Tasks
// composer said "Loading live workspace state..." for ever. Each page now says what failed and how
// to fix it: a retry for an outage, the role card for a 403 (docs/web-localization.md#error-sentences).

type Failure = 500 | 403 | 'offline';
const TASKS = /\/api\/v1\/tasks(\/summary)?(\?.*)?$/;
const APPROVALS = /\/api\/v1\/approvals(\/page|\/summary)?(\?.*)?$/;
const SNAPSHOT = /\/api\/v1\/(tasks|approvals)(\?.*)?$/;

/** Fails GET requests that match `pattern` until the returned `restore()` is called. */
async function failing(page: Page, pattern: RegExp, failure: Failure) {
  let on = true;
  await page.route(pattern, async (route) => {
    if (!on || route.request().method() !== 'GET') return route.fallback();
    if (failure === 'offline') return route.abort('internetdisconnected');
    return route.fulfill({ status: failure, contentType: 'application/json', body: JSON.stringify({ error: {
      code: failure === 403 ? 'forbidden' : 'internal_error', message: failure === 403 ? 'You do not have permission to do this.' : 'The database is not reachable.',
      correlation_id: 'corr_example', details: [] } }) });
  });
  return () => { on = false; };
}

const noRawError = async (page: Page) => {
  await expect(page.getByText(/ANUM API request failed/)).toHaveCount(0);
  await expect(page.getByText(/Failed to fetch/)).toHaveCount(0);
  await expect(page.locator('main').getByText('..', { exact: false }).filter({ hasText: /[a-z]\.\.(\s|$)/ })).toHaveCount(0);
};

test('Tasks: an outage says what failed, Try again loads the list, and the status line stops loading', async ({ page }) => {
  await mockWorkspaceApi(page);
  const restore = await failing(page, TASKS, 500);
  await page.goto('/#tasks');
  const row = page.locator('.taskErrorRow');
  await expect(row).toContainText('Could not load tasks: The database is not reachable.');
  await noRawError(page);
  await expect(page.getByText('Loading live workspace state...')).toHaveCount(0);
  await expect(page.getByText('Live workspace state is unavailable.')).toBeVisible();
  restore();
  await row.getByRole('button', { name: 'Try again' }).click();
  await expect(row).toHaveCount(0);
  await expect(page.locator('.taskRow').first()).toBeVisible();
  // The same press reloaded the shell's copy, so the status line is current again.
  await expect(page.getByText('Workspace state is current.')).toBeVisible();
});

test('Tasks: with no connection the sentence is ours, not the browser\'s, in English and Arabic', async ({ page }) => {
  await mockWorkspaceApi(page);
  await failing(page, TASKS, 'offline');
  await page.goto('/#tasks');
  await expect(page.locator('.taskErrorRow')).toContainText('Could not load tasks: Could not reach the server.');
  await noRawError(page);
  await useSavedLocale(page, 'ar');
  await page.reload();
  await expect(page.locator('.taskErrorRow')).toContainText('تعذّر الوصول إلى الخادم.');
  await expect(page.locator('.taskErrorRow')).not.toContainText(/[A-Za-z]{4,}/);
});

test('Tasks: a 403 shows who can open the list instead of an error to retry', async ({ page }) => {
  await mockWorkspaceApi(page);
  await failing(page, TASKS, 403);
  await page.goto('/#tasks');
  const card = page.locator('.paneDenied');
  await expect(card.getByRole('heading', { name: 'You cannot see agent tasks' })).toBeVisible();
  await expect(card).toContainText('Ask an owner or admin to change your role.');
  await expect(page.locator('.taskErrorRow')).toHaveCount(0);
  await expect(page.locator('main').getByRole('button', { name: 'Try again' })).toHaveCount(0);
  await expect(page.getByText('Loading live workspace state...')).toHaveCount(0);
  await noRawError(page);
});

test('Approvals: an outage shows one error with Retry and no request to decide; Retry loads the queue', async ({ page }) => {
  await mockWorkspaceApi(page);
  const restore = await failing(page, APPROVALS, 500);
  await page.goto('/#approvals');
  const error = page.locator('.panelError');
  await expect(error).toContainText('Could not load approvals: The database is not reachable.');
  await expect(page.locator('main').getByRole('alert')).toHaveCount(1);
  await expect(page.locator('main').getByRole('button', { name: /^(Approve|Reject)/ })).toHaveCount(0);
  await noRawError(page);
  restore();
  await error.getByRole('button', { name: 'Retry' }).click();
  await expect(error).toHaveCount(0);
  await expect(page.locator('main').getByRole('button', { name: /^Approve/ }).first()).toBeVisible();
});

test('Approvals: a 403 shows the role card and nothing to approve', async ({ page }) => {
  await mockWorkspaceApi(page);
  await failing(page, APPROVALS, 403);
  await page.goto('/#approvals');
  await expect(page.locator('.panelDenied')).toContainText('You cannot see approvals');
  await expect(page.locator('.panelDenied')).toContainText('Ask an owner or admin to change your role.');
  await expect(page.locator('main').getByRole('button', { name: /^(Approve|Reject|Retry)/ })).toHaveCount(0);
  await noRawError(page);
});

test('elsewhere the shell says once what failed, with a Retry that reads the lists again', async ({ page }) => {
  await mockWorkspaceApi(page);
  const restore = await failing(page, SNAPSHOT, 'offline');
  await page.goto('/#settings');
  const problem = page.locator('.paneProblem');
  await expect(problem).toHaveText(/Tasks and approvals could not be loaded: Could not reach the server\. Check your connection, then try again\./);
  await noRawError(page);
  restore();
  await problem.getByRole('button', { name: 'Retry' }).click();
  await expect(problem).toHaveCount(0);
});
