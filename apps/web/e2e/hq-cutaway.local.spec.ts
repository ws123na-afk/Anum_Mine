import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';

// Local render check of the section view (not run in CI): screenshots of one building for the owner review.
// CUT_DIR, CUT_BUILDING, CUT_W, CUT_H, CUT_LIGHT, CUT_LOCALE, CUT_FACADE (cutaway|closed), CUT_TURNS (number of "Rotate right" steps), CUT_FLOOR (index).
const OUT = process.env.CUT_DIR ?? '/tmp/cutaway-shots';
mkdirSync(OUT, { recursive: true });
const BUILDING = process.env.CUT_BUILDING ?? 'ANUM 360 Commerce';
const W = Number(process.env.CUT_W ?? 1440), H = Number(process.env.CUT_H ?? 900);
const LIGHT = (process.env.CUT_LIGHT ?? 'night') as 'day' | 'night';
const LOCALE = (process.env.CUT_LOCALE ?? 'en') as 'en' | 'ar';
const hq = (page: Page) => page.locator('.hqLive');

test(`cutaway ${BUILDING}`, async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: W, height: H });
  await page.addInitScript((l) => { try { localStorage.setItem('anum.hq.light', l); } catch { /* */ } }, LIGHT);
  if (LOCALE === 'ar') await useSavedLocale(page, 'ar');
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await hq(page).getByRole('button', { name: new RegExp(`^${BUILDING}[.،]`) }).first().click({ force: true });
  await hq(page).getByRole('button', { name: LOCALE === 'ar' ? new RegExp(BUILDING) : `Open ${BUILDING}` }).first().click();
  await expect(hq(page).locator('.hqSceneWrap')).toBeVisible();
  const hide = hq(page).getByRole('button', { name: /Hide side panels|إخفاء/ });
  if (await hide.count()) await hide.first().click({ timeout: 4000 }).catch(() => undefined);
  await page.waitForTimeout(2500);
  if (process.env.CUT_FULL === '1') { await hq(page).getByRole('button', { name: /Full screen|ملء الشاشة/ }).first().click(); await page.waitForTimeout(1500); }
  if (process.env.CUT_STAGE) { await page.addStyleTag({ content: `.hqBuilding .hqStage{block-size:${process.env.CUT_STAGE}px !important}` }); await page.waitForTimeout(1500); }
  if (process.env.CUT_FACADE === 'closed') await hq(page).getByTestId('hq-facade-closed').click();
  for (let i = 0; i < Number(process.env.CUT_TURNS ?? 0); i++) { await hq(page).getByRole('button', { name: /Rotate right|تدوير لليمين/ }).click(); await page.waitForTimeout(900); }
  const f = Number(process.env.CUT_FLOOR ?? 0);
  if (f > 0) for (let i = 0; i < f; i++) await page.keyboard.press('PageUp');
  await page.waitForTimeout(4500);
  const tag = `${BUILDING.replace(/\W+/g, '-')}-${LIGHT}-${LOCALE}-${W}x${H}-${process.env.CUT_FACADE ?? 'cutaway'}-t${process.env.CUT_TURNS ?? 0}-f${f}`;
  await page.screenshot({ path: `${OUT}/page-${tag}.png` });
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${tag}.png` });
});
