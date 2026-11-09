import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { mockTax } from './support/businessTaxApi';
import { useSavedLocale } from './support/workspaceApi';

// Business tax (docs/business-tax.md): tax settings with the registration number checked, the rate table, the VAT return
// with its warnings, documents and CSV, a failure and Try again, the ZATCA readiness panel with the not-verified notice,
// generating XML and the refused submission, a member's read-only view, Arabic right to left, the gate at five sizes.
// Figures, sellers and buyers are SYNTHETIC.
async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

async function noOverflow(page: Page, where: string) {
  expect(await horizontalOverflow(page), `${where}: horizontal page overflow`).toBeLessThanOrEqual(0);
  expect(await clippedControls(page), `${where}: clipped controls`).toEqual([]);
}

const part = (page: Page, name: string) => page.getByRole('navigation', { name: 'Tax sections' }).getByRole('button', { name, exact: true });

test('an owner sets up VAT with a checked number, then reads the return, its documents and the CSV', async ({ page }) => {
  await mockBooks(page);
  const mock = await mockTax(page, { settings: false });
  await page.goto('/#business-books/tax');
  await expect(page.getByText('Tax is not set up yet. Choose the jurisdiction and save to start the rate table.')).toBeVisible();
  await expect(page.getByText(/^Not verified with ZATCA\. ANUM does not claim compliance/).first()).toBeVisible();
  await page.getByLabel('VAT registration number').fill('123456789012345');
  await page.getByRole('checkbox', { name: 'The business is registered for VAT' }).check();
  await page.getByRole('button', { name: 'Save tax settings' }).click();
  await expect(page.getByText('A Saudi VAT number is 15 digits that start and end with 3.')).toBeVisible();
  await page.getByLabel('VAT registration number').fill('300000000000003');
  await page.getByRole('button', { name: 'Save tax settings' }).click();
  await expect(page.getByText('Tax settings saved.')).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'PUT')?.body?.zatca_enabled).toBe(false);
  await expect(page.getByRole('row', { name: /Standard rated.*from 1 July 2020.*15%/ })).toBeVisible();

  await part(page, 'VAT return').click();
  await page.getByLabel('Filing period').selectOption({ index: 2 });
  const boxes = page.getByRole('table', { name: 'VAT return boxes for the period' });
  await expect(boxes.getByRole('row', { name: /^1 Standard rated sales/ })).toContainText('1,800.00');
  await expect(boxes.getByRole('row', { name: /^9 Imports subject to VAT through the reverse charge/ })).toContainText('150.00');
  await expect(page.getByText('Documents with VAT but no tax code, counted as standard rated: 2. Set their tax code to confirm.')).toBeVisible();
  await expect(page.getByText('The VAT in the return plus manual entries equals the movement on the VAT accounts.')).toBeVisible();
  await page.getByRole('button', { name: 'Documents (30) of box 1' }).click();
  const drawer = page.getByRole('dialog', { name: 'Documents of box 1' });
  await expect(drawer.getByRole('row', { name: /INV-2026-0001/ })).toContainText('Earlier period');
  await expect(drawer.getByRole('row', { name: /INV-2026-0002/ })).toContainText('Inferred');
  await drawer.getByRole('button', { name: /Show more/ }).click();
  await expect(drawer.getByRole('row', { name: /INV-2026-0030/ })).toBeVisible();
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^vat-return-.*\.csv$/);
  await noSeriousAxe(page);
});

