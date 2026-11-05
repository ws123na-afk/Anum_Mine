import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { clippedControls } from './support/gatePages';
import { mockWhatsApp } from './support/whatsappApi';
import { useSavedLocale } from './support/workspaceApi';

// WhatsApp, Sales CRM: the figures come from the one lead list, Business leads with the WhatsApp
// source, plus the enquiries still waiting to join it (docs/business.md#whatsapp-leads).
const overview = {
  leads: 12, qualified: 5, quoted: 2, won: 1, lost: 3, waiting: 4, by_intent: { website: 9, ecommerce: 3 },
  source: 'business_leads', scope: 'all_whatsapp_leads', revenue_estimate: null,
};

async function mockSales(page: Page, { failOverviewOnce = false } = {}) {
  await mockWhatsApp(page);
  let fail = failOverviewOnce;
  const calls: string[] = [];
  await page.route('http://localhost:8000/api/v1/sales/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/sales', '');
    calls.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/overview') {
      if (fail) { fail = false; return json({ error: { code: 'service_unavailable', message: 'The database is not reachable.', correlation_id: 'c' } }, 503); }
      return json(overview);
    }
    if (path === '/campaign-conversions') return json({ source: 'self_reported', verified_attribution: false, campaigns: [] });
    if (path === '/operations') return json({ quotes: [], followups: [], delivery_enabled: false, execution_enabled: false });
    if (path === '/automation-settings') return json({ enabled: false, paused: false, max_sends_per_day: 20, updated_by: null, updated_at: null });
    if (path === '/automation-readiness') return json({ checks: [], configuration_ready: false, live_provider_verified: false, last_webhook_at: null, note: '' });
    return route.abort();
  });
  return calls;
}

async function openSales(page: Page) {
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: /WhatsApp|واتساب/ })).toBeVisible();
  await page.getByRole('button', { name: /^(Sales CRM|المبيعات)$/ }).click();
}

const figure = (page: Page, label: string) => page.locator('.salesKpi').filter({ hasText: label }).locator('strong');

test('the Sales overview counts WhatsApp leads in the one lead list and the ones still waiting', async ({ page }) => {
  const calls = await mockSales(page);
  await openSales(page);
  await expect(figure(page, 'WhatsApp leads')).toHaveText('12');
  await expect(figure(page, 'Offered')).toHaveText('2');
  await expect(figure(page, 'Waiting to join the list')).toHaveText('4');
  await expect(page.getByText(/Counted from Business, Leads \(WhatsApp source\)/)).toBeVisible();
  await expect(page.locator('.salesHint a[href="#business-leads"]').first()).toBeVisible();
  // The desk no longer reads the old WhatsApp lead list.
  expect(calls).not.toContain('/leads');
});

test('a failed load says what failed, shows no made-up figures, and Refresh recovers', async ({ page }) => {
  await mockSales(page, { failOverviewOnce: true });
  await openSales(page);
  await expect(page.getByRole('alert')).toContainText('database is not reachable');
  await expect(page.locator('.salesKpi')).toHaveCount(0);
  await page.locator('.salesHero').getByRole('button', { name: 'Refresh' }).click();
  await expect(figure(page, 'WhatsApp leads')).toHaveText('12');
});

const SIZES = [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
for (const locale of ['en', 'ar'] as const) {
  test(`@gate the Sales overview fits every gate size and passes axe (${locale})`, async ({ page }) => {
    await useSavedLocale(page, locale);
    await mockSales(page);
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto('about:blank');
      await openSales(page);
      await expect(figure(page, locale === 'ar' ? 'بانتظار الإضافة إلى القائمة' : 'Waiting to join the list')).toHaveText('4');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${size.width}x${size.height}`).toBeLessThanOrEqual(1);
      expect(await clippedControls(page), `${size.width}x${size.height}`).toEqual([]);
      const violations = (await new AxeBuilder({ page }).include('.salesDesk').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
      expect(violations, `${size.width}x${size.height}`).toEqual([]);
      if (process.env.ANUM_SHOTS) await page.screenshot({ path: `${process.env.ANUM_SHOTS}/sales-${locale}-${size.width}x${size.height}.png`, fullPage: false });
    }
  });
}
