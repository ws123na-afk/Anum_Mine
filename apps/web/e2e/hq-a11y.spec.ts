import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { openHq } from './support/hqApi';

// F14 accessibility scans of the headquarters: English and Arabic, light and dark app theme,
// the office's own day and night lighting, and the no-WebGL renderer.
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'];

async function violations(page: import('@playwright/test').Page) {
  const results = await new AxeBuilder({ page }).include('.hqLive').withTags(TAGS).analyze();
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 4).map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    for (const lighting of ['day', 'night'] as const) {
      test(`axe: ${locale}, ${theme} theme, ${lighting} lighting`, async ({ page }) => {
        await openHq(page, { locale, theme });
        await expect(page.locator('.hqLive')).toBeVisible();
        await expect(page.locator('canvas.hqCanvas')).toBeVisible();
        if (lighting === 'night') await page.getByRole('button', { name: 'Night lighting' }).click();
        await expect(page.locator('.hqLive')).toHaveClass(lighting === 'night' ? /hqNight/ : /hqDay/);
        expect(await violations(page)).toEqual([]);
      });
    }
  }
}

test('axe: no-WebGL renderer with a room selected', async ({ page }) => {
  await openHq(page, { noWebgl: true });
  await expect(page.locator('.hqGpuFallback')).toBeVisible();
  // The room inspector is hidden below 768 px (issue register H-04), so only pick a room where it exists.
  const pick = page.getByRole('button', { name: /Alpha Room 2/ });
  if (await pick.isVisible()) await pick.click();
  expect(await violations(page)).toEqual([]);
});

test('axe: workstation panel open', async ({ page }) => {
  await openHq(page);
  await page.getByRole('button', { name: 'Live workstations' }).click();
  await expect(page.getByRole('dialog', { name: 'Live Employee Workstations' })).toBeVisible();
  expect(await violations(page)).toEqual([]);
});
