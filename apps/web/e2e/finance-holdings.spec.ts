import { expect, test, type Page } from '@playwright/test';
import { mockHoldingsApi } from './support/financeHoldings';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Holdings (docs/finance.md#holdings): manual positions valued with the prices the person recorded.
// Figures, value over time, allocation, positions with search, sort and row actions, data quality with stale
// prices, recording a price, add, edit and delete, every load state, Arabic and the responsive gate. The API is
// mocked at http://localhost:8000/** like the other specs; values are synthetic.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const mockApi = mockHoldingsApi;

test('the summary values positions with recorded prices and flags what is missing or stale', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-holdings');
  const holdings = page.getByRole('region', { name: 'Holdings' });
  const figures = holdings.getByRole('region', { name: 'Portfolio figures' });
  // 120 x 100 + 77.2 x 100 + 42 x 100 = 23,920; NEWFND has no price.
  await expect(figures).toContainText('23,920.00');
  await expect(figures).toContainText('1 position has no price yet.');
  // Unrealised: 23,920 against 22,724 paid for the priced positions.
  await expect(figures).toContainText('+SAR 1,196.00');
  await expect(figures).toContainText('9,620.00');
  await expect(holdings.getByText(/fetches no market prices/)).toBeVisible();
  await expect(holdings.getByText(/not investment advice/)).toBeVisible();
  await expect(holdings.getByRole('img', { name: 'Portfolio value over time, in SAR' })).toBeVisible();
  await expect(holdings.getByRole('img', { name: 'Allocation by value, in SAR' })).toBeVisible();
  await expect(holdings.getByRole('list', { name: 'Allocation shares' })).toContainText('Broad market ETF');
  const quality = holdings.getByRole('region', { name: 'Data quality' });
  await expect(quality).toContainText('4 manual positions');
  await expect(quality).toContainText('1 price is older than 3 days');
  await expect(quality).toContainText('1 position has no price');
  const table = holdings.getByRole('table', { name: 'Your positions with value and gain' });
  await expect(table.getByRole('row').nth(1)).toContainText('Broad market ETF');
  await expect(table).toContainText('Stale');
  await expect(holdings.getByRole('table', { name: 'Recent changes to your holdings and prices' })).toContainText('Price recorded');
  // The table view of the chart has the same figures.
  await holdings.getByRole('button', { name: 'Show as table' }).click();
  await expect(holdings.getByRole('table', { name: 'Portfolio value by date, in SAR' })).toContainText('23,920.00');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a price is recorded, then a holding is added, edited and deleted', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-holdings');
  const holdings = page.getByRole('region', { name: 'Holdings' });
  await holdings.getByRole('button', { name: 'Record a price for New fund' }).click();
  const dialog = page.getByRole('dialog', { name: 'Record a price for NEWFND' });
  await dialog.getByLabel('Price (SAR)').fill('abc');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await dialog.getByLabel('Price (SAR)').fill('110');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Price for NEWFND saved.')).toBeVisible();
  expect(sent.find((r) => r.method === 'POST' && r.path.endsWith('/prices'))?.body).toMatchObject({ symbol: 'NEWFND', currency: 'SAR', price: '110' });
  await expect(holdings.getByRole('region', { name: 'Portfolio figures' })).toContainText('25,020.00');

  await holdings.getByRole('button', { name: 'Add holding' }).first().click();
  const add = page.getByRole('dialog', { name: 'Add holding' });
  await add.getByLabel('Symbol').fill('ADDED');
  await add.getByLabel('Quantity').fill('5');
  await add.getByLabel('Total cost (SAR)').fill('500');
  await add.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('ADDED saved.')).toBeVisible();

  await holdings.getByRole('tab', { name: 'Positions' }).click();
  await holdings.getByRole('searchbox', { name: 'Search by symbol, name or account' }).fill('gold');
  const table = holdings.getByRole('table', { name: 'Your positions with value and gain' });
  await expect(table.getByRole('row')).toHaveCount(2);
  await table.getByRole('button', { name: 'Edit Gold holding' }).click();
  const edit = page.getByRole('dialog', { name: 'Edit Gold holding' });
  await edit.getByLabel('Quantity').fill('50');
  await edit.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('GOLDX saved.')).toBeVisible();
  expect(sent.find((r) => r.method === 'PATCH')?.body).toMatchObject({ quantity: '50' });
  await table.getByRole('button', { name: 'Delete Gold holding' }).click();
  await page.getByRole('alertdialog', { name: 'Delete Gold holding?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Gold holding deleted.')).toBeVisible();
  expect(sent.some((r) => r.method === 'DELETE' && r.path.endsWith('/holdings/fin_h_gold'))).toBe(true);
});

