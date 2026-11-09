import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockAssets } from './support/businessAssetsApi';
import { mockBooks } from './support/businessBooksApi';
import { useSavedLocale } from './support/workspaceApi';

// Business accounting completion (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation,
// #group-balance-sheet): the register with its roll-forward, an asset's depreciation, impairment (maker and checker) and
// disposal, a loan with its schedule and a repayment, revaluation with a missing closing rate, the group balance sheet,
// a failure and Try again, a member's read-only view, Arabic right to left and the gate at five sizes. Figures are SYNTHETIC.
async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function noOverflow(page: Page, where: string) {
  expect(await horizontalOverflow(page), `${where}: horizontal page overflow`).toBeLessThanOrEqual(0);
  expect(await clippedControls(page), `${where}: clipped controls`).toEqual([]);
}

test('an owner reads the register, changes how an asset is depreciated, approves an impairment and records a disposal', async ({ page }) => {
  await mockBooks(page);
  const mock = await mockAssets(page);
  await page.goto('/#business-books/assets');
  await expect(page.getByRole('heading', { name: /Roll-forward/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /Cost at the end/ })).toContainText('1,800.00');
  const register = page.getByRole('table', { name: 'Fixed assets with cost, depreciation and net book value' });
  await expect(register.getByRole('row', { name: /Example forklift/ })).toContainText('Disposed of');
  await page.getByRole('button', { name: 'Open Example laptop' }).click();
  const drawer = page.getByRole('dialog', { name: 'Example laptop' });
  await drawer.getByRole('button', { name: /Approve the impairment of/ }).click();
  await expect(drawer.getByText('Approved')).toBeVisible();
  await drawer.getByLabel('Method').selectOption('reducing_balance');
  await drawer.getByRole('button', { name: 'Save depreciation' }).click();
  await expect(drawer.getByText('Enter the yearly rate of the reducing balance method, above 0 and at most 100.')).toBeVisible();
  await drawer.getByLabel('Yearly rate (%)').fill('30');
  await drawer.getByRole('button', { name: 'Save depreciation' }).click();
  await expect(drawer.getByText('Saved. The journal follows the new depreciation.')).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ method: 'reducing_balance', rate_pct: '30' });
  await drawer.getByLabel('Useful life (months)').fill('6');
  await drawer.getByRole('button', { name: 'Save depreciation' }).click();
  await expect(drawer.getByText(/already in a closed period/)).toBeVisible();
  await drawer.getByLabel('Sold for (SAR)').fill('500');
  await drawer.getByRole('button', { name: 'Record disposal' }).click();
  await expect(drawer.getByText(/Disposed of on/)).toBeVisible();
  await noSeriousAxe(page);
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^fixed-assets-.*\.csv$/);
});

test('the register says what failed and reads again; with no assets it says how one gets there', async ({ page }) => {
  await mockBooks(page);
  await mockAssets(page, { failAssets: 1, empty: true });
  await page.goto('/#business-books/assets');
  await expect(page.getByText('The register could not be read.')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.getByRole('heading', { name: 'No fixed assets yet' })).toBeVisible();
});

