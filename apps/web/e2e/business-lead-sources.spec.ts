import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockBusiness } from './support/businessApi';
import { useSavedLocale } from './support/workspaceApi';

// One lead list (docs/business.md#whatsapp-leads): WhatsApp Sales CRM leads join Business leads with a
// WhatsApp source badge and the conversation link; a lead that may be the same person waits for the
// owner (link or keep apart), and duplicate pairs are merged only by an owner.
const at = '2026-10-20T08:00:00Z';
const waiting = (id: string, extra: Record<string, unknown> = {}) => ({
  id, contact_name: 'Sara', company_name: '', phone: '+971501234567', requirements: 'Need a bilingual website', stage: 'quoted', conversation_id: `conv_${id}`,
  campaign_hint: null, created_at: at, updated_at: at, suggestions: [], ...extra,
});
const pair = {
  first: { id: 'blead_a', title: 'Website for Sara Bakery', state: 'discovered', channel: 'whatsapp', score_total: 0, updated_at: at },
  second: { id: 'blead_b', title: 'Sara Bakery app', state: 'qualified', channel: null, score_total: 9, updated_at: at },
  reason: 'Same phone number.',
};

async function openLeads(page: Page, options: Parameters<typeof mockBusiness>[1] = {}) {
  const mock = await mockBusiness(page, options);
  await page.goto('/#business-leads');
  await expect(page.getByRole('heading', { level: 1, name: 'Business' })).toBeVisible();
  return mock;
}

test('waiting WhatsApp leads join the list with a WhatsApp badge; a possible match waits for the owner', async ({ page }) => {
  const mock = await openLeads(page, { whatsapp: [
    waiting('wa_1'),
    waiting('wa_2', { contact_name: 'Omar', phone: '+971509999999', suggestions: [{ lead_id: 'blead_x', title: 'Existing Omar lead', reason: 'Same phone number as this lead\'s contact.' }] }),
  ] });
  const panel = page.getByRole('region', { name: /WhatsApp leads are not in this list yet/ });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('WhatsApp stage: Quoted').first()).toBeVisible();
  await expect(panel.getByRole('link', { name: 'Open the conversation' }).first()).toHaveAttribute('href', '#whatsapp/conv_wa_1');
  await expect(panel.getByText('Maybe the same person as:')).toBeVisible();
  await panel.getByRole('button', { name: 'Add the ones with no question' }).click();
  await expect(page.getByText('Added 1. 1 wait for your answer.')).toBeVisible();
  await expect(page.locator('.bzSourceBadge', { hasText: 'WhatsApp' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Same person: link' }).click();
  await expect(page.getByText('Omar was linked to the existing lead.')).toBeVisible();
  expect(mock.calls.find((call) => call.path === '/lead-sources/whatsapp/wa_2/map')?.body).toEqual({ link_to_lead_id: 'blead_x' });
  // The source filter narrows the list to WhatsApp leads.
  await page.getByLabel('Source').selectOption('whatsapp');
  await expect.poll(() => mock.calls.filter((call) => call.path === '/leads').at(-1)?.query).toContain('channel=whatsapp');
});

test('different people are added apart, and a failed load says so with Try again', async ({ page }) => {
  const mock = await openLeads(page, { failWhatsAppOnce: true, whatsapp: [waiting('wa_3', { suggestions: [{ lead_id: 'blead_y', title: 'Other Sara', reason: 'Same WhatsApp number.' }] })] });
  await expect(page.getByText('WhatsApp leads could not be loaded')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await page.getByRole('button', { name: 'Different people: add as a new lead' }).click();
  await expect(page.getByText('Sara is now a lead.')).toBeVisible();
  expect(mock.calls.find((call) => call.path === '/lead-sources/whatsapp/wa_3/map')?.body).toEqual({ separate_from: ['blead_y'] });
});

test('possible duplicates: owners merge after confirming, members may only keep them apart', async ({ page }) => {
  const mock = await openLeads(page, { duplicates: [pair] });
  const region = page.getByRole('region', { name: /pair of leads may be the same person/ });
  await expect(region.getByText('Same phone number.')).toBeVisible();
  page.once('dialog', (dialog) => void dialog.accept());
  await region.getByRole('button', { name: 'Merge, keep "Sara Bakery app"' }).click();
  await expect(page.getByText('Merged into "Sara Bakery app".')).toBeVisible();
  expect(mock.calls.find((call) => call.path === '/lead-duplicates/merge')?.body).toEqual({ keep_id: 'blead_b', merge_id: 'blead_a' });
});

test('members see the pair without a merge button', async ({ page }) => {
  await openLeads(page, { role: 'member', duplicates: [pair] });
  const region = page.getByRole('region', { name: /pair of leads may be the same person/ });
  await expect(region.getByText('Owners merge leads.')).toBeVisible();
  await expect(region.getByRole('button', { name: /Merge/ })).toHaveCount(0);
  await region.getByRole('button', { name: 'Different people' }).click();
  await expect(page.getByText('Kept apart. This pair will not be suggested again.')).toBeVisible();
});

test('with nothing waiting the panels stay out of the way', async ({ page }) => {
  await openLeads(page);
  await expect(page.getByRole('heading', { name: 'No leads yet' }).or(page.getByText('No leads yet'))).toBeVisible();
  await expect(page.getByRole('region', { name: /WhatsApp leads/ })).toHaveCount(0);
});

const SIZES = [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
for (const locale of ['en', 'ar'] as const) {
  test(`@gate the lead list with WhatsApp leads fits every gate size and passes axe (${locale})`, async ({ page }) => {
    await useSavedLocale(page, locale);
    const whatsapp = Array.from({ length: 30 }, (_, index) => waiting(`wa_${index}`, { contact_name: `Person ${index}`,
      suggestions: index === 0 ? [{ lead_id: 'blead_x', title: 'Existing lead with a long company name for wrapping checks', reason: 'Same phone number.' }] : [] }));
    await mockBusiness(page, { whatsapp, duplicates: [pair] });
    for (const size of SIZES) {
      await page.setViewportSize(size);
      await page.goto('about:blank');
      await page.goto('/#business-leads');
      // Thirty waiting leads stay folded so the lead list stays in view; Review opens them.
      await page.locator('.bzWaiting').first().getByRole('button', { expanded: false }).click();
      await expect(page.locator('.bzWaitingItem').first()).toBeVisible();
      await expect(page.locator('.bzWaiting')).toHaveCount(2);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${size.width}x${size.height}`).toBeLessThanOrEqual(1);
      const violations = (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
      expect(violations, `${size.width}x${size.height}`).toEqual([]);
      if (process.env.ANUM_SHOTS) await page.screenshot({ path: `${process.env.ANUM_SHOTS}/leads-${locale}-${size.width}x${size.height}.png` });
    }
  });
}
