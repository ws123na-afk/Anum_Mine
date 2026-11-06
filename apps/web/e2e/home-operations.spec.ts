import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockBooks } from './support/businessBooksApi';
import { mockVault } from './support/vaultApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Home, Operations overview (docs/home.md#operations-overview): the business month from the books, the fleet and its
// kilometres, the Attention center, fleet status, attendance, document expiry and purchases, each from its own module's
// API; a refused or failing module shows its own state while the rest loads; My day keeps the person's day; Arabic.
async function mockAll(page: Page, { books = 'ok', vehicles = 'ok' }: { books?: 'ok' | 'denied'; vehicles?: 'ok' | 'fail' } = {}) {
  await mockWorkspaceApi(page);
  await mockBooks(page);
  await mockVault(page);
  if (books === 'denied') {
    await page.route('http://localhost:8000/api/v1/business/books/**', (route) => route.fulfill({ status: 403, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'forbidden', message: 'Only owners and members read the business books.', details: [] } }) }));
  }
  if (vehicles === 'fail') {
    await page.route('http://localhost:8000/api/v1/vehicles/overview**', (route) => route.fulfill({ status: 503, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unavailable', message: 'The database is not answering.', details: [] } }) }));
  }
}

test('the overview shows the month, the fleet, what needs attention, attendance, documents and purchases', async ({ page }) => {
  await mockAll(page);
  await page.goto('/#home');
  await expect(page.getByRole('heading', { level: 1, name: 'Operations overview' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  const kpis = page.getByRole('region', { name: 'This month at a glance' });
  await expect(kpis).toContainText('SAR 21,900.00');
  await expect(kpis).toContainText('2 vehicles');
  await expect(kpis).toContainText('300 km');
  await expect(page.getByRole('list', { name: 'Spend by category' })).toContainText('Office rent');
  const attention = page.getByRole('region', { name: 'Attention center' });
  await expect(attention.getByRole('link', { name: /1 approval waiting/ })).toHaveAttribute('href', '#approvals');
  await expect(attention.getByRole('link', { name: /1 bill is overdue/ })).toHaveAttribute('href', '#business-books/bills');
  const fleet = page.getByRole('region', { name: 'Fleet status' });
  await expect(fleet.getByRole('row', { name: /ABC 1234/ })).toContainText('Due in 10 days');
  await expect(fleet.getByRole('row', { name: /ABC 1234/ })).toContainText('10.0 km/L');
  await expect(page.getByRole('region', { name: 'Document expiry' })).toContainText('Next expiry');
  await expect(page.getByRole('region', { name: 'Purchases and delivery' })).toContainText('On the way');
  await expect(page.getByRole('region', { name: 'Attendance' })).toContainText('This week');
  await expect(page.getByRole('region', { name: 'Recent activity' })).toBeVisible();
  // My day keeps the person's day; the tab is a link of its own.
  await page.getByRole('tab', { name: /My day/ }).click();
  await expect(page).toHaveURL(/#home-day$/);
  await expect(page.getByRole('region', { name: 'Today at a glance' })).toBeVisible();
  await page.getByRole('tab', { name: 'Overview' }).click();
  await attention.getByRole('link', { name: /1 bill is overdue/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Expenses and P&L' })).toBeVisible();
});

test('a refused module says who may see it and a failing one offers a retry, while the rest loads', async ({ page }) => {
  await mockAll(page, { books: 'denied', vehicles: 'fail' });
  await page.goto('/#home');
  await expect(page.getByRole('region', { name: 'Income vs. expenses' })).toContainText('The business books are for owners and members with Business access.');
  const fleet = page.getByRole('region', { name: 'Fleet status' });
  await expect(fleet).toContainText('Could not load this part: The database is not answering.');
  await expect(fleet.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Purchases and delivery' })).toContainText('On the way');
});

test('Arabic reads right to left with no serious accessibility issue', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockAll(page);
  await page.goto('/#home');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'نظرة عامة على العمليات' })).toBeVisible();
  await expect(page.locator('.opsHome .opsShares li').first()).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(', ')}`)).toEqual([]);
});
