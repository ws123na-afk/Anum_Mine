import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';
import { mockFlow } from './support/hqFlowApi';

// Local render check for the label layer (not run in CI: screenshots for the owner review, no pixel assertions).
const OUT = process.env.LBL_DIR ?? '/tmp/lbl-shots';
mkdirSync(OUT, { recursive: true });
const hq = (page: Page) => page.locator('.hqLive');
const BUILDING = process.env.LBL_BUILDING ?? 'ANUM 360 Commerce';
const FLOORS = (process.env.LBL_FLOORS ?? '0').split(',').map(Number);
const LIGHT = (process.env.LBL_LIGHT ?? 'night') as 'day' | 'night';
const W = Number(process.env.LBL_W ?? 1440), H = Number(process.env.LBL_H ?? 900);
const LANG = process.env.LBL_LANG ?? 'en';
const SEATED = [['x1', 'campus-02-commerce-ecommerce', 'agent_fleet'], ['x2', 'campus-02-commerce-retail', 'agent_care'], ['x3', 'campus-02-commerce-orders', 'agent_leads'], ['x4', 'campus-02-commerce-warehouse', 'agent_invoice'], ['x5', 'campus-02-commerce-sales-office', 'agent_outreach'],
  ['x6', 'campus-01-hq-command-center', 'agent_fleet'], ['x7', 'campus-01-hq-ai-organization', 'agent_care'], ['x8', 'campus-01-hq-executive-office', 'agent_leads']] as const;

test(`labels ${BUILDING}`, async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: W, height: H });
  await page.addInitScript(([l, lang]) => { try { localStorage.setItem('anum.hq.light', l!); if (lang === 'ar') { localStorage.setItem('anum.locale', 'ar'); localStorage.setItem('anum.language', 'ar'); } } catch { /* */ } }, [LIGHT, LANG]);
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live', presence: 'api', stream: 'live', ...(process.env.LBL_AGENTS === '0' ? {} : { extraDesks: SEATED.map(([id, roomId, agentId]) => ({ id: `desk_${id}`, roomId, label: 'Desk X', agentId })) }) });
  if (process.env.LBL_FLOW !== '0') await mockFlow(page);
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await hq(page).getByRole('button', { name: new RegExp(`^${BUILDING}\\.`) }).first().click({ force: true });
  await hq(page).getByRole('button', { name: `Open ${BUILDING}` }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: BUILDING })).toBeVisible();
  await page.waitForTimeout(3000);
  if (process.env.LBL_FULL === '1') { await hq(page).getByRole('button', { name: 'Full screen' }).click(); await page.waitForTimeout(2500); }
  if (process.env.LBL_HIDE === '1') { const h = hq(page).getByRole('button', { name: /Hide side panels/ }); if (await h.count()) await h.click({ timeout: 4000 }).catch(() => undefined); const c = hq(page).getByRole('button', { name: /Collapse the floor list/ }); if (await c.count()) await c.click().catch(() => undefined); await page.waitForTimeout(1500); }
  if (process.env.LBL_ALL === '1') { await hq(page).getByRole('checkbox', { name: /All floors/ }).check(); await page.waitForTimeout(3000); }
  const rail = hq(page).getByRole('group', { name: 'Floors of this building' });
  for (const f of FLOORS) {
    if (await rail.getByRole('button').count()) await rail.getByRole('button').nth(f).click();
    await page.waitForTimeout(4500);
    await page.screenshot({ path: `${OUT}/${process.env.LBL_FULL === '1' ? 'fs-' : ''}${BUILDING.replace(/\W+/g, '-')}-f${f}${process.env.LBL_ALL === '1' ? '-stack' : ''}-${LIGHT}-${LANG}-${W}x${H}.png` });
  }
});
