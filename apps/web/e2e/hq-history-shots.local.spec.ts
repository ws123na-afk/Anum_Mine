import { expect, test } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockHistory } from './support/hqHistoryApi';

const OUT = '/tmp/claude-0/-home-user/61f032fb-c00f-5793-80f2-ca9f0fc13fd1/scratchpad/hq-shots-history';
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;

for (const [w, h] of SIZES) {
  for (const locale of ['en', 'ar'] as const) {
    test(`shots ${w}x${h} ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await openHq(page, { registry, locale, beforeGoto: async (p) => { await mockHistory(p); } });
      const hq = page.locator('.hqLive');
      await hq.getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
      await hq.getByRole('button', { name: locale === 'en' ? 'History' : 'السجل', exact: true }).click();
      const panel = page.getByTestId('hq-history');
      await expect(panel.locator('.hqHistRow').first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      await expect.poll(async () => { const b = await panel.boundingBox(); return b ? b.x >= -1 && b.x + b.width <= w + 1 : false; }, { timeout: 8000 }).toBe(true);
      await panel.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished.catch(() => undefined))));
      await page.screenshot({ path: `${OUT}/${w}x${h}-${locale}-timeline.png` });
      await panel.locator('.hqHistRow').first().click();
      await page.waitForTimeout(150);
      await panel.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished.catch(() => undefined))));
      await page.screenshot({ path: `${OUT}/${w}x${h}-${locale}-detail.png` });
      await panel.locator('.hqHistDetail > button').first().click();
      await panel.getByRole('tab').nth(1).click();
      await panel.locator('select').first().selectOption('Alpha-room-0');
      await expect(panel.getByRole('slider')).toBeVisible();
      await page.keyboard.press('Tab');
      await panel.getByRole('slider').focus();
      await page.keyboard.press('ArrowLeft');
      await page.waitForTimeout(150);
      await panel.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished.catch(() => undefined))));
      await page.screenshot({ path: `${OUT}/${w}x${h}-${locale}-slider-gap.png` });
    });
  }
}
