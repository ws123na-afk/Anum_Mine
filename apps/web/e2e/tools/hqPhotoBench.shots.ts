import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { mockHq } from '../support/hqCampusApi';

// Cost of each photographic effect, one at a time (docs/ANUM_MINE_HQ_PHOTOREAL.md). Not a CI spec. The renderer exposes
// `window.__anumHqBench(n, off)` when `anum.hq.bench` = 1: n frames, each ended by a GPU finish, with the named effects off.
// On a software rasteriser the milliseconds are CPU rasterising time (only the ratios mean anything); on a real GPU they are real.
//   HQ_PORT=4311 LOOK_DIR=<dir> LOOK_Q=4 pnpm exec playwright test -c playwright.hq.shots.config.ts --project=desktop -g bench
const OUT = process.env.LOOK_DIR ?? 'test-results/photoreal';
const FX = ['ao', 'detail', 'contact', 'pbr', 'gtao', 'dof', 'shafts', 'grain', 'bloom', 'lights', 'cascade'];
mkdirSync(OUT, { recursive: true });

test('bench: cost of every effect', async ({ page }) => {
  test.setTimeout(3_000_000);
  await page.setViewportSize({ width: Number(process.env.LOOK_W ?? 1280), height: Number(process.env.LOOK_H ?? 720) });
  await page.addInitScript(([l, q]) => { localStorage.setItem('anum.hq.light', l!); localStorage.setItem('anum.hq.quality', q!); localStorage.setItem('anum.hq.bench', '1'); }, [process.env.LOOK_LIGHT ?? 'night', process.env.LOOK_Q ?? '4']);
  await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await expect.poll(() => page.locator('canvas.hqCanvas').getAttribute('data-fx'), { timeout: 900_000, intervals: [3000] }).toContain('agx');
  const n = Number(process.env.LOOK_N ?? 3);
  const run = (off: string[]) => page.evaluate(([count, list]) => (window as unknown as { __anumHqBench: (n: number, off: string[]) => { ms: number; triangles: number; calls: number } }).__anumHqBench(count as number, list as string[]), [n, off] as const);
  const rows: Array<{ off: string; ms: number; triangles: number; calls: number }> = [];
  rows.push({ off: 'none (everything on)', ...(await run([])) });
  for (const name of FX) rows.push({ off: name, ...(await run([name])) });
  rows.push({ off: 'all off', ...(await run(FX)) });
  writeFileSync(`${OUT}/bench.json`, JSON.stringify(rows, null, 2));
  console.log(rows.map((r) => `${r.off.padEnd(24)} ${r.ms.toFixed(0).padStart(7)} ms  ${String(r.triangles).padStart(8)} tris  ${String(r.calls).padStart(5)} calls`).join('\n'));
});