test('a loan shows its schedule against actual and a repayment is recorded with its split', async ({ page }) => {
  await mockBooks(page);
  const mock = await mockAssets(page);
  await page.goto('/#business-books/loans');
  await page.getByRole('button', { name: 'Open the loan from Example Bank' }).click();
  const drawer = page.getByRole('dialog', { name: 'Example Bank' });
  await expect(drawer.getByRole('table', { name: 'Schedule against actual' }).getByRole('row').nth(2)).toContainText('Overdue');
  await drawer.getByLabel('Amount (SAR)').fill('50000');
  await drawer.getByRole('button', { name: 'Record repayment' }).click();
  await expect(drawer.getByText(/more than is owed/)).toBeVisible();
  await drawer.getByLabel('Amount (SAR)').fill('1066.19');
  await drawer.getByRole('button', { name: 'Record repayment' }).click();
  await expect(drawer.getByText('Recorded: interest SAR 110.54, principal SAR 955.65.')).toBeVisible();
  expect(mock.calls.find((call) => call.path.endsWith('/repayments'))?.key).toBeTruthy();
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  await page.getByRole('button', { name: 'New loan' }).click();
  const form = page.getByRole('dialog', { name: 'New loan' });
  await form.getByLabel('Lender').fill('Example Second Bank');
  await form.getByLabel('Principal', { exact: true }).fill('6000');
  await form.getByLabel('Yearly interest rate (%)').fill('8');
  await form.getByLabel('Repaid').selectOption('quarterly');
  await form.getByLabel('Term (months)').fill('10');
  await form.getByRole('button', { name: 'Save loan' }).click();
  await expect(form.getByText(/whole number of payments/)).toBeVisible();
  await form.getByLabel('Term (months)').fill('12');
  await form.getByRole('button', { name: 'Save loan' }).click();
  await expect(page.getByRole('dialog', { name: 'Example Second Bank' })).toBeVisible();
  await noSeriousAxe(page);
});

test('revaluation shows each currency and names the one without a closing rate', async ({ page }) => {
  await mockBooks(page);
  await mockAssets(page, { missingRate: true });
  await page.goto('/#business-books/revaluation');
  await expect(page.getByText(/There is no AED rate on or before/)).toBeVisible();
  await expect(page.getByRole('table', { name: /Balances in USD/ }).getByRole('row', { name: /Unrealised gain or loss/ })).toContainText('5.00');
  await page.getByRole('button', { name: 'Revalue' }).click();
  await expect(page.getByText('Revalued, except AED: there is no closing rate. Record one in Settings, then revalue again.')).toBeVisible();
  await noSeriousAxe(page);
});

test('the group balance sheet balances with eliminations and a translation reserve', async ({ page }) => {
  await mockBooks(page);
  await mockAssets(page);
  await page.goto('/#business-books/group');
  const table = page.getByRole('table', { name: /Group balance sheet in SAR/ });
  await expect(table.getByRole('row', { name: /Translation reserve/ })).toContainText('50.00');
  await expect(table.getByRole('row', { name: /^Total assets/ })).toContainText('4,805.00');
  await expect(page.getByText('Assets equal liabilities and equity')).toBeVisible();
  await expect(page.getByText(/Example Tyre Business: 1 AED = 1.02 SAR/)).toBeVisible();
  await noSeriousAxe(page);
});

test('a member reads but cannot change, and Arabic reads right to left', async ({ page }) => {
  await mockBooks(page, { role: 'member' });
  await mockAssets(page, { canEdit: false });
  await page.goto('/#business-books/assets');
  await page.getByRole('button', { name: 'Open Example delivery van' }).click();
  await expect(page.getByText('Only an owner or an accountant of this business can change an asset. You can read everything.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save depreciation' })).toHaveCount(0);
  await page.goto('/#business-books/loans');
  await expect(page.getByRole('button', { name: 'New loan' })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await useSavedLocale(page, 'ar');
  await mockBooks(page);
  await mockAssets(page);
  await page.goto('about:blank');
  await page.goto('/#business-books/assets');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('table', { name: 'الأصول الثابتة مع التكلفة والإهلاك وصافي القيمة الدفترية' })).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: assets, loans, revaluation and the group balance sheet at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(300_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page);
    await mockAssets(page, { missingRate: true });
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      for (const [section, marker] of [['assets', '.bkAssets .dataTable'], ['loans', '.bkLoans .dataTable'], ['revaluation', '.bzCard .dataTable'], ['group', '.groupBs .dataTable']] as const) {
        await page.goto(`/#business-books/${section}`);
        await page.reload();
        await expect(page.locator(marker).first()).toBeVisible();
        await noOverflow(page, `${where} ${section}`);
      }
      if (size.width === 1440 || size.width === 390) {
        await page.goto('/#business-books/assets');
        await page.reload();
        await expect(page.locator('.bkAssets .dataTable').first()).toBeVisible();
        await page.screenshot({ path: `test-results/assets-${locale}-${size.width}.png`, fullPage: false });
      }
    }
  }
});
