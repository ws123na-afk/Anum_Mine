import { expect, test, type Page } from '@playwright/test';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { hqRegistry, TENANT_A, openHq } from './support/hqApi';

// These specs test data and speed, not layout: use a desktop viewport in both projects (the phone layout hides the room inspector, issue H-04).
test.use({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false });

// F14 performance budgets for the headquarters scene (docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md).
// Frame and memory numbers come from a software renderer (SwiftShader) with 1 worker, which is far
// slower than a laptop GPU: they are a regression guard, not a claim about real devices.
export const BUDGET = {
  chunkGzipBytes: 140_000,       // HeadquartersEntrypoint JS (campus, buildings, asset builders), gzip
  rendererGzipBytes: 160_000,    // hqThreeRenderer JS (three.js plus the animation and skinning code the glTF loader shares, loaded on demand), gzip
  chunkCssGzipBytes: 17_000,     // HeadquartersEntrypoint CSS, gzip
  // The photographic look (docs/ANUM_MINE_HQ_PHOTOREAL.md) added a second lazy importer of three.js, so the bundler moved three.core into its own
  // chunk: the renderer chunk alone shrank, so the whole path a lower tier downloads is budgeted too (the main branch measured 159,130 for it).
  rendererPathGzipBytes: 170_000, // hqThreeRenderer + three.core + baked occlusion (hqSceneFx, hqBakedAo), gzip
  photoGzipBytes: 20_000,        // hqPhotoFx + hqCampusFx: only the photographic tier and the campus detail download them, gzip
  frameP95Ms: { 21: 2_500, 200: 3_500 } as Record<number, number>,
  jsHeapMb: { 21: 200, 200: 260 } as Record<number, number>,
  readyMs: { 21: 15_000, 200: 25_000 } as Record<number, number>,
};

test('budget: the lazy headquarters chunk stays small', () => {
  const dir = new URL('../dist/assets/', import.meta.url);
  const js = readdirSync(dir).find((f) => /^HeadquartersEntrypoint-.*\.js$/.test(f));
  const css = readdirSync(dir).find((f) => /^HeadquartersEntrypoint-.*\.css$/.test(f));
  expect(js, 'HeadquartersEntrypoint chunk exists (run pnpm build)').toBeTruthy();
  const jsGz = gzipSync(readFileSync(new URL(js!, dir))).length;
  const cssGz = css ? gzipSync(readFileSync(new URL(css, dir))).length : 0;
  console.log(`HQ_CHUNK js_gzip=${jsGz} css_gzip=${cssGz}`);
  const renderer = readdirSync(dir).find((f) => /^hqThreeRenderer-.*\.js$/.test(f));
  expect(renderer, 'hqThreeRenderer chunk exists').toBeTruthy();
  const rendererGz = gzipSync(readFileSync(new URL(renderer!, dir))).length;
  console.log(`HQ_CHUNK renderer_gzip=${rendererGz}`);
  expect(rendererGz).toBeLessThanOrEqual(BUDGET.rendererGzipBytes);
  const gz = (re: RegExp) => readdirSync(dir).filter((f) => re.test(f)).reduce((n, f) => n + gzipSync(readFileSync(new URL(f, dir))).length, 0);
  const rendererPath = rendererGz + gz(/^three\.core-.*\.js$/) + gz(/^hqSceneFx-.*\.js$/) + gz(/^hqBakedAo-.*\.js$/);
  const photo = gz(/^hqPhotoFx-.*\.js$/) + gz(/^hqCampusFx-.*\.js$/);
  console.log(`HQ_CHUNK renderer_path_gzip=${rendererPath} photo_gzip=${photo}`);
  expect(rendererPath).toBeLessThanOrEqual(BUDGET.rendererPathGzipBytes);
  expect(photo).toBeLessThanOrEqual(BUDGET.photoGzipBytes);
  expect(jsGz).toBeLessThanOrEqual(BUDGET.chunkGzipBytes);
  expect(cssGz).toBeLessThanOrEqual(BUDGET.chunkCssGzipBytes);
});

async function frameStats(page: Page) {
  const box = (await page.locator('canvas.hqCanvas').boundingBox())!;
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  await page.evaluate(() => {
    const w = window as unknown as { __frames: number[] };
    w.__frames = [];
    let last = performance.now();
    const tick = (now: number) => { w.__frames.push(now - last); last = now; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  for (let i = 0; i < 60; i += 1) {
    await page.mouse.move(cx + Math.sin(i / 6) * 120, cy + Math.cos(i / 6) * 40);
  }
  await page.mouse.up();
  const frames = await page.evaluate(() => (window as unknown as { __frames: number[] }).__frames.slice(2));
  const sorted = [...frames].sort((a, b) => a - b);
  const pct = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))] ?? 0;
  return { frames: frames.length, p50: pct(0.5), p95: pct(0.95), max: sorted[sorted.length - 1] ?? 0 };
}

for (const count of [21, 200]) {
  test(`budget: scene with ${count} agents (frame time, memory, time to ready)`, async ({ page }) => {
    test.setTimeout(150_000);
    const registry = hqRegistry(TENANT_A, { rooms: Math.ceil(count / 4), desks: count, agentIds: Array.from({ length: count }, (_, i) => `agent_synth_${i}`) });
    const started = Date.now();
    await openHq(page, { registry });
    await expect(page.locator('canvas.hqCanvas')).toBeVisible();
    await expect(page.locator('.hqLive').getByRole('button').first()).toBeAttached();
    const readyMs = Date.now() - started;
    await page.waitForTimeout(500);
    const stats = await frameStats(page);
    const client = await page.context().newCDPSession(page);
    await client.send('Performance.enable');
    const metrics = (await client.send('Performance.getMetrics')).metrics;
    const heapMb = (metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? 0) / 1048576;
    console.log(`HQ_PERF agents=${count} ready_ms=${readyMs} frames=${stats.frames} p50=${stats.p50.toFixed(1)} p95=${stats.p95.toFixed(1)} max=${stats.max.toFixed(1)} heap_mb=${heapMb.toFixed(1)}`);
    expect(stats.frames).toBeGreaterThan(10);
    expect(stats.p95).toBeLessThanOrEqual(BUDGET.frameP95Ms[count]);
    expect(heapMb).toBeLessThanOrEqual(BUDGET.jsHeapMb[count]);
    expect(readyMs).toBeLessThanOrEqual(BUDGET.readyMs[count]);
    await expect(page.locator('.hqLive').getByText(/3D is not available in this browser/)).toHaveCount(0);
  });
}
