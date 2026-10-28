import { expect, test, type Page } from '@playwright/test';
import { openHq } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';

// The room panel's desk rows stay inside the panel and the status badge is never cut short: the name and desk text give way instead.
const hq = (page: Page) => page.locator('.hqLive');
test.describe.configure({ timeout: 180_000 });

for (const [w, h] of [[1440, 900], [1024, 768], [390, 844]] as const) {
  test(`a desk row and its status badge fit the room panel at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openHq(page, { stream: 'live', beforeGoto: async (p) => { await mockFlow(p); } });
    await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
    await hq(page).locator('.hqLabels').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
    await hq(page).locator('.hqDetail button.hqPrimary').click();
    await expect(hq(page).locator('.hqNav[data-level="building"]')).toBeVisible({ timeout: 30_000 });
    await hq(page).locator('.hqLabels').getByRole('button', { name: /Alpha Room 1/ }).first().click();
    const dock = hq(page).locator('.hqDetailDock');
    const row = dock.locator('[data-testid="room-desk"]').first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    const fit = await row.evaluate((el) => {
      const panel = el.closest('.hqDetailDock')!.getBoundingClientRect(), box = el.getBoundingClientRect();
      const pill = el.querySelector<HTMLElement>('.hqPill');
      return { inside: box.left >= panel.left - 0.5 && box.right <= panel.right + 0.5, pillWhole: !pill || pill.scrollWidth <= pill.clientWidth, pill: pill?.textContent ?? '' };
    });
    expect(fit.inside, 'the desk row stays inside the room panel').toBe(true);
    expect(fit.pill).toContain('Working');
    expect(fit.pillWhole, 'the status badge is shown in full').toBe(true);
  });
}
