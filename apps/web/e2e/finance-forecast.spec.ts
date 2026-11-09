import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { mockBooks } from './support/businessBooksApi';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// The forecast, the emergency reserve, Finance settings (docs/finance.md#forecast, #emergency-reserve, #finance-settings)
// and the Accountant review in Business books (docs/business-books.md#accountant-review). The API is mocked at
// http://localhost:8000/** like the other specs; every account, vendor and amount is SYNTHETIC.

const stamp = '2026-11-01T00:00:00Z';

async function mockApi(page: Page) {
  const state = financeState();
  const sent: { method: string; path: string; body: unknown }[] = [];
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
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function shot(page: Page, name: string) {
  const dir = process.env.FIN_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test('a reserve set up from Safe to spend is a goal; the forecast then warns before the balance drops below it', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-safe');
  const reserve = page.getByRole('region', { name: 'Emergency reserve' });
  await expect(reserve).toContainText('No emergency reserve in AED yet');
  await expect(reserve).toContainText(/3 months is .*18,000\.00/);
  await reserve.getByRole('button', { name: 'Set up a reserve' }).click();
  await expect(page).toHaveURL(/#finance-settings$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Finance settings' })).toBeVisible();
  const form = page.getByRole('region', { name: /^Emergency reserve A goal set aside/ });
  await expect(form.getByLabel('Size it by')).toHaveValue('months');
  await expect(form).toContainText(/Target .*18,000\.00/);
  await form.getByRole('button', { name: 'Create the reserve' }).click();
  await expect(page.getByText('Emergency reserve saved.')).toBeVisible();
  const created = sent.find((s) => s.method === 'POST' && s.path.endsWith('/finance/goals'));
  expect(created?.body).toMatchObject({ purpose: 'emergency', reserve_months: 3, target_amount: '18000.00', currency: 'AED', account_id: null });
  await expect(form).toContainText(/of .*18,000\.00/);
  await expect(form.getByRole('meter')).toHaveAttribute('aria-valuenow', '0');
  await shot(page, 'settings-reserve');

  await page.goto('/#finance-safe');
  const forecast = page.getByRole('region', { name: /Forecast to the end of/ });
  await expect(forecast).toContainText('Below your emergency reserve');
  await expect(forecast).toContainText(/drops below your reserve of .*18,000\.00/);
  await expect(page.getByRole('region', { name: /^Emergency reserve Emergency reserve/ })).toContainText('Target: 3 months of essential spending');
});

test('settings save the currency and time zone with the keyword rules kept, and link to the pages that own the rest', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-settings');
  const nav = page.getByRole('navigation', { name: 'Finance sections' });
  await expect(nav.getByRole('button', { name: 'Finance settings' })).toHaveAttribute('aria-current', 'page');
  const general = page.getByRole('region', { name: /Currency, time zone and statement rules/ });
  await general.getByLabel('Time zone').fill('Asia/Riyadh');
  await general.getByRole('button', { name: 'Add rule' }).click();
  await general.getByLabel('Description contains').last().fill('example gym');
  await general.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Finance settings saved.')).toBeVisible();
  const saved = sent.find((s) => s.method === 'PUT' && s.path.endsWith('/finance/settings'));
  expect(saved?.body).toMatchObject({ base_currency: 'AED', timezone: 'Asia/Riyadh' });
  expect((saved?.body as { category_rules: { match: string }[] }).category_rules.some((r) => r.match === 'example gym')).toBe(true);
  await expect(page.getByRole('region', { name: /Salary profile/ })).toContainText('No salary profile yet');
  await expect(page.getByRole('region', { name: /Privacy/ }).getByRole('link', { name: 'Download my data' })).toHaveAttribute('href', '#privacy');
  // Safe to spend's buffer is edited in its own dialog, through PUT /finance/plan.
  await page.getByRole('region', { name: /^Safe to spend/ }).getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('region', { name: /Rules and automation/ }).getByRole('link', { name: 'Automation policies' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Suggestions' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Automation' })).toHaveAttribute('aria-selected', 'true');
});

test('the old Settings button on a Finance page opens the settings page', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-import');
  await page.getByRole('main').getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(/#finance-settings$/);
});

