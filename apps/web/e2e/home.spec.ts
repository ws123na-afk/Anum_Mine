import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Home, the owner's overview (docs/home.md#the-web-home), and the shell around it (sidebar with
// workspaces, global search, notifications, user menu), against the mocked workspace API
// (e2e/support/workspaceApi.ts and e2e/support/homeApi.ts). Giving orders is ANUM Command
// (e2e/command-center.spec.ts); Home must not be a copy of it.

const VIEWPORTS = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const API = 'http://localhost:8000';
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function serious(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 5).map((n) => n.target.join(' ')).join(' | ')}`);
}

async function openHome(page: Page) {
  const state = await mockWorkspaceApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: /^Good (morning|afternoon|evening), Sara$/ })).toBeVisible();
  return state;
}

test('Home is the overview of the day, not the command page', async ({ page }) => {
  await openHome(page);
  await expect(page.getByRole('main', { name: /Good (morning|afternoon|evening), Sara/ })).toBeVisible();
  // No command composer, plan or saved commands on Home: those live on ANUM Command.
  await expect(page.getByLabel('Your command')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Saved commands' })).toHaveCount(0);

  const glance = page.getByRole('region', { name: 'Today at a glance' });
  await expect(glance.getByRole('button', { name: /Approvals waiting\s*1/ })).toBeVisible();
  await expect(glance.getByRole('button', { name: /Tasks\s*0\s*running.*0 failed · 1 waiting/ })).toBeVisible();
  await expect(glance.getByRole('button', { name: /Unread email\s*1/ })).toBeVisible();
  await expect(glance.getByRole('button', { name: /Unread WhatsApp\s*3\s*In 1 chat/ })).toBeVisible();
  await expect(glance.getByRole('button', { name: /Meetings today\s*2/ })).toBeVisible();
  await expect(glance.getByRole('button', { name: /AI budget this month\s*75%/ })).toBeVisible();

  await expect(page.getByRole('region', { name: /Today's focus/ }).getByRole('button', { name: /Invoice overdue from Gulf Tyres/ })).toBeVisible();
  await expect(page.getByRole('region', { name: "Today's meetings" }).getByText('Fleet review with the team', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'ANUM noticed' }).getByText('A task has not moved for 2 hours')).toBeVisible();

  // Recent activity is paged: six first, then the rest.
  const activity = page.getByRole('region', { name: 'Recent activity' });
  await expect(activity.getByRole('listitem')).toHaveCount(6);
  await activity.getByRole('button', { name: 'Show 2 more' }).click();
  await expect(activity.getByRole('listitem')).toHaveCount(8);

  // A quick link opens its area; the glance card opens Approvals.
  await page.getByRole('navigation', { name: 'Go to' }).getByRole('button', { name: /Inbox/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Inbox' })).toBeVisible();
});

test('approving from Home sends the payload hash that was shown', async ({ page }) => {
  await openHome(page);
  let decision: { path: string; body: unknown } | null = null;
  await page.route(`${API}/api/v1/approvals/approval_pending/*`, async (route) => {
    decision = { path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() };
    const now = new Date().toISOString();
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      task: { id: 'task_release', title: 'Review the release', prompt: 'Review', status: 'running', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now },
      run: null, approval: null,
    }) });
  });
  const approvals = page.getByRole('region', { name: /Waiting for your decision/ });
  await expect(approvals.getByText('external.action')).toBeVisible();
  await approvals.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => decision).toEqual({ path: '/api/v1/approvals/approval_pending/approve', body: { payload_hash: 'ab'.repeat(32) } });
});

test('an insight hands its request to ANUM Command, which shows the plan first', async ({ page }) => {
  await openHome(page);
  await page.getByRole('region', { name: 'ANUM noticed' }).getByRole('button', { name: 'Analyze' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'ANUM Command' })).toBeVisible();
  await expect(page.getByLabel('Your command')).toHaveValue('Find out why the release review is stuck');
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toBeVisible();
});

test('pins: add a part of ANUM, open it, and the empty and error states say what to do', async ({ page }) => {
  await openHome(page);
  const pins = page.getByRole('region', { name: 'Pinned' });
  await pins.getByRole('button', { name: 'Edit pins' }).click();
  await pins.getByLabel('Which part').selectOption('reports');
  await pins.getByRole('button', { name: 'Add a pin' }).click();
  await expect(pins.getByRole('button', { name: 'Reports', exact: true })).toBeVisible();
  await pins.getByRole('button', { name: 'Done' }).click();
  await pins.getByRole('button', { name: 'Tasks', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Tasks' })).toBeVisible();
});

test('a part that fails says what failed and offers Try again; a viewer is told who sees the budget', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route(`${API}/api/v1/home/summary`, (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Home is warming up', correlation_id: 'c1' } }) }));
  await page.route(`${API}/api/v1/workspace-memberships/current`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'viewer', active: true }) }));
  await page.goto('/#home');
  const focus = page.getByRole('region', { name: /Today's focus/ });
  await expect(focus.getByRole('alert')).toContainText("Today's overview could not load");
  await expect(focus.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(page.getByRole('button', { name: /AI budget this month.*Only workspace owners see budget use/ })).toBeVisible();
});

test('Ctrl+K searches views, tasks, documents and emails', async ({ page }) => {
  await openHome(page);
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', { name: 'Search ANUM' });
  await expect(palette).toBeVisible();
  await palette.getByRole('combobox').fill('release');
  await expect(palette.getByRole('option', { name: /release-notes\.md/ })).toBeVisible();
  await expect(palette.getByRole('option', { name: /Review the release/ })).toBeVisible();
  await palette.getByRole('combobox').fill('knowl');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Knowledge Base' })).toBeVisible();
});

test('notifications list the pending approval; the user menu opens Settings sections', async ({ page }) => {
  await openHome(page);
  // One pending approval plus the team mock's one unread team notification.
  await page.getByRole('button', { name: 'Notifications, 2 waiting' }).click();
  await page.getByRole('dialog', { name: 'Notifications' }).getByRole('button', { name: /external\.action/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible();
  await page.getByRole('button', { name: /Account menu/ }).click();
  await page.getByRole('dialog', { name: 'Your account' }).getByRole('button', { name: 'Language' }).click();
  await expect(page.locator('.languageChoices')).toBeVisible();
});

test('the sidebar lists the workspaces from the API and offers Add workspace', async ({ page, isMobile }) => {
  test.skip(isMobile, 'on phones the workspace list lives in the top-bar switcher');
  await openHome(page);
  const nav = page.getByRole('navigation', { name: 'Primary' });
  await expect(nav.getByRole('list', { name: 'Workspaces' }).getByRole('button', { name: /Foundation|workspace_foundation/ })).toHaveAttribute('aria-current', 'true');
  await nav.getByRole('button', { name: 'Add workspace' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a workspace' });
  await expect(dialog.getByLabel('Name')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['dark', 'light'] as const) {
    test(`${locale} ${theme}: no serious axe violations, no horizontal overflow at the five viewports`, async ({ page }) => {
      await useSavedLocale(page, locale);
      if (theme === 'light') await page.addInitScript(() => { try { window.localStorage.setItem('anum.theme', 'light'); } catch { /* ignore */ } });
      await mockWorkspaceApi(page);
      for (const size of VIEWPORTS) {
        await page.setViewportSize(size);
        await page.goto('/#home');
        await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
        await expect(page.locator('.glanceCard').first()).toBeVisible();
        await expect(page.locator('.homePage .listRow').first()).toBeVisible();
        expect(await noHorizontalOverflow(page), `${size.width}x${size.height}`).toBe(true);
      }
      expect(await serious(page)).toEqual([]);
    });
  }
}
