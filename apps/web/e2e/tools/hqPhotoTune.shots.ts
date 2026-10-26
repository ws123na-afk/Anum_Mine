import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from '../support/hqCampusApi';

// Look tuning in one session (not a CI spec): opens the campus at tier 4 once, then renders a list of grade variants through
// `window.__anumHqTune` (needs `anum.hq.bench` = 1) and saves one screenshot per variant. LOOK_VARIANTS is a JSON object of name -> tune.
const OUT = process.env.LOOK_DIR ?? 'test-results/photoreal-tune';
mkdirSync(OUT, { recursive: true });
const VARIANTS: Record<string, unknown> = JSON.parse(process.env.LOOK_VARIANTS ?? '{"base":{}}');

test('tune: grade variants in one session', async ({ page }) => {
  test.setTimeout(3_000_000);
  await page.setViewportSize({ width: Number(process.env.LOOK_W ?? 900), height: Number(process.env.LOOK_H ?? 560) });
  const light = process.env.LOOK_LIGHT ?? 'day';
  await page.addInitScript(([l]) => { localStorage.setItem('anum.hq.light', l === 'dusk' ? 'night' : l!); localStorage.setItem('anum.hq.preset', l === 'dusk' ? 'dusk' : 'night'); localStorage.setItem('anum.hq.quality', '4'); localStorage.setItem('anum.hq.bench', '1'); }, [light]);
  await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await expect.poll(() => page.locator('canvas.hqCanvas').getAttribute('data-fx'), { timeout: 900_000, intervals: [3000] }).toContain('agx');
  await page.addStyleTag({ content: '.hqLive :is(.hqOverlay,.hqPanel,.hqLabels,.hqNotice,.hqNote,.hqStats,.hqSideInline,.hqMenuWrap,.hqGhost,.hqTotalsInline,.hqHead){visibility:hidden !important} .hqLive [role=status],.hqLive [role=alert],.hqLive [aria-live]{visibility:hidden !important}' });
  for (const [name, tune] of Object.entries(VARIANTS)) {
    await page.evaluate((t) => (window as unknown as { __anumHqTune: (t: unknown) => void }).__anumHqTune(t), tune);
    await page.waitForTimeout(Number(process.env.LOOK_FRAME_WAIT ?? 8000));
    await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${name}.png` });
  }
});
