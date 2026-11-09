import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { suggestionsState } from './support/financeSuggestionsApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Suggestions (docs/finance.md#suggestions, #rules-and-learning, #automation-policies): accept a history
// suggestion and undo it, dismiss one, open the page that owns another, save a rule with a preview and apply it to
// earlier rows, restore a version, merchant spellings and subcategories, the automation switches with what is never
// automated, Apply to similar from the Transactions drawer, every state (empty with a next step, error with a retry,
// denied), Arabic and the responsive gate. The API is mocked at http://localhost:8000/** like the other specs; every
// merchant and amount is SYNTHETIC.

const stamp = '2026-11-01T00:00:00Z';

async function mockApi(page: Page, o: { empty?: boolean; fail?: number; denied?: boolean } = {}) {
  const state = financeState({ denied: o.denied });
  state.suggestions = suggestionsState({ empty: o.empty, fail: o.fail });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return state;
}

test('the queue: accept a history suggestion and undo it, dismiss one, mark a bill as seen', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-suggestions');
  await expect(page.getByRole('heading', { level: 1, name: 'Suggestions' })).toBeVisible();
  const history = page.locator('.fsugCard', { hasText: 'File as Groceries?' });
  await expect(history).toContainText('You chose Groceries for zebramart testcity 3 of 3 times.');
  await expect(history).toContainText('90% sure');
  await history.getByRole('button', { name: 'Accept' }).click();
  await expect(page.getByText('Done. One row changed.')).toBeVisible();
  await expect(history).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('Put back one row.')).toBeVisible();
  await expect(page.locator('.fsugCard', { hasText: 'File as Groceries?' })).toBeVisible();
  const unknown = page.locator('.fsugCard', { hasText: 'Not yet filed' });
  await expect(unknown.getByRole('button', { name: 'Accept' })).toHaveCount(0);
  await unknown.getByRole('button', { name: 'Dismiss' }).click();
  await expect(page.getByText('Dismissed. It will not come back.')).toBeVisible();
  await expect(page.locator('.fsugCard', { hasText: 'Not yet filed' })).toHaveCount(0);
  const bill = page.locator('.fsugCard', { hasText: 'Rent is due in 3 days' });
  await bill.getByRole('button', { name: 'Mark as seen' }).click();
  await expect(page.getByText('Marked as seen.')).toBeVisible();
  await page.getByRole('button', { name: /Statement rows held/ }).click();
  await expect(page.locator('.fsugCard')).toHaveCount(1);
  await page.locator('.fsugCard').getByRole('button', { name: 'Review' }).click();
  await expect(page).toHaveURL(/#finance-import$/);
});

test('rules: save a rule with a preview, apply it to earlier rows, then restore an older version', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-suggestions');
  await page.getByRole('tab', { name: 'Rules' }).click();
  await expect(page.getByText('No rules yet')).toBeVisible();
  await page.getByRole('button', { name: 'New rule' }).first().click();
  const editor = page.getByRole('dialog', { name: 'New rule' });
  await editor.getByRole('button', { name: 'Save and approve' }).click();
  await expect(editor.getByRole('alert')).toHaveText('Give a merchant, words or a reference.');
  await editor.getByLabel('Words in the description').fill('zebramart');
  await editor.getByLabel('Direction').selectOption('out');
  await editor.getByRole('button', { name: 'Preview' }).click();
  await expect(editor).toContainText('1 rows would change.');
  await editor.getByRole('button', { name: 'Save and approve' }).click();
  await expect(page.getByText('Rule saved and approved.')).toBeVisible();
  const row = page.locator('.fsugRule', { hasText: 'zebramart' });
  await row.getByRole('button', { name: 'Apply to earlier rows' }).click();
  const apply = page.getByRole('dialog', { name: 'What this rule would change' });
  await expect(apply).toContainText('POS 3381 ZEBRAMART TESTCITY');
  await apply.getByRole('button', { name: 'Apply to 1 rows' }).click();
  await expect(page.getByText(/Changed 1 rows/)).toBeVisible();
  await expect(page.locator('.fsugBatches')).toContainText('One row');
  await row.getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('dialog', { name: 'Edit rule' });
  await edit.getByLabel('Category', { exact: true }).selectOption('shopping');
  await edit.getByRole('button', { name: 'Save and approve' }).click();
  await row.getByRole('button', { name: 'Versions' }).click();
  const versions = page.getByRole('dialog', { name: 'Versions' });
  await expect(versions.getByRole('listitem')).toHaveCount(2);
  await versions.getByRole('button', { name: 'Restore' }).click();
  await expect(page.getByText('Restored as a new version and approved.')).toBeVisible();
  await expect(row).toContainText('Version 3');
});

