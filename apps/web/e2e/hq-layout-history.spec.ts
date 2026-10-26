import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openBuilder } from './support/hqBuilderApi';

// Layout history, restore and templates in the office builder (docs/ANUM_MINE_LAYOUT_HISTORY.md): the main
// path, every state (loading, empty, error, denied, conflict), keyboard, Arabic and the five viewports.
// The API is mocked at http://localhost:8000/** with the server's rules (support/hqLayoutHistoryApi.ts).
test.use({ viewport: { width: 1440, height: 900 } });

const SIZES = [
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1366x768', width: 1366, height: 768 },
  { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 },
  { name: '390x844', width: 390, height: 844 },
] as const;

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}

async function openTab(page: Page, name: RegExp): Promise<void> {
  await expect(page.locator('.hqb .hqbToolbar')).toBeVisible();
  const tab = page.getByRole('tab', { name });
  if (!(await tab.isVisible())) await page.locator('.hqbSheetToggle').click();
  await tab.click();
}
const historyTab = (page: Page) => openTab(page, /^History$|^السجل$/);
const templatesTab = (page: Page) => openTab(page, /^Templates$|^القوالب$/);

async function expectNoOverflow(page: Page): Promise<void> {
  const problems = await page.evaluate(() => {
    const out: string[] = [];
    const root = document.querySelector('.hqb') as HTMLElement;
    const box = root.getBoundingClientRect();
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    if (root.scrollWidth > root.clientWidth + 1) out.push(`builder scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`);
    const panel = document.querySelector('.hqbTabPanel') as HTMLElement | null;
    if (panel && panel.scrollWidth > panel.clientWidth + 1) out.push(`panel scrolls sideways (${panel.scrollWidth} > ${panel.clientWidth})`);
    for (const el of root.querySelectorAll<HTMLElement>('.hqhTab button, .hqhDialog button, .hqhDialog input, .hqhPlan, .hqbTabs')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const host = el.closest('.hqbTabPanel, .hqhDialog') as HTMLElement | null;
      const hb = (host ?? root).getBoundingClientRect();
      if (r.left < hb.left - 1 || r.right > hb.right + 1) out.push(`clipped: ${el.textContent?.trim().slice(0, 30) || el.getAttribute('aria-label') || el.tagName} (${Math.round(r.left)}..${Math.round(r.right)})`);
      if (r.left < box.left - 1 || r.right > box.right + 1) out.push(`outside the builder: ${el.tagName}`);
    }
    const dialog = document.querySelector('.hqhDialog') as HTMLElement | null;
    if (dialog) {
      const d = dialog.getBoundingClientRect();
      if (d.bottom > window.innerHeight + 1 || d.top < -1) out.push(`dialog leaves the screen (${Math.round(d.top)}..${Math.round(d.bottom)})`);
      const actions = dialog.querySelector('.hqhDialogActions') as HTMLElement | null;
      const a = actions?.getBoundingClientRect();
      if (a && (a.bottom > d.bottom + 1 || a.height === 0)) out.push('dialog actions are clipped');
    }
    return out;
  });
  expect(problems).toEqual([]);
}