for (const locale of ['en', 'ar'] as const) {
  test(`@gate Finance settings and the forecast fit every size without sideways scrolling (${locale})`, async ({ page }) => {
    await mockApi(page);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const hash of ['finance-settings', 'finance-safe']) {
      for (const size of GATE_SIZES) {
        await page.setViewportSize(size);
        await page.goto(`/#${hash}`);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        if (hash === 'finance-safe') await expect(page.locator('.finForecast figure.finFigure svg')).toBeVisible();
        else await expect(page.locator('.finSettingsGrid')).toBeVisible();
        expect(await horizontalOverflow(page), `${hash} at ${size.width}`).toBeLessThanOrEqual(0);
        expect(await clippedControls(page), `${hash} at ${size.width}`).toEqual([]);
        await shot(page, `${hash}-${locale}-${size.width}`);
      }
      await noSeriousAxe(page);
    }
  });
}

// -- Accountant review ---------------------------------------------------------------------------------------------------

const books = (page: Page, name: string) => page.getByRole('navigation', { name: 'Books sections' }).getByRole('button', { name, exact: true });

test('accountant review: the owner sees a draft they made (someone else approves), a missing rate and the close checklist', async ({ page }) => {
  const mock = await mockBooks(page, { ledger: 'issues' });
  mock.state.bills[1].status = 'draft';
  await page.goto('/#business-books');
  const banner = page.getByRole('region', { name: /wait for an accountant/ });
  await expect(banner).toContainText('1 draft bill');
  await expect(banner).toContainText('1 currency without a rate');
  await banner.getByRole('button', { name: 'Open the accountant review' }).click();
  await expect(books(page, 'Accountant review')).toHaveAttribute('aria-current', 'page');
  const list = page.getByRole('region', { name: /items waiting/ });
  const draft = list.getByRole('listitem').filter({ hasText: 'Draft bill to approve' });
  await expect(draft).toContainText('A draft bill from Example Fuel Station waits for approval.');
  await expect(draft).toContainText('You made this draft');
  await expect(draft.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  await expect(list.getByRole('listitem').filter({ hasText: 'No exchange rate' })).toContainText('1 record in USD has no exchange rate');
  await expect(page.getByRole('region', { name: /Close checklist/ }).first()).toBeVisible();
  await list.getByRole('combobox').selectOption('unconverted');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await list.getByRole('listitem').getByRole('button', { name: /^Open/ }).click();
  await expect(books(page, 'Settings')).toHaveAttribute('aria-current', 'page');
});

test('accountant review: an accountant approves the draft; a member who is not an accountant sees no section', async ({ page }) => {
  const mock = await mockBooks(page, { role: 'member', accountant: true });
  mock.state.bills[1].status = 'draft';
  await page.goto('/#business-books/review');
  const draft = page.getByRole('listitem').filter({ hasText: 'Draft bill to approve' });
  await draft.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Approved the draft bill from Example Fuel Station.')).toBeVisible();
  expect(mock.calls.some((call) => call.method === 'POST' && call.path.endsWith('/approve'))).toBe(true);
  await noSeriousAxe(page);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { role: 'member' });
  await page.goto('/#business-books');
  await page.reload();
  await expect(books(page, 'Bills')).toBeVisible();
  await expect(books(page, 'Accountant review')).toHaveCount(0);
  await page.goto('/#business-books/review');
  await expect(page.getByText(/Only an owner or an accountant of this business sees the accountant review/)).toBeVisible();
});

test('@gate the accountant review fits every size', async ({ page }) => {
  const mock = await mockBooks(page, { ledger: 'issues' });
  mock.state.bills[1].status = 'draft';
  for (const size of GATE_SIZES) {
    await page.setViewportSize(size);
    await page.goto('/#business-books/review');
    await expect(page.getByRole('region', { name: /items waiting/ })).toBeVisible();
    expect(await horizontalOverflow(page), `review at ${size.width}`).toBeLessThanOrEqual(0);
    expect(await clippedControls(page), `review at ${size.width}`).toEqual([]);
    await shot(page, `books-review-${size.width}`);
  }
});
