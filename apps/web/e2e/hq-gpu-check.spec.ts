import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';
import { sceneReady } from './support/hqScene';
import { REPORT_SCHEMA, reportLooksAnonymous } from '../src/lib/hqGpuReport';
import { HQ_TIERS } from '../src/lib/hqQualityTiers';

// Graphics check, quality chip, context loss and shader fallback (docs/ANUM_MINE_HQ_GPU_CHECK.md).
// These run on a software rasteriser (SwiftShader), so they prove the LOGIC: that the panel reports a software renderer honestly,
// measures, pins, saves an anonymous report and recovers from a lost context. They say nothing about a real graphics card.
const hq = (page: Page) => page.locator('.hqLive');
const SHOT_DIR = process.env.HQ_GPU_SHOTS ?? '';

async function openOffice(page: Page, init?: () => void) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  if (init) await page.addInitScript(init);
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await sceneReady(page);
  await expect(hq(page).locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
}

async function openPanel(page: Page) {
  await hq(page).getByRole('button', { name: /^(Office tools|أدوات المكتب)/ }).click();
  await hq(page).getByRole('button', { name: /^(Graphics check|فحص الرسومات)$/ }).click();
  await expect(page.getByTestId('hq-gpu-check')).toBeVisible();
}

const violations = async (page: Page) => (await new AxeBuilder({ page }).include('[data-testid="hq-gpu-check"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze())
  .violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.help}`);

test.describe('graphics check', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('opens from Office tools, reads the device honestly (a software rasteriser here) and passes axe', async ({ page }) => {
    test.setTimeout(150_000);
    await openOffice(page);
    await openPanel(page);
    const panel = page.getByTestId('hq-gpu-check');
    await expect(panel.locator('[data-fact="webgl"]')).toHaveText('WebGL 2');
    const software = panel.locator('[data-fact="software"]');
    await expect(software).toHaveAttribute('data-software', 'true');
    await expect(software).toContainText('Yes');
    await expect(panel.locator('[data-fact="renderer"]')).toContainText(/swiftshader|llvmpipe|software/i);
    await expect(panel.getByTestId('hq-gpu-verdict')).toHaveAttribute('data-kind', 'software');
    await expect(panel.getByTestId('hq-gpu-quality')).toContainText('software');
    expect(await violations(page)).toEqual([]);
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
  });

  test('the chip shows the tier and why, and opens the panel', async ({ page }) => {
    test.setTimeout(150_000);
    await openOffice(page);
    const chip = page.getByTestId('hq-gpu-chip');
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute('data-tier', 'low');
    await expect(chip).toContainText('Graphics: Low');
    await expect(chip).toHaveAttribute('title', /software/i);
    await chip.click();
    await expect(page.getByTestId('hq-gpu-check')).toBeVisible();
  });

  test('a benchmark of every tier runs, says it is software, and a pinned tier survives a reload', async ({ page }) => {
    test.setTimeout(240_000);
    await openOffice(page);
    await openPanel(page);
    const panel = page.getByTestId('hq-gpu-check');
    await panel.getByTestId('hq-gpu-length').selectOption('2');
    await panel.getByTestId('hq-gpu-bench').click();
    await expect(panel.getByTestId('hq-gpu-progress')).toContainText(/Done\. Tiers measured: 4/, { timeout: 120_000 });
    const rows = panel.getByTestId('hq-gpu-results').locator('li');
    await expect(rows).toHaveCount(HQ_TIERS.length);
    for (const tier of HQ_TIERS) await expect(panel.getByTestId('hq-gpu-results').locator(`li[data-tier="${tier.key}"]`)).toHaveCount(1);
    const verdict = panel.getByTestId('hq-gpu-verdict');
    await expect(verdict).toHaveAttribute('data-kind', 'software');
    await expect(verdict).toContainText('draws in software');
    await expect(verdict).toContainText('say nothing about how a real graphics card');
    await expect(verdict).not.toContainText('60 frames a second');
    expect(await violations(page)).toEqual([]);
    // The run left the quality as it was.
    await expect(page.getByTestId('hq-gpu-chip')).toHaveAttribute('data-tier', 'low');

    // Pin the verdict's tier: stored under the documented key, and the chip says pinned.
    await verdict.getByTestId('hq-gpu-use').click();
    await expect(panel.getByTestId('hq-gpu-note')).toContainText('is pinned');
    expect(await page.evaluate(() => localStorage.getItem('anum.hq.quality'))).toBe('0');
    await expect(page.getByTestId('hq-gpu-chip')).toHaveAttribute('title', /pinned/);
    await verdict.getByTestId('hq-gpu-auto').click();
    expect(await page.evaluate(() => localStorage.getItem('anum.hq.quality'))).toBeNull();
    await expect(page.getByTestId('hq-gpu-chip')).toHaveAttribute('title', /software/i);
  });

  test('the saved report has a fixed shape and nothing personal', async ({ page }) => {
    test.setTimeout(150_000);
    await openOffice(page);
    await openPanel(page);
    const panel = page.getByTestId('hq-gpu-check');
    await panel.getByTestId('hq-gpu-length').selectOption('2');
    await panel.getByTestId('hq-gpu-measure').click();
    await expect(panel.getByTestId('hq-gpu-progress')).toContainText(/Done\. Tiers measured: 1/, { timeout: 60_000 });
    const [download] = await Promise.all([page.waitForEvent('download'), panel.getByTestId('hq-gpu-download').click()]);
    expect(download.suggestedFilename()).toBe('anum-hq-graphics-report.json');
    const path = await download.path();
    const text = (await import('node:fs')).readFileSync(path, 'utf8');
    const report = JSON.parse(text);
    expect(report.schema).toBe(REPORT_SCHEMA);
    expect(report.gpu.software).toBe(true);
    expect(report.verdict.kind).toBe('software');
    expect(report.tiers).toHaveLength(1);
    expect(report.tiers[0].frames.count).toBeGreaterThan(0);
    expect(Object.keys(report).sort()).toEqual(['generatedAt', 'gpu', 'notes', 'quality', 'schema', 'tiers', 'verdict']);
    expect(reportLooksAnonymous(text)).toBe(true);
    expect(text).not.toMatch(/localhost|office-preview|tenant|workspace/i);
  });
});

test.describe('context loss and shader failure', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  const lose = (page: Page) => page.evaluate(() => {
    const el = document.querySelector('canvas.hqCanvas') as HTMLCanvasElement;
    const gl = (el.getContext('webgl2') ?? el.getContext('webgl')) as WebGL2RenderingContext;
    const ext = gl.getExtension('WEBGL_lose_context') as { loseContext(): void; restoreContext(): void };
    (window as unknown as { __ext: typeof ext }).__ext = ext;
    ext.loseContext();
  });

  test('a lost context shows a banner that does not block the page, then the scene comes back and keeps drawing', async ({ page }) => {
    test.setTimeout(150_000);
    await openOffice(page);
    await lose(page);
    const banner = hq(page).getByText('Restoring the 3D view...');
    await expect(banner).toBeVisible();
    // Non blocking: the controls under it still take clicks.
    expect(await banner.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none');
    await expect(hq(page).getByRole('button', { name: /^Office tools/ })).toBeEnabled();
    await page.evaluate(() => (window as unknown as { __ext: { restoreContext(): void } }).__ext.restoreContext());
    await expect(banner).toHaveCount(0);
    await expect(page.getByTestId('hq-gpu-chip')).toBeVisible();
    await hq(page).getByRole('button', { name: /^Zoom in|^Zoom/ }).first().click().catch(() => undefined);
    await expect.poll(() => page.evaluate(() => (document.querySelector('canvas.hqCanvas') as HTMLCanvasElement).dataset.drawMs ?? ''), { timeout: 30_000 }).not.toBe('');
    await expect(hq(page).getByRole('button', { name: /^Executive HQ\./ })).toBeAttached();
  });

  test('a context that never returns falls back to the list and plan, and "Try 3D again" brings the scene back', async ({ page }) => {
    test.setTimeout(180_000);
    await openOffice(page);
    await lose(page);
    await expect(hq(page).getByText(/3D is not available in this browser/)).toBeVisible({ timeout: 30_000 });
    await expect(hq(page).getByText(/context-lost/)).toBeVisible();
    // The list is usable meanwhile.
    await expect(hq(page).getByRole('button', { name: 'Open Executive HQ' })).toBeVisible();
    await page.evaluate(() => (window as unknown as { __ext: { restoreContext(): void } }).__ext.restoreContext());
    await hq(page).getByTestId('hq-retry-3d').click();
    await expect(hq(page).getByText(/3D is not available in this browser/)).toHaveCount(0);
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('hq-gpu-chip')).toBeVisible({ timeout: 60_000 });
  });

  test('a shader that fails to link switches to the simple materials and keeps the scene on screen', async ({ page }) => {
    test.setTimeout(180_000);
    await openOffice(page, () => {
      // Quality is pinned to 1 (full materials, shadows) and the first program link fails once, as a driver bug would; later ones work.
      try { localStorage.setItem('anum.hq.quality', '1'); } catch { /* storage blocked */ }
      const proto = WebGL2RenderingContext.prototype;
      const original = proto.getProgramParameter;
      let failed = false;
      proto.getProgramParameter = function (this: WebGL2RenderingContext, program: WebGLProgram, pname: number) {
        if (pname === this.LINK_STATUS && !failed) { failed = true; return false; }
        return original.call(this, program, pname);
      };
    });
    const chip = page.getByTestId('hq-gpu-chip');
    await expect(chip).toHaveAttribute('data-reduced', 'true', { timeout: 60_000 });
    await expect(chip).toContainText('reduced');
    await chip.click();
    await expect(page.getByTestId('hq-gpu-quality')).toContainText('simple materials');
    await expect(hq(page).getByText(/3D is not available in this browser/)).toHaveCount(0);
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  });
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of SIZES) {
    test(`@gate the graphics check fits ${width}x${height} in ${locale}`, async ({ page }) => {
      test.setTimeout(180_000);
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await openOffice(page);
      await openPanel(page);
      const panel = page.getByTestId('hq-gpu-check');
      await panel.getByTestId('hq-gpu-length').selectOption('2');
      await panel.getByTestId('hq-gpu-measure').click();
      await expect(panel.getByTestId('hq-gpu-progress')).toContainText(/\d/, { timeout: 60_000 });
      await expect(panel.getByTestId('hq-gpu-results')).toBeVisible({ timeout: 60_000 });
      if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
      const metrics = await page.evaluate(() => {
        const p = document.querySelector('[data-testid="hq-gpu-check"]') as HTMLElement;
        const box = p.getBoundingClientRect();
        const wide = [...p.querySelectorAll<HTMLElement>('button, select, h2, h3, dd, dt, p')].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 || el.getBoundingClientRect().left < -1).map((el) => el.tagName + ':' + (el.textContent ?? '').slice(0, 30));
        return { page: document.documentElement.scrollWidth - window.innerWidth, panelOverflow: p.scrollWidth - p.clientWidth, right: box.right, left: box.left, width: window.innerWidth, wide };
      });
      expect(metrics.page).toBeLessThanOrEqual(0);
      expect(metrics.panelOverflow).toBeLessThanOrEqual(0);
      expect(metrics.wide).toEqual([]);
      expect(await violations(page)).toEqual([]);
      if (SHOT_DIR) {
        await page.screenshot({ path: `${SHOT_DIR}/gpu-${locale}-${width}x${height}-top.png` });
        await panel.locator('.hqGpuBody').evaluate((el) => { el.scrollTop = el.scrollHeight; });
        await page.screenshot({ path: `${SHOT_DIR}/gpu-${locale}-${width}x${height}-bottom.png` });
      }
    });
  }
}
