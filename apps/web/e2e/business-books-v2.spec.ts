import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { useSavedLocale } from './support/workspaceApi';

// Business books, second release (docs/business-books.md#web): the combined books of the businesses an owner owns
// (a column each, eliminations and the total, what is not converted and the rates used), recurring bills (create,
// the bills coming, a draft approved, pause, resume, end), automatic ECB rates (turned on, fetched, a failure shown),
// a member who sees neither the combined books nor owner actions, Arabic right to left, and the gate at five sizes.
async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function noOverflow(page: Page, where: string) {
  expect(await horizontalOverflow(page), `${where}: horizontal page overflow`).toBeLessThanOrEqual(0);
  expect(await clippedControls(page), `${where}: clipped controls`).toEqual([]);
}

const books = (page: Page, name: string) => page.getByRole('navigation', { name: 'Books sections' }).getByRole('button', { name, exact: true });

test('the combined books show a column per business, the eliminations, the total and what is not converted', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books');
  await books(page, 'All businesses').click();
  const pnl = page.locator('.bkGroup-pnl');
  await expect(pnl.getByRole('columnheader', { name: 'Example ANUM Business' })).toBeVisible();
  await expect(pnl.getByRole('columnheader', { name: 'Eliminations' })).toBeVisible();
  await expect(pnl.getByRole('row', { name: /^Other income/ })).toContainText('-SAR 2,000.00');
  await expect(pnl.getByRole('row', { name: /^Net profit/ }).locator('td').last()).toContainText('7,680.00');
  await expect(page.locator('.bkGroup-balances').getByRole('row', { name: /^Related businesses owe/ }).locator('td').last()).toContainText('0.00');
  await expect(page.getByText(/taken out of the total \(the Eliminations column\)/)).toBeVisible();
  await expect(page.getByText(/Not converted \(no exchange rate/)).toBeVisible();
  await expect(page.getByText(/Example Tyre Business: Operating expenses, EUR\s*90\.00/)).toBeVisible();
  await expect(page.getByText(/Example Tyre Business: 1 AED = 1\.0211 SAR/)).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Example ANUM Business' })).toBeDisabled();
  await page.getByLabel('Reporting currency').selectOption('USD');
  await expect.poll(() => mock.calls.some((c) => c.path.endsWith('/group') && c.method === 'GET')).toBe(true);
  await expect(page.getByRole('heading', { name: 'All businesses in USD' })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^group-pnl-.*\.csv$/);
  await noSeriousAxe(page);
});

test('combined books: one business only, a failure and Try again, and a member never sees them', async ({ page }) => {
  await mockBooks(page, { group: 'one' });
  await page.goto('/#business-books/group');
  await expect(page.getByRole('heading', { name: 'Combined books need two businesses' })).toBeVisible();
  await expect(page.getByText('Not included because their books are not set up: Example Tyre Business.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check again' })).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { group: 'fail' });
  await page.goto('about:blank');
  await page.goto('/#business-books/group');
  await expect(page.getByText('The combined books could not be read.')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.locator('.bkGroup-pnl')).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { role: 'member' });
  await page.goto('about:blank');
  await page.goto('/#business-books/group');
  await expect(books(page, 'All businesses')).toHaveCount(0);
  await expect(page.getByText('Only workspace owners see the combined books of the businesses they own.')).toBeVisible();
});

test('more businesses than one view holds: the left-out one is named and can be chosen instead', async ({ page }) => {
  const mock = await mockBooks(page, { group: 'many' });
  await page.goto('/#business-books/group');
  await expect(page.getByText('One view holds 3 businesses. Not included: Example Store Business. Tick the ones you want above.')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Example Store Business' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Example Store Business' })).toBeDisabled();
  await page.getByRole('checkbox', { name: 'Example Parts Business' }).uncheck();
  await page.getByRole('checkbox', { name: 'Example Store Business' }).check();
  await expect(page.locator('.bkGroup-pnl').getByRole('columnheader', { name: 'Example Store Business' })).toBeVisible();
  await expect(page.getByText(/Not included: Example Store Business/)).toHaveCount(0);
  const last = mock.calls.filter((c) => c.path.endsWith('/group')).at(-1);
  expect(last).toBeTruthy();
});