test('positions sort and page; performance and valuation settings use the same records', async ({ page }) => {
  await mockApi(page, { many: true });
  await page.goto('/#finance-holdings');
  const holdings = page.getByRole('region', { name: 'Holdings' });
  await holdings.getByRole('tab', { name: 'Positions' }).click();
  const table = holdings.getByRole('table', { name: 'Your positions with value and gain' });
  await expect(table.getByRole('row')).toHaveCount(26);
  await expect(holdings.getByText('1 to 25 of 34')).toBeVisible();
  await holdings.getByRole('button', { name: 'Next' }).click();
  await expect(table.getByRole('row')).toHaveCount(10);
  await holdings.getByRole('button', { name: 'Previous' }).click();
  await table.getByRole('button', { name: 'Asset' }).click();
  await expect(table.getByRole('columnheader', { name: 'Asset' })).toHaveAttribute('aria-sort', 'ascending');
  await expect(table.getByRole('row').nth(1)).toContainText('Broad market ETF');

  await holdings.getByRole('tab', { name: 'Performance' }).click();
  await expect(holdings.getByRole('region', { name: 'Gain by position' })).toContainText('+5.26%');
  await holdings.getByRole('tab', { name: 'Valuation settings' }).click();
  await expect(holdings.getByRole('region', { name: 'How values are worked out' })).toContainText('older than 3 days is marked stale');
  await expect(holdings.getByRole('region', { name: 'Recorded prices' })).toContainText('BRDETF');
});

test('loading, a failed load with retry, no account, no holdings and permission denied', async ({ page }) => {
  await mockApi(page, { fail: true, slow: true });
  await page.goto('/#finance-holdings');
  await expect(page.getByText('Loading your holdings...')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Your holdings could not be loaded: The database is not reachable.');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('region', { name: 'Portfolio figures' })).toContainText('23,920.00');

  await page.unrouteAll();
  await mockApi(page, { noAccount: true });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Add an investment account first' })).toBeVisible();
  await page.getByRole('button', { name: 'Add account' }).last().click();
  await expect(page).toHaveURL(/#finance-accounts$/);

  await page.unrouteAll();
  await mockApi(page, { empty: true });
  await page.goto('/#finance-holdings');
  await expect(page.getByRole('heading', { name: 'No holdings yet' })).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, { denied: true });
  await page.reload();
  await expect(page.getByText(/viewer to member/).first()).toBeVisible();
});

test('Arabic, right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-holdings');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('region', { name: 'أرقام المحفظة' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'جودة البيانات' })).toContainText('مركز واحد بلا سعر');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('@gate holdings fit at every size in both languages', async ({ page }) => {
  test.setTimeout(120_000);
  for (const locale of ['en', 'ar'] as const) {
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    await mockApi(page);
    for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
      await page.setViewportSize({ width, height });
      await page.goto('/#finance-holdings');
      await page.reload();
      await expect(page.getByRole('region', { name: locale === 'en' ? 'Portfolio figures' : 'أرقام المحفظة' })).toBeVisible();
      expect(await noHorizontalOverflow(page), `${locale} ${width}x${height}`).toBe(true);
    }
    await page.unrouteAll();
  }
});