test('merchants, subcategories and automation, with what is never automated', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-suggestions');
  await page.getByRole('tab', { name: 'Merchants and subcategories' }).click();
  await expect(page.getByText('No spellings yet.', { exact: false })).toBeVisible();
  await page.getByLabel("Spelling in the bank's text").fill('زيبرا');
  await page.getByLabel('Merchant', { exact: true }).fill('Zebra Mart');
  await page.getByRole('button', { name: 'Add spelling' }).click();
  await expect(page.locator('.fsugAliasList').first()).toContainText('Zebra Mart');
  await page.getByLabel('Name').fill('Fruit');
  await page.getByRole('button', { name: 'Add subcategory' }).click();
  await expect(page.getByText('Subcategory saved.')).toBeVisible();
  await page.getByRole('tab', { name: 'Automation' }).click();
  await expect(page.getByRole('region', { name: 'Never automated' })).toContainText('Approving payments');
  await page.getByLabel('File rows automatically when ANUM is sure').check();
  await page.getByRole('button', { name: 'Save automation' }).click();
  await expect(page.getByText('Automation saved.')).toBeVisible();
});

test('apply to similar from the Transactions drawer', async ({ page }) => {
  const state = await mockApi(page);
  // Row 3 was typed by hand (the mock register's origins repeat sms, Apple Pay, statement, by hand).
  state.transactions[3] = { ...state.transactions[3], description: 'POS 1 ZEBRAMART TESTCITY', category: 'groceries', category_source: 'manual', import_id: null };
  await page.goto('/#finance-transactions');
  await page.locator('.fregTable tbody tr', { hasText: 'ZEBRAMART' }).first().click();
  const drawer = page.locator('.fregDrawer');
  await drawer.getByRole('button', { name: 'Apply to similar' }).click();
  const dialog = page.getByRole('dialog', { name: 'Apply to similar transactions' });
  await expect(dialog).toContainText('zebramart testcity');
  await dialog.getByRole('radio', { name: 'Only this account' }).click();
  await dialog.getByRole('button', { name: 'Save rule and apply to 1 rows' }).click();
  await expect(drawer.getByText(/Changed 1 rows/)).toBeVisible();
});

test('every state: empty with a next step, an error with a retry, denied', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-suggestions');
  await expect(page.getByText('Nothing waits for you')).toBeVisible();
  await page.getByRole('button', { name: 'Add a rule' }).click();
  await expect(page.getByRole('dialog', { name: 'New rule' })).toBeVisible();
});

test('a failed load says what failed and retries', async ({ page }) => {
  await mockApi(page, { fail: 1 });
  await page.goto('/#finance-suggestions');
  await expect(page.getByText('The database is not reachable.')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.fsugCard').first()).toBeVisible();
});

test('permission denied explains who can use Finance', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-suggestions');
  await expect(page.locator('.cmDenied')).toBeVisible();
});

test('Arabic: the page reads right to left with its own words', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-suggestions');
  await expect(page.getByRole('heading', { level: 1, name: 'الاقتراحات' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(page.locator('.fsugCard').first()).toBeVisible();
});

test('the sidebar keeps Finance and Suggestions in view at 1440x900', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Phones show the section strip instead of the sidebar.');
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#finance-suggestions');
  const primary = page.getByRole('navigation', { name: 'Primary' });
  await expect(primary.getByRole('button', { name: 'Finance', exact: true })).toBeInViewport();
  await expect(primary.getByRole('navigation', { name: 'Finance sections' }).locator('[aria-current="page"]')).toHaveText('Suggestions');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const view of ['Queue', 'Rules', 'Merchants and subcategories', 'Automation'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: Suggestions, ${view}, fits with no sideways scroll at ${width}x${height}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await mockApi(page);
      await page.goto('/#finance-suggestions');
      await page.getByRole('tab', { name: view }).click();
      await expect(page.locator('.fsugPage .finCard, .fsugCard').first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      // A control outside the window counts as clipped unless it sits in a strip that scrolls sideways on purpose (the tabs, the kind chips).
      const clipped = await page.locator('.fsugPage').evaluate((root) => [...root.querySelectorAll('button, input, select')].filter((e) => {
        const box = e.getBoundingClientRect();
        if (box.width === 0 || (box.right <= document.documentElement.clientWidth + 1 && box.left >= -1)) return false;
        for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
        return true;
      }).map((e) => `${e.tagName}.${e.className}`));
      expect(clipped).toEqual([]);
      if (width === 1440 || width === 390) await page.screenshot({ path: `test-results/finance-suggestions-${view.split(' ')[0].toLowerCase()}-${width}.png`, fullPage: true });
    });
  }
}
