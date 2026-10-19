import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { DEEP_LINKS, GATE_PAGES, GATE_SIZES, clippedControls, horizontalOverflow, settle, type GatePage } from './support/gatePages';
import { VIEWS, viewFromHash } from '../src/lib/shell.ts';

// The UI quality gate (CLAUDE.md, Responsive UI quality gate; docs/web-localization.md#accessibility)
// on every page the app can open (support/gatePages.ts). Each page is opened once per size and
// language, and that one load is checked for layout (nothing scrolls sideways, no control is cut
// off on either axis) and for accessibility (no serious or critical axe violation; the message lists
// each rule, its help text and the offending selectors). The light theme gets the axe gate once per
// page. The sizes are set here, so the mobile project leaves these @gate tests out (playwright.config.ts).

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
}

/** Opens the page with its data showing. */
async function open(page: Page, item: GatePage) {
  await page.goto(`/#${item.hash}`);
  await expect(page.locator(item.ready).first(), item.hash).toBeVisible();
  await settle(page);
}

/** The Inbox also opens a message, so its draft cards get the axe check too. */
async function openDetail(page: Page, item: GatePage) {
  if (item.hash !== 'inbox') return;
  await page.locator('.inboxRow').first().click();
  await expect(page.locator('.inboxDraftCard').first()).toBeVisible();
}

test('the gate list covers every view and every deep-linked tab', () => {
  // DEEP_LINKS comes from the source (shell.ts views and the modules' TABS lists); the shell's own
  // routing must agree with it, and each one must have a gate entry.
  expect(VIEWS.filter((view) => !DEEP_LINKS.includes(view))).toEqual([]);
  for (const link of DEEP_LINKS) expect(VIEWS, link).toContain(viewFromHash(`#${link}`));
  const gated = new Set(GATE_PAGES.map((item) => item.hash.split('?')[0]));
  expect(DEEP_LINKS.filter((link) => !gated.has(link))).toEqual([]);
  for (const item of GATE_PAGES) expect(viewFromHash(`#${item.hash}`), item.hash).toBe(item.view);
});

for (const size of GATE_SIZES) {
  test.describe(`at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    for (const item of GATE_PAGES) {
      test(`#${item.hash}: layout and axe, in English and Arabic`, async ({ page }) => {
        test.setTimeout(120_000);
        for (const locale of ['en', 'ar'] as const) {
          const tab = await page.context().newPage();
          await item.mock(tab);
          await useSavedLocale(tab, locale);
          await open(tab, item);
          await expect(tab.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
          expect(await horizontalOverflow(tab), `${item.hash} ${locale}: horizontal overflow`).toBeLessThanOrEqual(0);
          expect(await clippedControls(tab), `${item.hash} ${locale}: clipped controls`).toEqual([]);
          await openDetail(tab, item);
          expect(await seriousViolations(tab), `${item.hash} ${locale}: axe`).toEqual([]);
          await tab.close();
        }
      });
    }
  });
}

// The light theme (Settings > Appearance, docs/design/anum360.md#theme), in English at the default desktop size.
test.describe('light theme @gate', () => {
  for (const item of GATE_PAGES) {
    test(`#${item.hash} has no serious or critical axe violations`, async ({ page }) => {
      await item.mock(page);
      await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
      await open(page, item);
      await openDetail(page, item);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
});
