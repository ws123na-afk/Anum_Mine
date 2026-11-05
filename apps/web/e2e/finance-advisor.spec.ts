import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { advisorState, type AdvisorState } from './support/financeAdvisor';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Money advisor (docs/finance.md#money-advisor): this week's note (runway three ways with the
// assumptions, where money went, coming payments, cards, ranked next steps), Generate now, past weeks with
// paging, the weekly switches, every state (loading, empty with a next step, no accounts, short history,
// error with a retry, permission denied), Arabic and the responsive gate. ANUM only advises, and the page
// says so. The API is mocked at http://localhost:8000/** like the other specs; figures are SYNTHETIC.

async function mockApi(page: Page, advisor: AdvisorState, o: { slow?: boolean } = {}) {
  const state = financeState({});
  state.advisor = advisor;
  const asked: string[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path.startsWith('/api/v1/finance/advisor')) {
      asked.push(`${request.method()} ${path.replace('/api/v1/finance', '')}${url.search}`);
      if (o.slow) await new Promise((resolve) => setTimeout(resolve, 700));
    }
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { asked, state: advisor };
}

const card = (page: Page, name: string | RegExp) => page.getByRole('region', { name });

test('this week\'s note: survival three ways, where money went, coming payments, cards and ranked steps', async ({ page }) => {
  await mockApi(page, advisorState(1));
  await page.goto('/#finance-advisor');
  await expect(page.getByRole('heading', { level: 1, name: 'Money advisor' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Finance sections' }).getByRole('button', { name: 'Money advisor' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByText('ANUM only advises', { exact: true })).toBeVisible();
  await expect(page.getByText('It never moves money, pays a bill or touches a bank or card.', { exact: false })).toBeVisible();
  const kpis = page.getByRole('region', { name: 'This week at a glance' });
  await expect(kpis).toContainText('Days you could last');
  await expect(kpis).toContainText('50 days');
  await expect(kpis).toContainText('Essentials only, if your income stopped');
  const runway = card(page, /If your income stopped/);
  await expect(runway.getByRole('listitem').filter({ hasText: 'Bare minimum' })).toContainText('279 days');
  await expect(runway.getByRole('listitem').filter({ hasText: 'Essentials' }).first()).toContainText('50 days');
  await expect(runway.getByRole('listitem').filter({ hasText: 'As you spend now' })).toContainText('37 days');
  await expect(runway).toContainText('Card limits are not your money and are not counted');
  await expect(runway).toContainText('Average of the full months');
  await expect(runway).toContainText('nothing is counted twice');
  const steps = card(page, /What to do next/);
  await expect(steps.getByRole('listitem').first()).toContainText('Urgent');
  await expect(steps.getByRole('listitem').first()).toContainText('Pay Rewards card (9876) in full');
  await expect(card(page, /Where your money went/)).toContainText('47% above a usual month');
  await expect(card(page, /Where your money went/).getByRole('cell').filter({ hasText: 'AED' }).first()).toBeVisible();
  const coming = card(page, /Coming payments/);
  await expect(coming).toContainText('DEWA');
  await expect(coming).toContainText('No due day set');
  await expect(coming).toContainText('Found in your records, not saved as bills');
  const cards = card(page, /Your cards/);
  await expect(cards).toContainText('Paying only the minimum costs about');
  await expect(cards).toContainText('To add under Debts: the interest rate, the due day');
  // A step opens the page where it can be done.
  await steps.getByRole('button', { name: 'Open Bills' }).first().click();
  await expect(page).toHaveURL(/#finance-bills$/);
});

test('generate now, past weeks with paging, open a past week and the weekly switches', async ({ page }) => {
  const { asked, state } = await mockApi(page, advisorState(11));
  await page.goto('/#finance-advisor');
  const history = card(page, 'Past weeks');
  await expect(history.getByRole('row')).toHaveCount(9);
  await expect(history).toContainText('Page 1 of 2');
  await history.getByRole('button', { name: 'Older' }).click();
  await expect(history).toContainText('Page 2 of 2');
  await expect(history.getByRole('row')).toHaveCount(4);
  await history.getByRole('button', { name: /Open: Week of/ }).first().click();
  await expect(page.getByText(/You are looking at the note for the week of/)).toBeVisible();
  await page.getByRole('button', { name: 'Back to the newest note' }).click();
  await expect(page.getByText(/You are looking at the note for the week of/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Generate now' }).click();
  await expect(page.getByText('This week\'s note was worked out again from your records as they are now.')).toBeVisible();
  expect(state.generated).toBe(1);
  const settings = card(page, 'Weekly note');
  await expect(settings.getByLabel('Day')).toHaveValue('6');
  await settings.getByLabel('Day').selectOption('4');
  await settings.getByRole('checkbox', { name: 'Send me a weekly money note' }).uncheck();
  await settings.getByRole('button', { name: 'Save' }).click();
  await expect(settings).toContainText('Saved.');
  await expect(settings).toContainText('The weekly note is off.');
  expect(state.settings).toMatchObject({ enabled: false, weekday: 4 });
  expect(asked).toContain('PUT /advisor/settings');
  expect(asked.some((a) => a.startsWith('GET /advisor/notes?limit=8&offset=8'))).toBe(true);
});

test('another currency is shown on its own, never mixed', async ({ page }) => {
  await mockApi(page, advisorState(1));
  await page.goto('/#finance-advisor');
  await page.getByLabel('Currency').selectOption('USD');
  const runway = card(page, /If your income stopped/);
  await expect(runway).toContainText(/Money you have: \W*USD\s1,250\.00/);
  await expect(runway.getByRole('listitem').filter({ hasText: 'Bare minimum' })).toContainText('Nothing goes out on these figures');
  await expect(card(page, /Coming payments/)).toContainText('No bills, minimums or recurring payments found');
});

test('empty: no note yet offers Generate now and Import', async ({ page }) => {
  const { state } = await mockApi(page, advisorState(0));
  await page.goto('/#finance-advisor');
  const empty = card(page, 'No money note yet');
  await expect(empty.getByRole('button', { name: 'Import statements' })).toBeVisible();
  await expect(card(page, 'Past weeks')).toContainText('Past notes appear here week by week.');
  await empty.getByRole('button', { name: 'Generate now' }).click();
  await expect(card(page, /If your income stopped/)).toBeVisible();
  expect(state.generated).toBe(1);
});

test('no accounts: says to import statements, with the next actions', async ({ page }) => {
  await mockApi(page, advisorState(1, { noAccounts: true }));
  await page.goto('/#finance-advisor');
  const empty = card(page, 'Import statements to get advice');
  await empty.getByRole('button', { name: 'Add an account' }).click();
  await expect(page).toHaveURL(/#finance-accounts$/);
});

test('short history: only the bare minimum, said plainly', async ({ page }) => {
  await mockApi(page, advisorState(1, { shortHistory: true }));
  await page.goto('/#finance-advisor');
  const runway = card(page, /If your income stopped/);
  await expect(runway).toContainText('Not enough history yet');
  await expect(runway).toContainText('You have 19 days of records');
  await expect(runway.getByRole('listitem').filter({ hasText: 'Essentials' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'This week at a glance' })).toContainText('Bills and minimums only: not enough history yet');
});

test('loading, then an error with a retry, and permission denied', async ({ page }) => {
  await mockApi(page, advisorState(1, { failOverview: 1 }), { slow: true });
  await page.goto('/#finance-advisor');
  await expect(page.getByText('Loading your money note...')).toBeVisible();
  const alert = page.getByRole('alert').filter({ hasText: 'Your money note could not be loaded' });
  await expect(alert).toContainText('The database is not reachable.');
  await alert.getByRole('button', { name: 'Try again' }).click();
  await expect(card(page, /If your income stopped/)).toBeVisible();
});

test('permission denied explains who can use Finance', async ({ page }) => {
  await mockApi(page, advisorState(1, { denied: true }));
  await page.goto('/#finance-advisor');
  const denied = page.locator('.cmDenied');
  await expect(denied.getByRole('heading')).toBeVisible();
  await expect(denied).toContainText(/owner|member/);
  await expect(page.getByRole('button', { name: 'Generate now' })).toHaveCount(0);
});

test('Arabic: the note reads right to left with the steps in Arabic and figures left to right', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page, advisorState(3));
  await page.goto('/#finance-advisor');
  await expect(page.getByRole('heading', { level: 1, name: 'مستشار المال' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(page.getByText('ANUM يقدّم النصيحة فقط', { exact: true })).toBeVisible();
  const steps = card(page, /ما الذي تفعله بعد ذلك/);
  await expect(steps.getByRole('listitem').first()).toContainText('سدّد');
  await expect(steps.getByRole('listitem').first()).toContainText('عاجل');
  await expect(card(page, /إذا توقف دخلك/)).toContainText('الأساسيات');
  await expect(page.getByRole('region', { name: 'هذا الأسبوع باختصار' }).locator('.finMoney').first()).toHaveAttribute('dir', 'ltr');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive (${locale}): the advisor fits with no sideways scroll at ${width}x${height}`, async ({ page }) => {
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await page.setViewportSize({ width, height });
      await mockApi(page, advisorState(11));
      await page.goto('/#finance-advisor');
      await expect(page.locator('.finScenarios')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const sticking = await page.locator('.finAdvisorPage').evaluate((root) => [...root.querySelectorAll('*')].filter((e) => {
        const box = e.getBoundingClientRect();
        if (box.right <= document.documentElement.clientWidth + 1 && box.left >= -1) return false;
        for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
        return true;
      }).map((e) => `${e.tagName}.${e.className}`));
      expect(sticking).toEqual([]);
      for (const name of locale === 'en' ? ['Generate now', 'Save', 'Older'] : ['أنشئ الآن', 'حفظ', 'الأقدم']) {
        const control = page.getByRole('button', { name, exact: true }).first();
        await control.scrollIntoViewIfNeeded();
        const box = await control.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
        expect(box!.height).toBeGreaterThanOrEqual(32);
      }
      await page.screenshot({ path: test.info().outputPath(`advisor-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
