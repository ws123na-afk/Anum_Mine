import { expect, test, type Page } from '@playwright/test';
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { hqRegistry, TENANT_A, mockOfficeSpaces } from './support/hqApi';
import { mockOrgChart, seedAgents } from './support/orgChartApi';

// These specs test data and speed, not layout: use a desktop viewport in both projects (the phone layout hides the room inspector, issue H-04).
test.use({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false });

// F14 performance budgets for the headquarters scene (docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md).
// Frame and memory numbers come from a software renderer (SwiftShader) with 1 worker, which is far
// slower than a laptop GPU: they are a regression guard, not a claim about real devices.
export const BUDGET = {
  chunkGzipBytes: 60_000,        // HeadquartersEntrypoint JS, gzip
  chunkCssGzipBytes: 16_000,     // HeadquartersEntrypoint CSS, gzip
  frameP95Ms: { 21: 180, 200: 300 } as Record<number, number>,
  jsHeapMb: { 21: 120, 200: 180 } as Record<number, number>,
  readyMs: { 21: 8_000, 200: 15_000 } as Record<number, number>,
};

test('budget: the lazy headquarters chunk stays small', () => {
  const dir = new URL('../dist/assets/', import.meta.url);
  const js = readdirSync(dir).find((f) => /^HeadquartersEntrypoint-.*\.js$/.test(f));
  const css = readdirSync(dir).find((f) => /^HeadquartersEntrypoint-.*\.css$/.test(f));
  expect(js, 'HeadquartersEntrypoint chunk exists (run pnpm build)').toBeTruthy();
  const jsGz = gzipSync(readFileSync(new URL(js!, dir))).length;
  const cssGz = css ? gzipSync(readFileSync(new URL(css, dir))).length : 0;
  console.log(`HQ_CHUNK js_gzip=${jsGz} css_gzip=${cssGz}`);
  expect(jsGz).toBeLessThanOrEqual(BUDGET.chunkGzipBytes);
  expect(cssGz).toBeLessThanOrEqual(BUDGET.chunkCssGzipBytes);
});

function syntheticAgents(count: number) {
  const base = seedAgents();
  return Array.from({ length: count }, (_, i) => ({ ...base[i % base.length], id: `agent_synth_${i}`, name: `Synthetic Agent ${i + 1}` }));
}

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
    test.setTimeout(90_000);
    const agents = syntheticAgents(count);
    await mockOrgChart(page);
    await page.route(/localhost:8000\/api\/v1\/agents(\?.*)?$/, async (route) => {
      const url = new URL(route.request().url());
      const limit = Number(url.searchParams.get('limit') ?? 24);
      const cursor = url.searchParams.get('cursor');
      const rows = cursor ? agents.slice(agents.findIndex((a) => a.id === cursor) + 1) : agents;
      const items = rows.slice(0, limit);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items, next_cursor: rows.length > limit ? items[items.length - 1].id : null, counts: { all: count, work: count, personal: 0, custom: 0 } }) });
    });
    const registry = hqRegistry(TENANT_A, { rooms: Math.ceil(count / 4), desks: count, agentIds: agents.map((a) => String(a.id)) });
    await mockOfficeSpaces(page, { registries: { [TENANT_A.tenantId]: registry } });
    const started = Date.now();
    await page.goto('/#office-preview');
    await expect(page.locator('canvas.hqCanvas')).toBeVisible();
    await expect(page.locator('.hqRoomList').getByRole('button').first()).toBeVisible();
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
    await expect(page.locator('.hqGpuFallback')).toHaveCount(0);
  });
}
