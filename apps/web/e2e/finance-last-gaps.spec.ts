import { expect, test, type Page, type Route } from '@playwright/test';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { mockTax } from './support/businessTaxApi';
import { useSavedLocale } from './support/workspaceApi';

// Dismissing a receipt proposed for a bill (Show dismissed receipts, Restore) and the VAT filing deadline in Tax settings,
// with the review item that asks for it when no deadline is known (docs/business-books.md#receipts-to-bills,
// docs/business-tax.md#filing-deadlines). The API is mocked at http://localhost:8000/** like the other specs; vendors,
// receipts and amounts are SYNTHETIC. Nothing is dismissed or restored until the person presses the button.

async function reviewRoutes(page: Page) {
  const calls: string[] = [];
  let dismissed = false;
  const receipt = { key: 'receipt_match:bbill_1:vdoc_1', producer: 'receipts', kind: 'receipt_match', detected: '', params: { document_id: 'vdoc_1',
    document: 'Example rent receipt', bill_id: 'bbill_1', reference: 'R-1', vendor: 'Example Landlord', merchant: 'EXAMPLE LANDLORD', receipt_date: '2026-11-03',
    vendor_agrees: '1', confidence: '0.9' }, severity: 'warning', occurred_on: '2026-11-03', amount: '1150.00', currency: 'SAR', ref_id: 'bbill_1', section: 'bills',
    action: 'link', can_act: true, why_not: null };
  const missing = { key: 'vat:filing_deadline_missing', producer: 'tax', kind: 'vat', detected: '', params: { code: 'filing_deadline_missing', from: '2026-07-01',
    to: '2026-09-30' }, severity: 'warning', occurred_on: '2026-11-10', amount: null, currency: null, ref_id: null, section: 'tax', action: 'open', can_act: true, why_not: null };
  const row = { id: 'brdis_1', bill_id: 'bbill_1', document_id: 'vdoc_1', dismissed_by: 'user_local', dismissed_at: '2026-11-10T08:00:00Z', bill_reference: 'R-1',
    vendor: 'Example Landlord', bill_date: '2026-11-03', amount: '1150.00', currency: 'SAR', document_title: 'Example rent receipt', bill_found: true, document_found: true };
  const month = (start: string, end: string, status: string) => ({ month: start.slice(0, 7), start, end, status, issues: 0, drafts: 0, allocations_in_review: 0,
    adjustments_pending: 0, ready: false });
  await page.route('http://localhost:8000/api/v1/business/books/**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/business/books', '');
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/review' && request.method() === 'GET') {
      const items = dismissed ? [missing] : [missing, receipt];
      const counts: Record<string, number> = {};
      for (const i of items) counts[i.kind] = (counts[i.kind] ?? 0) + 1;
      return json({ items, total: items.length, limit: 25, offset: 0, counts, blocking: 0, closed_through: '2026-09-30', to_post: 0,
        close_months: [month('2026-10-01', '2026-10-31', 'open'), month('2026-11-01', '2026-11-30', 'not_ended')], producers: ['tax', 'receipts'], unavailable: [] });
    }
    if (path === '/evidence/receipt-matches/dismiss') { calls.push(`dismiss ${request.postData()}`); dismissed = true; return json(row, 201); }
    if (path === '/evidence/receipt-matches/dismissed' && request.method() === 'GET') {
      const items = dismissed ? [row] : [];
      return json({ items, total: items.length, limit: 25, offset: 0 });
    }
    if (path === '/evidence/receipt-matches/dismissed/brdis_1' && request.method() === 'DELETE') { calls.push('restore brdis_1'); dismissed = false; return route.fulfill({ status: 204 }); }
    return route.fallback();
  });
  return calls;
}

