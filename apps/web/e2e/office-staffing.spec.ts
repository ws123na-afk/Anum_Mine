import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockStaffing, type StaffingMockOptions } from './support/officeStaffingApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Offices and agents (docs/ANUM_MINE_OFFICE_STAFFING.md): the command centre lists every office with its blueprint
// coverage, agents and data sources; an owner reviews and applies the missing agents, skills, tools and rules with a
// click; the headquarters room panel shows the same summary. API mocked; nothing changes without the Apply click.
const SIZES = [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const serious = async (page: Page, include: string) => (await new AxeBuilder({ page }).include(include).analyze()).violations
  .filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);

async function openOffices(page: Page, options: StaffingMockOptions = {}, locale: 'en' | 'ar' = 'en') {
  await mockWorkspaceApi(page);
  if (locale === 'ar') await useSavedLocale(page, 'ar');
  const mock = await mockStaffing(page, options);
  await page.goto('/#executive');
  await page.getByRole('tab', { name: locale === 'ar' ? 'المكاتب والوكلاء' : 'Offices and agents' }).click();
  return mock;
}

test('an owner sees every office, opens one and applies the chosen changes with a click', async ({ page }) => {
  const mock = await openOffices(page);
  const cards = page.locator('.osCard');
  await expect(cards).toHaveCount(2);
  const corporate = cards.filter({ hasText: 'Corporate Services' });
  await expect(corporate).toContainText('2 of 3 roles staffed');
  await expect(corporate).toContainText('1 agent is missing a skill, tool or rule');
  await expect(corporate).toContainText('1 data source not connected');
  await page.getByRole('searchbox', { name: 'Search offices' }).fill('tyre');
  await expect(cards).toHaveCount(1);
  await page.getByRole('searchbox', { name: 'Search offices' }).fill('');
  await expect(cards).toHaveCount(2);
  await corporate.getByRole('button', { name: 'Open Corporate Services' }).click();
  const drawer = page.getByRole('dialog', { name: 'Corporate Services' });
  await expect(drawer.locator('[data-role="hr_people"]')).toContainText('No agent yet');
  await expect(drawer.locator('[data-role="finance_clerk"]')).toContainText('Missing tools: finance.transactions.search');
  await expect(drawer.locator('[data-role="finance_clerk"]')).toContainText('Above the allowed risk (remove under Agents): send_email');
  await expect(drawer.locator('[data-role="legal_reviewer"]')).toContainText('Limited');
  await expect(drawer.getByRole('button', { name: 'Connect Finance ledger' })).toBeVisible();
  await expect(drawer).toContainText('Web search service: set by the server operator');
  await drawer.locator('[data-role="finance_clerk"] summary').click();
  await expect(drawer.locator('[data-role="finance_clerk"]')).toContainText('Place a live trade; live trading stays off');
  expect(mock.applies).toHaveLength(0);
  await drawer.getByRole('checkbox', { name: /Add the HR and People Assistant agent/ }).check();
  await drawer.getByRole('checkbox', { name: /Update Ledger Desk/ }).check();
  await drawer.getByRole('button', { name: 'Review 2 changes' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Apply to Corporate Services' });
  await expect(confirm).toContainText('Create "HR and People Assistant, Corporate Services" paused, with 2 tools and 1 skills, a monthly cap of USD 5, seated at Desk 2 in HR.');
  await expect(confirm).toContainText('Nothing is removed and nothing is loosened.');
  expect(mock.applies).toHaveLength(0);
  await confirm.getByRole('button', { name: 'Apply' }).click();
  await expect(drawer.getByRole('status').filter({ hasText: 'Done: 1 created, 1 updated, 0 not changed.' })).toBeVisible();
  expect(mock.applies).toEqual([{ input_mode: 'click', lang: 'en', actions: [{ id: 'create:hr_people', hash: 'a'.repeat(64) }, { id: 'update:agent_fin', hash: 'b'.repeat(64) }] }]);
  await expect(drawer.locator('[data-role="hr_people"]')).toContainText('Staffed');
  await expect(drawer).toContainText('Nothing to add: every role of this office is staffed and complete.');
  expect(await serious(page, '[role="dialog"]')).toEqual([]);
});

test('a viewer reads everything but cannot apply', async ({ page }) => {
  await openOffices(page, { role: 'viewer' });
  await page.getByRole('button', { name: 'Open Corporate Services' }).click();
  const drawer = page.getByRole('dialog', { name: 'Corporate Services' });
  await expect(drawer).toContainText('Only a workspace owner can add agents or change them here.');
  await expect(drawer.getByRole('checkbox')).toHaveCount(0);
});

test('an empty workspace offers to set up the campus', async ({ page }) => {
  await openOffices(page, { mode: 'empty' });
  await expect(page.getByText('This workspace has no offices yet.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open the headquarters and set up your campus' })).toBeVisible();
});

test('permission denied says who can grant access', async ({ page }) => {
  await openOffices(page, { mode: 'denied' });
  await expect(page.getByText(/You cannot read the offices of this workspace/)).toBeVisible();
});

test('a server without PostgreSQL says so and shows nothing invented', async ({ page }) => {
  await openOffices(page, { mode: 'missing' });
  await expect(page.getByText(/Offices need the PostgreSQL server/)).toBeVisible();
  await expect(page.locator('.osCard')).toHaveCount(0);
});

test('an error says what failed and retries', async ({ page }) => {
  await openOffices(page, { mode: 'error' });
  await expect(page.getByRole('alert')).toContainText('The offices could not be loaded.');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.osCard')).toHaveCount(2);
});

test('long lists page and a search with no match offers to clear it', async ({ page }) => {
  await openOffices(page, { offices: 30 });
  await expect(page.locator('.osCard')).toHaveCount(12);
  await expect(page.getByText('Page 1 of 3')).toBeVisible();
  await page.getByRole('button', { name: 'Next page' }).click();
  await expect(page.getByText('Page 2 of 3')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search offices' }).fill('nothing here');
  await expect(page.getByText('No office matches "nothing here".')).toBeVisible();
  await page.getByRole('button', { name: 'Clear the search' }).click();
  await expect(page.getByText('Page 1 of 3')).toBeVisible();
});

test('Arabic reads right to left with Arabic roles and rules', async ({ page }) => {
  await openOffices(page, {}, 'ar');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('button', { name: 'افتح الخدمات المؤسسية' }).click();
  const drawer = page.getByRole('dialog', { name: 'الخدمات المؤسسية' });
  await expect(drawer.locator('[data-role="hr_people"]')).toContainText('مساعد الموارد البشرية');
  await expect(drawer.locator('[data-role="hr_people"]')).toContainText('لا وكيل بعد');
  expect(await serious(page, '[role="dialog"]')).toEqual([]);
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of SIZES) {
    test(`@gate offices and agents fit at ${size.width}x${size.height} (${locale})`, async ({ page }) => {
      await page.setViewportSize(size);
      await openOffices(page, {}, locale);
      await expect(page.locator('.osCard')).toHaveCount(2);
      expect(await noOverflow(page)).toBe(true);
      expect(await serious(page, '.osView')).toEqual([]);
      await page.locator('.osCard').first().getByRole('button').first().click();
      const drawer = page.locator('.drawerPanel');
      await expect(drawer.locator('[data-role="hr_people"]')).toBeVisible();
      expect(await noOverflow(page)).toBe(true);
      // The drawer slides in 24px (dialog.css drawerIn): measure it once the slide has finished.
      await expect.poll(async () => {
        const box = await drawer.boundingBox();
        return Boolean(box && box.x >= -1 && box.x + box.width <= size.width + 1);
      }).toBe(true);
    });
  }
}

// The headquarters: a room's panel shows the staffing of its office (lazy card).
const registry = hqRegistry(TENANT_A, { rooms: 3, desks: 2, agentIds: ['agent_fin'] });
async function openRoom(page: Page, locale: 'en' | 'ar' = 'en') {
  await openHq(page, { registry, locale, beforeGoto: async (p) => { await mockStaffing(p); } });
  await expect(page.locator('.hqLive')).toBeVisible();
  await page.locator('.hqLive').getByRole('button', { name: locale === 'ar' ? 'قائمة' : 'List', exact: true }).click();
  await page.locator('.hqListRoom', { hasText: 'Alpha Room 1' }).click();
}

test('the room panel shows the office staffing and links to the command centre', async ({ page }) => {
  await openRoom(page);
  const card = page.locator('.osHqCard');
  await expect(card).toContainText('2 of 3 roles staffed');
  await expect(card.locator('[data-state="missing"]')).toContainText('HR and People Assistant');
  await expect(card.getByRole('button', { name: 'Connect Finance ledger' })).toBeVisible();
  await card.getByRole('button', { name: 'Open in the command centre' }).click();
  await expect(page).toHaveURL(/#executive$/);
  await expect(page.getByRole('tab', { name: 'Offices and agents' })).toHaveAttribute('aria-selected', 'true');
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of SIZES) {
    test(`@gate room staffing card fits at ${size.width}x${size.height} (${locale})`, async ({ page }) => {
      await page.setViewportSize(size);
      await openRoom(page, locale);
      const card = page.locator('.osHqCard');
      await expect(card).toContainText(locale === 'ar' ? 'أدوار مشغولة' : 'roles staffed');
      await card.scrollIntoViewIfNeeded();
      expect(await noOverflow(page)).toBe(true);
      expect(await serious(page, '.osHqCard')).toEqual([]);
    });
  }
}
