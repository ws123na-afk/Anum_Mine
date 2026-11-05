import { expect, test, type Page } from '@playwright/test';
import { mockLabApi } from './support/financeLab';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Paper trading and portfolio lab (docs/finance.md#paper-trading-lab): the PAPER MODE banner, SIMULATED
// figures, equity against buy and hold, allocation, sortable simulated orders with a new order reviewed with its
// code, the research brief with evidence, broker status with a connection test, the strategy lab, risk caps, the
// kill switch, the latest backtest, live execution locked with its real gates, the tabs, every load state, Arabic
// and the responsive gate. The API is mocked at http://localhost:8000/**; values are synthetic.
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the overview shows simulated figures and every card from the real records', async ({ page }) => {
  await mockLabApi(page);
  await page.goto('/#finance-trading');
  const lab = page.getByRole('region', { name: 'Paper trading and portfolio lab' });
  await expect(lab.getByRole('note').filter({ hasText: 'PAPER MODE · SIMULATED ORDERS ONLY' })).toContainText('No live orders are sent');
  const figures = lab.getByRole('region', { name: 'Simulated portfolio figures' });
  await expect(figures).toContainText('USD 9,998.50');
  await expect(figures).toContainText('-0.01%');
  await expect(figures).toContainText('1 running, 1 paused');
  await expect(lab.getByRole('img', { name: 'Simulated P&L over time, in USD' })).toBeVisible();
  await expect(lab.getByTestId('lab-benchmark-line')).toHaveCount(1);
  await expect(lab.getByRole('list', { name: 'Allocation shares' })).toContainText('EXMPL');
  const orders = lab.getByRole('table', { name: 'Latest simulated orders in Practice' });
  await expect(orders).toContainText('Filled (paper)');
  await orders.getByRole('button', { name: 'Symbol' }).click();
  await expect(orders.getByRole('columnheader', { name: 'Symbol' })).toHaveAttribute('aria-sort', 'ascending');
  const brief = lab.getByRole('region', { name: 'Research brief' });
  await expect(brief).toContainText('rose 14.2 percent');
  await expect(brief).toContainText('1 source unavailable');
  await expect(brief.getByRole('link', { name: /Stooq daily prices/ })).toHaveAttribute('href', 'https://stooq.com');
  await expect(lab.getByRole('region', { name: 'Strategy lab' })).toContainText('Paper suspended');
  await expect(lab.getByRole('region', { name: 'Risk caps' })).toContainText('USD 500.00');
  await expect(lab.getByRole('region', { name: 'Strategy backtest' })).toContainText('+34.20%');
  await expect(lab.getByRole('region', { name: 'Strategy backtest' })).toContainText('not a guarantee');
  const live = lab.getByRole('region', { name: 'Live execution' });
  await expect(live).toContainText('Locked');
  await expect(live).toContainText('3 of 9 gates met');
  await expect(live).toContainText('The paper period is complete (not met)');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a new simulated order runs only after review, and the broker connection is tested', async ({ page }) => {
  const { sent } = await mockLabApi(page);
  await page.goto('/#finance-trading');
  const lab = page.getByRole('region', { name: 'Paper trading and portfolio lab' });
  await lab.getByRole('button', { name: 'New simulated order' }).first().click();
  const form = page.getByRole('dialog', { name: 'New simulated order' });
  await form.getByLabel('Symbol').fill('EXMPL');
  await form.getByLabel('Quantity').fill('2');
  await form.getByRole('button', { name: 'Check and review' }).click();
  const review = page.getByRole('dialog', { name: 'Review the request' });
  await expect(review).toContainText('SIMULATED');
  await review.getByRole('button', { name: 'Approve this simulated order' }).click();
  await expect(page.getByText('Approved and filled in the simulation. No real money moved.')).toBeVisible();
  expect((sent.find((r) => r.path.endsWith('/approve'))?.body as { payload_hash: string }).payload_hash).toMatch(/^[0-9a-f]{64}$/);
  expect(sent.some((r) => r.path.includes('/ibkr/') && r.method === 'POST' && r.path.endsWith('/orders'))).toBe(false);

  const broker = lab.getByRole('region', { name: 'Broker status' });
  await expect(broker).toContainText('Log in to IBKR again');
  await broker.getByRole('button', { name: 'Test connection' }).click();
  await expect(broker).toContainText('Connection checked: Logged in.');
  await lab.getByRole('button', { name: 'Open paper orders' }).click();
  await expect(page).toHaveURL(/#finance-trading-orders$/);
  await expect(lab.getByRole('tab', { name: 'Paper orders' })).toHaveAttribute('aria-selected', 'true');
  await page.goBack();
  await expect(lab.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
});

test('the kill switch needs a confirmation; links open Execution and Guardian', async ({ page }) => {
  const { sent } = await mockLabApi(page);
  await page.goto('/#finance-trading');
  const lab = page.getByRole('region', { name: 'Paper trading and portfolio lab' });
  await lab.getByRole('button', { name: 'Stop everything' }).first().click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel' }).click();
  expect(sent.some((r) => r.path.endsWith('/kill-switch'))).toBe(false);
  await lab.getByRole('button', { name: /Audit events/ }).click();
  await expect(page).toHaveURL(/#finance-execution$/);
  await page.goto('/#finance-trading');
  await page.getByRole('button', { name: /Risk review/ }).click();
  await expect(page).toHaveURL(/#finance-guardian$/);
});

test('no broker, no strategies, a failed overview with retry, errors and permission denied', async ({ page }) => {
  await mockLabApi(page, { connected: false, empty: true, failIntel: true });
  await page.goto('/#finance-trading');
  const lab = page.getByRole('region', { name: 'Paper trading and portfolio lab' });
  await expect(lab.getByRole('heading', { name: 'No practice portfolio yet' })).toBeVisible();
  await expect(lab.getByRole('region', { name: 'Broker status' })).toContainText('No broker connected.');
  await expect(lab.getByRole('region', { name: 'Broker status' }).getByRole('button', { name: 'Connect broker' })).toBeVisible();
  await expect(lab.getByRole('heading', { name: 'No strategy yet' })).toBeVisible();
  await expect(lab.getByRole('heading', { name: 'No fund to research yet' })).toBeVisible();
  await expect(lab.getByRole('region', { name: 'Risk caps' }).getByRole('alert')).toContainText('The risk caps could not be loaded');
  await expect(lab.getByRole('region', { name: 'Risk caps' }).getByRole('button', { name: 'Try again' })).toBeVisible();

  await page.unrouteAll();
  await mockLabApi(page, { failTrading: true });
  await page.reload();
  await expect(page.getByRole('alert').filter({ hasText: 'Paper trading could not be loaded' })).toBeVisible();

  await page.unrouteAll();
  await mockLabApi(page, { denied: true });
  await page.reload();
  await expect(page.getByText(/viewer to member/).first()).toBeVisible();
});

test('Arabic, right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockLabApi(page);
  await page.goto('/#finance-trading');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'التداول التجريبي ومختبر المحفظة' })).toBeVisible();
  await expect(page.getByRole('note').filter({ hasText: 'الوضع التجريبي' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'التنفيذ الحقيقي' })).toContainText('مقفل');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('@gate the lab fits at every size in both languages', async ({ page }) => {
  test.setTimeout(120_000);
  for (const locale of ['en', 'ar'] as const) {
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    await mockLabApi(page);
    for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
      await page.setViewportSize({ width, height });
      await page.goto('/#finance-trading');
      await page.reload();
      await expect(page.getByRole('region', { name: locale === 'en' ? 'Simulated portfolio figures' : 'أرقام المحفظة المحاكاة' })).toBeVisible();
      expect(await noHorizontalOverflow(page), `${locale} ${width}x${height}`).toBe(true);
    }
    await page.unrouteAll();
  }
});
