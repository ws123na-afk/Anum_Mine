// Local screenshot runner for the decluttered headquarters (not part of CI): campus, a building cut away with every floor and with one
// floor, zoomed in like the owner's report, at six sizes. It prints the share of the viewport the stage takes and how many visible label
// boxes overlap. Usage: see playwright.hq.shots.config.ts.
//   HQ_PORT=4517 DECL_OUT=<dir> DECL_PREFIX=before pnpm exec playwright test -c playwright.hq.shots.config.ts e2e/tools/hqDeclutter.shots.ts --project=desktop
import { expect, test, type Page } from '@playwright/test';
import { mockHq } from '../support/hqCampusApi';

const OUT = process.env.DECL_OUT ?? '/tmp/declutter';
const PREFIX = process.env.DECL_PREFIX ?? 'shot';
const SIZES = [[1920, 1080], [1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 300_000 });

async function measure(page: Page) {
  return page.evaluate(() => {
    const stage = document.querySelector('.hqStage')?.getBoundingClientRect();
    const boxes = [...document.querySelectorAll<HTMLElement>('.hqLabels > *')].filter((n) => {
      const s = getComputedStyle(n);
      return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0.05 && n.dataset.hidden !== 'crowded';
    }).map((n) => n.getBoundingClientRect());
    let overlaps = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (a.left < b.right - 2 && b.left < a.right - 2 && a.top < b.bottom - 2 && b.top < a.bottom - 2) overlaps++;
    }
    const share = stage ? (stage.width * stage.height) / (window.innerWidth * window.innerHeight) : 0;
    return { share: share.toFixed(2), stageW: Math.round(stage?.width ?? 0), stageH: Math.round(stage?.height ?? 0), labels: boxes.length, overlaps, overflow: document.documentElement.scrollWidth - window.innerWidth };
  });
}

async function openHqBuilding(page: Page) {
  await hq(page).getByRole('button', { name: /^(List|قائمة)$/ }).click();
  await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name: 'ANUM HQ', exact: true }) }).locator('.hqListRow > button').click();
  await expect(hq(page).locator('.hqSceneWrap')).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(3000);
}

for (const [w, h] of SIZES) {
  for (const view of ['campus', 'all', 'one'] as const) {
    test(`${PREFIX} ${view} ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await mockHq(page, { campus: 'api', layout: 'starter-full', meetings: 'live' });
      await page.goto('/#office-preview');
      await expect(hq(page).locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 90_000 });
      if (view !== 'campus') {
        await openHqBuilding(page);
        if (view === 'one') await hq(page).getByRole('checkbox', { name: /All floors/ }).uncheck();
        await hq(page).getByRole('combobox', { name: 'Choose a floor' }).selectOption({ label: 'Rooftop: Terrace and Events' }).catch(() => undefined);
        await page.waitForTimeout(1500);
        // Zoomed in on the rooftop like the owner's picture.
        const zoom = hq(page).getByRole('button', { name: 'Zoom in' }).first();
        for (let i = 0; i < 3; i++) await zoom.click();
      }
      await page.waitForTimeout(3000);
      await page.screenshot({ path: `${OUT}/${PREFIX}-${view}-${w}x${h}.png` });
      console.log(`${PREFIX} ${view} ${w}x${h} ${JSON.stringify(await measure(page))}`);
    });
  }
}
