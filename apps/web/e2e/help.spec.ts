import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// In-app help (docs/help.md): the "?" in the top bar opens the panel with the article for the
// current page first, search narrows the list, an article opens with a link to the full docs, and
// Arabic readers get the Arabic articles. The sales demo card (docs/demo-workspace.md) creates the
// Demo workspace for an owner and explains the owner rule to others.
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('open help, search and read an article', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await page.getByRole('button', { name: 'Help', exact: true }).click();
  const panel = page.getByRole('dialog', { name: 'Help' });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole('region', { name: 'For this page' }).getByRole('button', { name: /Tasks/ })).toBeVisible();

  await panel.getByRole('searchbox').fill('approve reject');
  const results = panel.getByRole('region', { name: 'Matching articles' });
  await expect(results.getByRole('button').first()).toContainText('Approvals');
  await results.getByRole('button', { name: /Approvals/ }).first().click();

  const article = panel.getByRole('article', { name: 'Approvals' });
  await expect(article.getByRole('heading', { name: 'Approve or reject' })).toBeVisible();
  await expect(article.getByRole('link', { name: 'Full documentation' })).toHaveAttribute('href', /docs\/approvals-and-risk\.md$/);
  expect(await new AxeBuilder({ page }).include('.drawerPanel').analyze().then((r) => r.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious'))).toEqual([]);
  expect(await noOverflow(page)).toBe(true);

  await article.getByRole('button', { name: 'All articles' }).click();
  await panel.getByRole('searchbox').fill('zzzz nothing');
  await expect(panel.getByText('No article matches "zzzz nothing"')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
});

test('Arabic readers get the Arabic articles', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockWorkspaceApi(page);
  await page.goto('/#approvals');
  await page.getByRole('button', { name: 'المساعدة', exact: true }).click();
  const panel = page.getByRole('dialog', { name: 'المساعدة' });
  await panel.getByRole('region', { name: 'لهذه الصفحة' }).getByRole('button', { name: /الموافقات/ }).click();
  await expect(panel.getByRole('article', { name: 'الموافقات' })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
});

test('an owner creates the demo workspace from Settings', async ({ page }) => {
  await mockWorkspaceApi(page);
  let exists = false;
  const status = () => ({ workspace_id: 'ws_demo_0123456789abcdef', name: 'Demo', exists, records: exists ? 31 : 0, counts: exists ? { tasks: 6 } : {}, filled_at: exists ? new Date().toISOString() : null, sends_blocked: true });
  await page.route('http://localhost:8000/api/v1/demo**', async (route) => {
    if (route.request().method() === 'POST') exists = true;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(status()) });
  });
  await page.goto('/#settings');
  const card = page.getByTestId('demo-workspace');
  await expect(card.getByText('There is no demo workspace yet.')).toBeVisible();
  await expect(card.getByText('Sending is off in the demo workspace')).toBeVisible();
  await card.getByRole('button', { name: 'Create demo workspace' }).click();
  await expect(card.getByText('The demo workspace now holds 31 sample records.')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Open demo workspace' })).toBeVisible();
  await card.getByRole('button', { name: 'Reset demo data' }).click();
  await page.getByRole('alertdialog', { name: 'Reset the demo data?' }).getByRole('button', { name: 'Reset demo data' }).click();
  await expect(card.getByText('The demo workspace now holds 31 sample records.')).toBeVisible();
});

test('a member sees who can create the demo workspace', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route('http://localhost:8000/api/v1/demo**', (route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Only a workspace owner can create or reset the demo workspace.' }) }));
  await page.goto('/#settings');
  await expect(page.getByTestId('demo-workspace').getByText('Only a workspace owner can create or reset the demo workspace.')).toBeVisible();
});
