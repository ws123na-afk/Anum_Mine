import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';

// A camera control (Reset view) pressed while the camera still flies to a picked building ends that flight: the view
// settles where the control put it and the building cards there can be clicked. The flight used to keep setting its
// frames over the reset, so the camera landed back on the picked building and the card stayed off the stage.
const hq = (page: Page) => page.locator('.hqLive');

test('Reset view during the flight to a picked building holds, and the cards there can be clicked', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'none', profile: 'ok' });
  await page.goto('/#office-preview');
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  // The flight to the building takes over a second; press Reset view while it is still under way (its details are open by then).
  await expect(hq(page).getByRole('complementary', { name: 'About Al Noor Fleet HQ' })).toBeVisible();
  await hq(page).getByRole('button', { name: 'Reset view' }).click();
  const card = hq(page).getByRole('button', { name: /^Personal Office\./ });
  await expect(card.locator('xpath=ancestor-or-self::*[@data-hidden][1]')).toHaveAttribute('data-hidden', 'no');
  await card.click();
  await expect(hq(page).getByRole('complementary', { name: 'About Personal Office' })).toBeVisible();
});