test('the return says what failed and reads again; an empty period offers the next step', async ({ page }) => {
  await mockBooks(page);
  await mockTax(page, { failReturn: 1, emptyReturn: true });
  await page.goto('/#business-books/tax');
  await expect(page.getByText(/The return could not be read/)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await expect(page.getByRole('heading', { name: 'Nothing in this period' })).toBeVisible();
});

test('e-invoicing readiness shows the notice and what is missing, generates XML and never sends to ZATCA', async ({ page }) => {
  await mockBooks(page);
  const mock = await mockTax(page, { zatca: true });
  await page.goto('/#business-books/tax');
  await part(page, 'E-invoicing (ZATCA)').click();
  await expect(page.getByRole('note').filter({ hasText: 'Not verified with ZATCA. ANUM does not claim compliance until the business completes onboarding and the invoices pass ZATCA validation.' })).toBeVisible();
  await expect(page.getByText('E-invoicing: On. Onboarding with ZATCA: not started.')).toBeVisible();
  await expect(page.getByText('Cryptographic stamp identifier, CSID (Fatoora portal OTP and CSR)')).toBeVisible();
  await page.getByRole('button', { name: 'Generate XML for INV-2026-0001' }).click();
  const row = page.getByRole('row', { name: /INV-2026-0001/ });
  await expect(row.getByText(/Counter 1, hash/)).toBeVisible();
  const xml = page.waitForEvent('download');
  await row.getByRole('button', { name: 'Download XML of INV-2026-0001' }).click();
  expect((await xml).suggestedFilename()).toBe('INV-2026-0001.xml');
  await row.getByRole('button', { name: 'Send to ZATCA: INV-2026-0001' }).click();
  await expect(page.getByText(/needs onboarding first/)).toBeVisible();
  expect(mock.calls.filter((call) => call.path.endsWith('/submit'))).toHaveLength(1);
  await expect(page.getByText('Lines without a tax code: 1')).toBeVisible();
  await noSeriousAxe(page);
});

test('a member reads tax but cannot change it, and Arabic reads right to left', async ({ page }) => {
  await mockBooks(page, { role: 'member' });
  await mockTax(page, { canEdit: false, zatca: false });
  await page.goto('/#business-books/tax');
  await part(page, 'Settings').click();
  await expect(page.getByText('Only an owner or an accountant of this business can change the tax settings and rates.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save tax settings' })).toHaveCount(0);
  await part(page, 'E-invoicing (ZATCA)').click();
  await expect(page.getByText('E-invoicing is off. An owner or an accountant of a Saudi business turns it on in Settings.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Generate XML/ })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await useSavedLocale(page, 'ar');
  await mockBooks(page);
  await mockTax(page, { zatca: true });
  await page.goto('about:blank');
  await page.goto('/#business-books/tax');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('table', { name: 'خانات الإقرار الضريبي للفترة' })).toBeVisible();
  await noSeriousAxe(page);
  await page.getByRole('navigation', { name: 'أقسام الضريبة' }).getByRole('button', { name: 'الفوترة الإلكترونية (هيئة الزكاة)' }).click();
  await expect(page.getByText(/لا يدعي ANUM الامتثال حتى تكمل المنشأة التسجيل/)).toBeVisible();
  await noSeriousAxe(page);
});

test('responsive gate: the VAT return, settings and e-invoicing at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(300_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page);
    await mockTax(page, { zatca: true });
    const tabs = locale === 'en' ? ['VAT return', 'Settings', 'E-invoicing (ZATCA)'] : ['الإقرار الضريبي', 'الإعدادات', 'الفوترة الإلكترونية (هيئة الزكاة)'];
    const nav = locale === 'en' ? 'Tax sections' : 'أقسام الضريبة';
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#business-books/tax');
      await page.reload();
      await expect(page.locator('.taxBoxes')).toBeVisible();
      await noOverflow(page, `${where} return`);
      await page.getByRole('navigation', { name: nav }).getByRole('button', { name: tabs[1], exact: true }).click();
      await expect(page.locator('.taxForm')).toBeVisible();
      await noOverflow(page, `${where} settings`);
      await page.getByRole('navigation', { name: nav }).getByRole('button', { name: tabs[2], exact: true }).click();
      await expect(page.locator('.taxMissing')).toBeVisible();
      await noOverflow(page, `${where} e-invoicing`);
    }
  }
});