test('an accountant dismisses a proposed receipt, finds it under Show dismissed receipts and restores it', async ({ page }) => {
  await mockBooks(page, { role: 'member', accountant: true });
  const calls = await reviewRoutes(page);
  await page.goto('/#business-books/review');
  const list = page.getByRole('region', { name: /items waiting/ });
  await expect(list.getByRole('listitem').filter({ hasText: 'VAT return' })).toContainText(/^.*Set your VAT filing period and due day in Tax settings\./);
  const receipt = list.getByRole('listitem').filter({ hasText: 'Receipt for a bill' });
  await expect(receipt.getByRole('button', { name: 'Attach' })).toBeVisible();
  expect(calls).toEqual([]);
  await receipt.getByRole('button', { name: 'Dismiss Example rent receipt for this bill' }).click();
  await expect(page.getByText('Dismissed Example rent receipt. It will not be proposed for this bill again.')).toBeVisible();
  expect(calls[0]).toContain('"bill_id":"bbill_1"');
  await expect(list.getByRole('listitem').filter({ hasText: 'Receipt for a bill' })).toHaveCount(0);

  const dismissed = page.getByRole('region', { name: 'Dismissed receipts' });
  await dismissed.getByRole('button', { name: 'Show dismissed receipts' }).click();
  await expect(dismissed.getByRole('listitem')).toContainText('Example rent receipt for the bill R-1 from Example Landlord');
  await dismissed.getByRole('button', { name: 'Restore Example rent receipt' }).click();
  await expect(page.getByText('Restored. The receipt can be proposed for this bill again.')).toBeVisible();
  expect(calls).toContain('restore brdis_1');
  await expect(dismissed.getByText('No dismissed receipts. A receipt you dismiss in the list above appears here.')).toBeVisible();
  await expect(list.getByRole('listitem').filter({ hasText: 'Receipt for a bill' })).toBeVisible();
});

test('an owner enters their own VAT filing period and due day for a country ANUM has no deadline for', async ({ page }) => {
  await mockBooks(page);
  const mock = await mockTax(page, { settings: false });
  await page.goto('/#business-books/tax');
  await page.getByLabel('Jurisdiction').selectOption('OTHER');
  await page.getByLabel('VAT registration number').fill('SYNTHETIC-1');
  await page.getByRole('checkbox', { name: 'The business is registered for VAT' }).check();
  await page.getByLabel('Filing country').fill('fr');
  await expect(page.getByText(/^ANUM knows no deadline for this country\. Enter the filing period and due day/)).toBeVisible();
  await page.getByLabel('Filing country').fill('OM');
  await expect(page.getByText(/^Without your own due day, the usual deadline applies: 30 days after the period ends\./)).toBeVisible();
  await page.getByLabel('Filing country').fill('KW');
  await expect(page.getByText(/^This country has no VAT, so ANUM knows no deadline\./)).toBeVisible();
  await page.getByLabel('Filing country').fill('FR');
  await page.getByLabel('Filing frequency').selectOption('monthly');
  await page.getByLabel(/VAT return due day/).fill('121');
  await page.getByRole('button', { name: 'Save tax settings' }).click();
  await expect(page.getByText('The due day is a whole number of days from 1 to 120.')).toBeVisible();
  await page.getByLabel(/VAT return due day/).fill('20');
  await expect(page.getByText(/^The Accountant review reminds you of each return, due 20 days after its period ends\. A reminder only/)).toBeVisible();
  await page.getByRole('button', { name: 'Save tax settings' }).click();
  await expect(page.getByText('Tax settings saved.')).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'PUT')?.body).toMatchObject({ jurisdiction: 'OTHER', country: 'FR', filing_frequency: 'monthly', filing_due_days: 20 });
});

test('@gate the dismiss controls and the filing settings fit at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(240_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockBooks(page, { role: 'member', accountant: true });
    await mockTax(page, { settings: false });
    await reviewRoutes(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#business-books/review');
      await page.reload();
      await expect(page.getByRole('button', { name: locale === 'en' ? /^Dismiss Example rent receipt/ : /^تجاهل .*Example rent receipt/ })).toBeVisible();
      await page.getByRole('button', { name: locale === 'en' ? 'Show dismissed receipts' : 'عرض الإيصالات المتجاهلة' }).click();
      expect(await horizontalOverflow(page), `${where} review`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `${where} review`).toEqual([]);
      await page.goto('/#business-books/tax');
      await page.reload();
      await page.locator('.taxForm select').first().selectOption('OTHER');
      await expect(page.getByLabel(locale === 'en' ? 'Filing country' : 'دولة الإقرار')).toBeVisible();
      expect(await horizontalOverflow(page), `${where} tax settings`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `${where} tax settings`).toEqual([]);
    }
  }
});