test('owner: browse versions, preview, restore with the unassign list, and the plan follows', async ({ page }) => {
  const mock = await openBuilder(page);
  await historyTab(page);
  const items = page.getByRole('list', { name: 'Saved versions, newest first' }).getByRole('listitem');
  await expect(items).toHaveCount(3);
  await expect(items.first()).toContainText('Version 3');
  await expect(items.first()).toContainText('Current');
  await expect(items.last()).toContainText('Starting point before tracked changes');
  // The restore button only appears for an older version.
  await items.first().getByRole('button').click();
  await expect(page.getByRole('button', { name: /^Restore version/ })).toHaveCount(0);

  await items.last().getByRole('button').click();
  await expect(page.getByTestId('history-plan')).toBeVisible();
  await expect(page.getByTestId('history-summary')).toContainText('1 room added');
  await expect(page.getByTestId('history-summary')).toContainText('1 room removed');
  await expect(page.getByRole('note').filter({ hasText: 'removes 1 desk' })).toBeVisible();

  await page.getByRole('button', { name: 'Restore version 1' }).click();
  const dialog = page.getByRole('dialog', { name: /Restore version 1 of Ground floor\?/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Desk 1')).toBeVisible();
  const confirm = dialog.getByRole('button', { name: 'Restore version 1' });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByTestId('restore-dialog')).toHaveCount(0);
  await expect(page.getByText('Version 1 was restored as new version 4.')).toBeVisible();
  await expect(items).toHaveCount(4);
  await expect(items.first()).toContainText('Restored from version 1');
  const restore = mock.history.requests.find((r) => r.method === 'POST' && r.path.endsWith('/restore'));
  expect(restore?.body).toMatchObject({ input_mode: 'click', expected_head: 3, confirm_unassign: ['d-1'] });
  await expect(page.locator('.hqbSvg [data-room-id="r-old"]')).toBeVisible();
  await expect(page.locator('.hqbSvg [data-room-id="r-recep"]')).toHaveCount(0);
});

test('conflict: the floor changed while the dialog was open, nothing is restored and the list reloads', async ({ page }) => {
  const mock = await openBuilder(page);
  await historyTab(page);
  const items = page.getByRole('list', { name: 'Saved versions, newest first' }).getByRole('listitem');
  await items.nth(1).getByRole('button').click();
  await page.getByRole('button', { name: 'Restore version 2' }).click();
  const dialog = page.getByTestId('restore-dialog');
  await dialog.getByRole('checkbox').check();
  mock.history.otherEditorSaves();
  await dialog.getByRole('button', { name: 'Restore version 2' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Someone changed this floor');
  expect(mock.history.revisions).toHaveLength(4);
  await dialog.getByRole('button', { name: 'Reload the history' }).click();
  await expect(page.getByTestId('restore-dialog')).toHaveCount(0);
  await expect(items).toHaveCount(4);
  await expect(items.first()).toContainText('Version 4');
});

test('unsaved plan changes block a restore', async ({ page }) => {
  await openBuilder(page);
  await page.locator('.hqb [data-room-id="r-sales"]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('draft-status')).toContainText('Unsaved');
  await historyTab(page);
  await page.getByRole('list', { name: 'Saved versions, newest first' }).getByRole('listitem').last().getByRole('button').click();
  await expect(page.getByTestId('history-draft-note')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restore version 1' })).toBeDisabled();
});

test('dialog keyboard: focus moves in, tab stays inside, escape closes and focus goes back', async ({ page }) => {
  await openBuilder(page);
  await historyTab(page);
  await page.getByRole('list', { name: 'Saved versions, newest first' }).getByRole('listitem').nth(1).getByRole('button').click();
  const opener = page.getByRole('button', { name: 'Restore version 2' });
  await opener.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByTestId('restore-dialog');
  await expect(dialog).toBeFocused();
  for (let i = 0; i < 6; i += 1) {
    await page.keyboard.press('Tab');
    expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test('states: no versions yet shows what happens next', async ({ page }) => {
  await openBuilder(page, { noHistory: true });
  await historyTab(page);
  await expect(page.getByTestId('history-empty')).toContainText('No saved versions yet');
});

test('states: a role without access sees why, with no retry that cannot work', async ({ page }) => {
  await openBuilder(page, { denyHistory: true });
  await historyTab(page);
  await expect(page.getByRole('alert')).toContainText('Your role cannot open the history here');
  await expect(page.getByRole('alert').getByRole('button')).toHaveCount(0);
});

test('states: the office service is down, retry is offered', async ({ page }) => {
  await openBuilder(page, { historyDown: true });
  await historyTab(page);
  await expect(page.getByRole('alert')).toContainText('PostgreSQL');
  await expect(page.getByRole('alert').getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('viewer: can read the history, cannot restore or manage templates', async ({ page }) => {
  await openBuilder(page, { role: 'viewer', seedTemplate: true });
  await historyTab(page);
  const items = page.getByRole('list', { name: 'Saved versions, newest first' }).getByRole('listitem');
  await items.last().getByRole('button').click();
  await expect(page.getByTestId('history-plan')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Restore version/ })).toHaveCount(0);
  await expect(page.getByText('Only a workspace owner can restore a version.').first()).toBeVisible();
  await templatesTab(page);
  await expect(page.getByTestId('template-row')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Save this floor as a template' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New floor from this' })).toHaveCount(0);
});

test('templates: save a floor, refuse a duplicate name, make a new floor, rename and delete', async ({ page }) => {
  const mock = await openBuilder(page);
  await templatesTab(page);
  await expect(page.getByTestId('templates-empty')).toBeVisible();
  const name = page.getByLabel('Template name for this floor');
  await name.fill('Standard floor');
  await page.getByRole('button', { name: 'Save this floor as a template' }).click();
  await expect(page.getByTestId('template-notice')).toContainText('Template Standard floor saved.');
  await expect(page.getByTestId('template-notice')).toContainText('1 room that is not placed');
  await expect(page.getByTestId('template-row')).toHaveCount(1);
  await page.getByRole('button', { name: 'Save this floor as a template' }).click();
  await expect(page.getByRole('alert')).toContainText('already has that name');

  await page.getByRole('button', { name: 'New floor from this' }).click();
  await page.getByLabel('Name of the new floor').fill('First floor');
  await page.getByLabel('Level').fill('1');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByTestId('template-notice')).toContainText('Floor First floor created');
  expect(mock.floors.size).toBe(2);
  await expect(page.locator('.hqbHeader select').nth(1).locator('option:checked')).toContainText('First floor');

  await templatesTab(page);
  await page.getByRole('button', { name: 'Rename' }).click();
  await page.getByRole('textbox', { name: 'Rename' }).fill('Office floor');
  await page.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByTestId('template-row')).toContainText('Office floor');
  await page.getByRole('button', { name: 'Delete template' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete template' }).click();
  await expect(page.getByTestId('templates-empty')).toBeVisible();
});

test('Arabic: the history reads right to left, restores and has no accessibility violations', async ({ page }) => {
  await openBuilder(page, { locale: 'ar' });
  await historyTab(page);
  const items = page.getByRole('list', { name: 'النسخ المحفوظة، الأحدث أولاً' }).getByRole('listitem');
  await expect(items).toHaveCount(3);
  await items.last().getByRole('button').click();
  await expect(page.getByTestId('history-plan')).toBeVisible();
  await page.getByRole('button', { name: 'استعادة النسخة 1' }).click();
  const dialog = page.getByTestId('restore-dialog');
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((node) => getComputedStyle(node).direction)).toBe('rtl');
  const results = await new AxeBuilder({ page }).include('.hqb').disableRules(['color-contrast']).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 80)}`)).toEqual([]);
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'استعادة النسخة 1' }).click();
  await expect(items).toHaveCount(4);
});

for (const size of SIZES) {
  for (const locale of ['en', 'ar'] as const) {
    test(`@gate history at ${size.name} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await openBuilder(page, { locale, seedTemplate: true });
      await historyTab(page);
      const items = page.locator('.hqhList').first().getByRole('listitem');
      await expect(items).toHaveCount(3);
      await expectNoOverflow(page);
      await shot(page, `history-${size.name}-${locale}-list`);
      await items.last().getByRole('button').click();
      await expect(page.getByTestId('history-plan')).toBeVisible();
      await expectNoOverflow(page);
      await shot(page, `history-${size.name}-${locale}-detail`);
      await page.locator('.hqhDetail').getByRole('button').last().click();
      const dialog = page.getByTestId('restore-dialog');
      await expect(dialog).toBeVisible();
      await expectNoOverflow(page);
      await shot(page, `history-${size.name}-${locale}-dialog`);
      await dialog.getByRole('checkbox').check();
      await expectNoOverflow(page);
      await page.keyboard.press('Escape');
      await templatesTab(page);
      await expect(page.getByTestId('template-row')).toHaveCount(1);
      await expectNoOverflow(page);
      await shot(page, `history-${size.name}-${locale}-templates`);
    });
  }
}
