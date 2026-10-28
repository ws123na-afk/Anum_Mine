import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { hqRegistry, openHq, TENANT_A } from '../support/hqApi';
import { mockFlow } from '../support/hqFlowApi';
import { agentAvatars } from '../support/orgChartApi';
import { characterRegistry } from '../support/hqCharactersApi';
import { greenRegistry, mockGreenWork, newState } from '../support/hqGreenConcreteApi';
import { mockProvisioning } from '../support/hqProvisioningApi';

// Local screenshot runner for the people of the headquarters (not part of CI). Usage: see playwright.hq.shots.config.ts.
//   CH_BUILDING="Tyre Pulse" | "Executive HQ", CH_ZOOM=<zoom in clicks>, CH_NIGHT=1, CH_W / CH_H, CH_DIR, CH_TAG, CH_STYLES=masculine,feminine,neutral (cycled over the agents)
const OUT = process.env.CH_DIR ?? '/tmp/ch-shots';
mkdirSync(OUT, { recursive: true });
const W = Number(process.env.CH_W ?? 1440), H = Number(process.env.CH_H ?? 900);
const BUILDING = process.env.CH_BUILDING ?? 'Tyre Pulse';
const hq = (page: Page) => page.locator('.hqLive');

test(`people in ${BUILDING}`, async ({ page }) => {
  test.setTimeout(900_000);
  await page.setViewportSize({ width: W, height: H });
  const simple = BUILDING === 'Al Noor Fleet HQ';
  const registry = simple ? hqRegistry(TENANT_A, { rooms: 1, desks: 6, agentIds: ['agent_leads', 'agent_outreach', 'agent_fleet', 'agent_maint', 'agent_care', 'agent_invoice'] }) : characterRegistry();
  const styles = (process.env.CH_STYLES ?? '').split(',').filter(Boolean);
  for (const k of Object.keys(agentAvatars)) delete agentAvatars[k];
  if (styles.length) registry.desks.forEach((d, i) => { agentAvatars[d.agent_id as string] = { avatar_style: styles[i % styles.length] }; });
  if (process.env.CH_DEBUG === '1') await page.addInitScript(() => {
    const w = window as unknown as { __draws: Record<string, number> };
    w.__draws = {};
    for (const name of ['drawArraysInstanced', 'drawElementsInstanced'] as const) {
      const proto = WebGL2RenderingContext.prototype as unknown as Record<string, (...a: number[]) => void>;
      const orig = proto[name];
      proto[name] = function (this: unknown, ...a: number[]) { const key = `${name}:${a[1]}x${name === 'drawArraysInstanced' ? a[3] : a[4]}`; w.__draws[key] = (w.__draws[key] ?? 0) + 1; return orig.apply(this, a); };
    }
  });
  const green = BUILDING === 'Green Concrete';
  const at = new Date(Date.UTC(2026, 9, 26, process.env.CH_NIGHT === '1' ? 19 : 8, 30, 0));
  if (green) await page.clock.setFixedTime(at);
  const gcState = newState(() => at.getTime());
  await openHq(page, { registry: green ? greenRegistry() : registry, locale: process.env.CH_LOCALE === 'ar' ? 'ar' : 'en', beforeGoto: async (p) => {
    if (process.env.CH_NIGHT === '1') await p.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'night'); } catch { /* */ } });
    await p.addInitScript(() => { try { localStorage.setItem('anum.hq.light', localStorage.getItem('anum.hq.light') === 'night' ? 'night' : 'day'); } catch { /* */ } });
    await mockFlow(p);
    if (green) { await mockProvisioning(p); await mockGreenWork(p, gcState); }
  } });
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  if (green) {
    await hq(page).locator('.hqBuildingCard').first().dispatchEvent('click');
    await hq(page).getByRole('button', { name: /^(Open |فتح |افتح )/ }).first().dispatchEvent('click');
  } else {
    await hq(page).getByRole('button', { name: new RegExp(`^${BUILDING}\\.`) }).first().click({ force: true });
    await hq(page).getByRole('button', { name: `Open ${BUILDING}` }).click();
  }
  await expect(hq(page).getByRole('heading', { level: 2, name: BUILDING })).toBeVisible();
  const canvas = page.locator('canvas.hqCanvas');
  await expect.poll(async () => Number(await canvas.getAttribute('data-model-figures')), { timeout: 150_000 }).toBeGreaterThan(0).catch(() => console.log('figures not loaded in time (the stand-ins stay)'));
  await page.waitForTimeout(3000);
  const hide = hq(page).getByRole('button', { name: /Hide side panels/ });
  if (await hide.count()) await hide.click({ timeout: 4000 }).catch(() => undefined);
  if (process.env.CH_LIST === '1') console.log('BUTTONS', JSON.stringify(await hq(page).getByRole('button').evaluateAll((els) => els.map((e) => (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 60)))));
  const tag = process.env.CH_TAG ?? 'after';
  const name = (BUILDING + (process.env.CH_ROOM ? '-' + process.env.CH_ROOM : '')).replace(/\W+/g, '-').toLowerCase();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/${tag}-${name}-overview-${W}x${H}.png` });
  if (process.env.CH_ROOM) { await hq(page).getByRole('button', { name: new RegExp(`^Quick view: ${process.env.CH_ROOM}\\.`) }).first().dispatchEvent('click'); await page.waitForTimeout(2500); await hq(page).getByRole('button', { name: /^Close$/ }).first().click({ timeout: 3000 }).catch(() => undefined); await page.waitForTimeout(1500); }
  // The label cards sit on top of the people they name: hide them so the figures can be judged (CH_LABELS=1 keeps them).
  if (process.env.CH_LABELS !== '1') await page.addStyleTag({ content: '.hqLabels{visibility:hidden !important}' });
  if (process.env.CH_INSIDE === '1') {
    await hq(page).getByRole('button', { name: /Go inside the selected room/ }).first().dispatchEvent('click');
    await page.waitForTimeout(3500);
  }
  const steps = (process.env.CH_ZOOM ?? '5').split(',').map(Number);
  let zoomed = 0;
  for (const target of steps) {
    for (; zoomed < target; zoomed++) {
      if (process.env.CH_WHEEL === '1') { const box = (await canvas.boundingBox())!; await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.55); await page.mouse.wheel(0, -500); } else await hq(page).getByRole('button', { name: /Zoom in/ }).first().click({ timeout: 4000 }).catch(() => undefined);
      await page.waitForTimeout(300);
    }
    await page.waitForTimeout(3000);
    await canvas.screenshot({ path: `${OUT}/${tag}-${name}-z${target}-${W}x${H}.png` });
  }
  // Pans (CH_PANS="dx,dy;dx,dy"): the Pan tool, a drag, a shot, the drag back.
  const pans = (process.env.CH_PANS ?? '').split(';').filter(Boolean).map((v) => v.split(',').map(Number));
  if (pans.length) {
    await hq(page).getByRole('button', { name: 'Pan', exact: true }).first().click({ timeout: 4000 }).catch(() => undefined);
    const box = (await canvas.boundingBox())!;
    const cx = box.x + box.width * 0.62, cy = box.y + box.height * 0.5;
    let k = 0;
    for (const [dx, dy] of pans) {
      await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + dx, cy + dy, { steps: 12 }); await page.mouse.up();
      await page.waitForTimeout(2500);
      await canvas.screenshot({ path: `${OUT}/${tag}-${name}-pan${k++}-${W}x${H}.png` });
      await page.mouse.move(cx + dx, cy + dy); await page.mouse.down(); await page.mouse.move(cx, cy, { steps: 12 }); await page.mouse.up();
    }
  }
  await page.screenshot({ path: `${OUT}/${tag}-${name}-page-${W}x${H}.png` });
  if (process.env.CH_CLOSED === '1') {
    await hq(page).getByRole('button', { name: 'Closed', exact: true }).click({ timeout: 5000 }).catch(() => undefined);
    await hq(page).getByRole('checkbox', { name: /All floors/ }).uncheck({ timeout: 5000 }).catch(() => undefined);
    await page.waitForTimeout(4000);
    await canvas.screenshot({ path: `${OUT}/${tag}-${name}-closed-${W}x${H}.png` });
  }
  if (process.env.CH_DEBUG === '1') console.log('DRAWS', JSON.stringify(await page.evaluate(() => (window as unknown as { __draws: Record<string, number> }).__draws)));
  if (process.env.CH_DEBUG === '1') console.log('LAYER', JSON.stringify(await page.evaluate(() => {
    const r = (globalThis as unknown as { __hqDebug: { layer: { root: { children: Array<Record<string, any>> } }; camera: { position: { x: number; y: number; z: number } }; scene: { children: unknown[] } } }).__hqDebug;
    return { camera: [r.camera.position.x, r.camera.position.y, r.camera.position.z].map((v) => Math.round(v * 10) / 10), sceneChildren: r.scene.children.length,
      layer: r.layer.root.children.map((c) => ({ t: c.type, n: c.count, vis: c.visible, p: c.isInstancedMesh ? Array.from(c.instanceMatrix.array.slice(0, 16) as number[]).map((v) => Math.round(v * 100) / 100) : null, v: c.geometry?.attributes?.position?.count, attrs: Object.keys(c.geometry?.attributes ?? {}).join(','), mat: c.material ? [c.material.type, c.material.visible, c.material.opacity, c.material.transparent, c.material.side, c.material.depthTest, c.material.depthWrite].join('/') : null, bb: (() => { try { c.geometry.computeBoundingBox(); const b = c.geometry.boundingBox; return [b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z].map((v: number) => Math.round(v * 100) / 100); } catch { return null; } })(), layers: c.layers?.mask, ro: c.renderOrder })).slice(0, 3) };
  })));
  console.log(`figures=${await canvas.getAttribute('data-model-figures')} looks=${await canvas.getAttribute('data-figure-looks')} fallbacks=${await canvas.getAttribute('data-model-fallbacks')} triangles=${await canvas.getAttribute('data-triangles')}`);
});