test('a recurring bill is made, its first bill appears, and it is paused, resumed and ended', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books/recurring');
  await expect(page.getByText('1 recurring bill')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Bills coming in the next 90 days' })).toBeVisible();
  await page.getByRole('button', { name: 'New recurring bill' }).first().click();
  const form = page.getByRole('dialog', { name: 'New recurring bill' });
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText('Choose the vendor.')).toBeVisible();
  await form.getByRole('combobox', { name: /^Vendor/ }).selectOption('bven_fuel');
  await form.getByLabel('What it is for').fill('Generator diesel');
  await form.getByLabel('Amount before tax').fill('300');
  await form.getByLabel('Repeats').selectOption('weeks');
  await form.getByLabel('Every how many weeks').fill('60');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText(/1 to 52 weeks/)).toBeVisible();
  await form.getByLabel('Every how many weeks').fill('2');
  await form.getByLabel('First bill on').fill('2026-11-01');
  await form.getByRole('radio', { name: 'After a number of bills' }).check();
  await form.getByRole('textbox', { name: 'Number of bills' }).fill('6');
  await form.getByRole('radio', { name: /Drafts/ }).check();
  await form.getByRole('button', { name: 'Save' }).click();
  const drawer = page.getByRole('dialog', { name: /Example Fuel Station, Generator diesel/ });
  await expect(drawer.getByText('Makes drafts')).toBeVisible();
  const created = mock.calls.find((c) => c.method === 'POST' && c.path.endsWith('/recurring'));
  expect(created?.body).toMatchObject({ vendor_id: 'bven_fuel', frequency: 'weeks', interval: 2, max_count: 6, end_date: null, create_as: 'draft', subtotal: '300' });
  // Its first bill is a draft: approved from the bill itself.
  await drawer.getByRole('list').getByRole('button').first().click();
  const bill = page.getByRole('dialog', { name: /Example Fuel Station/ }).last();
  await expect(bill.getByText(/A draft made by a recurring bill/)).toBeVisible();
  await bill.getByRole('button', { name: 'Approve bill' }).click();
  await expect(page.getByText('Bill approved. It counts in the books from now on.')).toBeVisible();
  await bill.getByRole('button', { name: 'Close' }).first().click();
  await drawer.getByRole('button', { name: 'Pause' }).click();
  await expect(drawer.getByText('Paused. No bill is made until it resumes.')).toBeVisible();
  await drawer.getByRole('button', { name: 'Resume' }).click();
  await expect(drawer.getByText('Resumed from the next period.')).toBeVisible();
  await drawer.getByRole('button', { name: 'End' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'End this recurring bill?' });
  await confirm.getByRole('button', { name: 'End' }).click();
  await expect(drawer.getByText('Ended. Bills already made stay as they are.')).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Edit recurring bill' })).toHaveCount(0);
  await noSeriousAxe(page);
});

test('the profit and loss lists the recurring costs and the bills coming', async ({ page }) => {
  await mockBooks(page);
  await page.goto('/#business-books');
  const card = page.locator('section[aria-labelledby="bk-recurring"]');
  await expect(card.getByText(/^next /)).toBeVisible();
  await expect(card.getByRole('heading', { name: 'Bills coming in the next 90 days' })).toBeVisible();
  await expect(card.getByText(/Office rent/).first()).toBeVisible();
});

test('automatic ECB rates are turned on by an owner, fetched now, and a failed fetch says the rates are kept', async ({ page }) => {
  const mock = await mockBooks(page);
  await page.goto('/#business-books/settings');
  const card = page.locator('section[aria-labelledby="bk-feed"]');
  await expect(card.getByText('Off', { exact: true })).toBeVisible();
  await card.getByRole('checkbox', { name: 'Fetch the ECB rates every day' }).check();
  await card.getByRole('checkbox', { name: 'USD' }).check();
  await card.getByRole('button', { name: 'Save' }).click();
  await expect(card.getByText('Saved.')).toBeVisible();
  expect(mock.calls.find((c) => c.method === 'PUT' && c.path.endsWith('/rates/auto'))?.body).toEqual({ enabled: true, currencies: ['USD'], version: null });
  await card.getByRole('button', { name: 'Fetch now' }).click();
  await expect(card.getByText('Fetched: 1 new rate.')).toBeVisible();
  await expect(card.getByText(/No automatic rate for KWD: the ECB does not publish them/)).toBeVisible();
  await expect(card.getByText('ECB', { exact: true }).first()).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockBooks(page, { feedFails: true });
  await page.goto('about:blank');
  await page.goto('/#business-books/settings');
  const again = page.locator('section[aria-labelledby="bk-feed"]');
  await again.getByRole('checkbox', { name: 'Fetch the ECB rates every day' }).check();
  await again.getByRole('button', { name: 'Save' }).click();
  await again.getByRole('button', { name: 'Fetch now' }).click();
  await expect(again.getByText(/The fetch failed: The source answered 503/)).toBeVisible();
  await expect(again.getByText(/The rates already stored are still used/).first()).toBeVisible();
});

test('a member reads recurring bills and the rate feed but cannot change the feed', async ({ page }) => {
  await mockBooks(page, { role: 'member' });
  await page.goto('/#business-books/settings');
  const card = page.locator('section[aria-labelledby="bk-feed"]');
  await expect(card.getByText('Only workspace owners can change this. You can read everything.')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Fetch now' })).toHaveCount(0);
  await books(page, 'Recurring bills').click();
  await expect(page.getByRole('button', { name: 'New recurring bill' }).first()).toBeVisible();
});

test('Arabic reads right to left in the combined books and recurring bills', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockBooks(page);
  await page.goto('/#business-books/group');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('columnheader', { name: 'الاستبعادات' }).first()).toBeVisible();
  await noSeriousAxe(page);
  await page.goto('/#business-books/recurring');
  await expect(page.getByRole('heading', { name: 'الفواتير القادمة خلال 90 يومًا' })).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: combined books, recurring bills with one open and the rate feed at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(240_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#business-books/group');
      await page.reload();
      await expect(page.locator('.bkGroup-pnl')).toBeVisible();
      await noOverflow(page, `${where} combined books`);
      await page.goto('/#business-books/recurring');
      await expect(page.locator('.bzTable tbody tr').first()).toBeVisible();
      await noOverflow(page, `${where} recurring bills`);
      await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
      await expect(page.locator('.drawerPanel .bzFacts')).toBeVisible();
      await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState !== 'running'));
      await noOverflow(page, `${where} recurring bill`);
      await page.keyboard.press('Escape');
      await page.goto('/#business-books/settings');
      await expect(page.locator('#bk-feed')).toBeVisible();
      await noOverflow(page, `${where} settings`);
    }
  }
});
