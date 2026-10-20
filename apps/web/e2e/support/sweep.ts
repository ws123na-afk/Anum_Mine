import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';
import { clippedControls, horizontalOverflow } from './gatePages';

/**
 * The layout and accessibility checks of the UI gate (CLAUDE.md, Responsive UI quality gate) on whatever
 * is showing now (a drawer, a dialog, a step): nothing scrolls sideways, no control is cut off, and no
 * serious or critical axe violation. `label` names the state in the failure message.
 */
export async function sweep(page: Page, label: string): Promise<void> {
  await page.waitForLoadState('networkidle', { timeout: 3_000 }).catch(() => undefined);
  expect(await horizontalOverflow(page), `${label}: horizontal overflow`).toBeLessThanOrEqual(0);
  expect(await clippedControls(page), `${label}: clipped controls`).toEqual([]);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  const serious = results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
  expect(serious, `${label}: axe`).toEqual([]);
}
