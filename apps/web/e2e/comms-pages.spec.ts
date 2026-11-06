import { expect, test } from '@playwright/test';
import { mockCallsApi } from './support/callsApi';
import { mockWorkspaceApi } from './support/workspaceApi';

// The communication pages on the shared shell (docs/web-design-system.md#communication-pages): the
// Phone calls figures, period filter and dial board, and the Meetings tabs, all from the (mocked) API.

test('Phone calls: today’s figures come from the summary, a logged call counts, the period filter asks the API', async ({ page }) => {
  const { calls } = await mockCallsApi(page);
  await page.goto('/#calls');
  const figures = page.getByRole('region', { name: 'Calls at a glance' });
  await expect(figures.locator('.finKpi').first().locator('.finKpiValue')).toHaveText('0');
  await expect(figures.getByText('No missed calls today')).toBeVisible();
  // The sample calls are from October: Today has none, and says how to clear the filter.
  await page.getByLabel('Period').selectOption('today');
  await expect(page.getByText('No call matches')).toBeVisible();
  expect(calls.listQueries.at(-1)).toContain('since=');
  await page.getByRole('button', { name: 'Clear search and filters' }).click();
  await expect(page.getByRole('heading', { name: /^Earlier \(2\)/ })).toBeVisible();
  // Logging a call made now adds it to Today and to the figures.
  await page.getByRole('button', { name: 'Log a call' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Number').fill('+971509998888');
  await dialog.getByRole('button', { name: 'Save to the log' }).click();
  await expect(figures.locator('.finKpi').first().locator('.finKpiValue')).toHaveText('1');
  // Phones open the logged call itself; wide screens show it under Today beside it.
  if ((page.viewportSize()?.width ?? 0) >= 1024) await expect(page.getByRole('heading', { name: /^Today \(1\)/ })).toBeVisible();
  else await expect(page.getByRole('heading', { level: 2, name: '+971509998888' })).toBeVisible();
});

test('Phone calls: picking a contact on the dial board fills the number and shows who it is', async ({ page }) => {
  await mockCallsApi(page);
  await page.goto('/#calls-dial');
  await page.getByRole('button', { name: /Sara Example/ }).click();
  await expect(page.locator('.callsDial input')).toHaveValue('+971501234567');
  const about = page.getByRole('region', { name: 'Sara Example' });
  await expect(about.getByText('Al Noor Trading')).toBeVisible();
  await about.getByRole('button', { name: 'Open contact' }).click();
  await expect(page).toHaveURL(/#calls-contacts\?contact=contact_sara$/);
});

test('Phone calls: with AI calls off the AI tab says why and offers the provider setup', async ({ page }) => {
  await mockCallsApi(page);
  await page.goto('/#calls-ai');
  await expect(page.getByText('AI calls are off until the voice provider is set up, and every call needs an owner’s approval.')).toBeVisible();
  await page.getByRole('button', { name: 'Go to provider setup' }).click();
  await expect(page.getByRole('tab', { name: 'Provider', selected: true })).toBeVisible();
});

test('Meetings: the tabs show the processing queue, the connectors and the live rooms', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#meetings');
  const figures = page.getByRole('region', { name: 'Meetings and action items' });
  await expect(figures.locator('.finKpi').first().locator('.finKpiValue')).toHaveText('2');
  await expect(page.getByRole('complementary', { name: 'Meeting connections and processing' }).getByText('Zoom', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: /^Processing queue/ }).click();
  await expect(page.getByText('Nothing is waiting')).toBeVisible();
  await page.getByRole('tab', { name: 'Connectors' }).click();
  await expect(page.locator('.meetingConnectorBlock').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Live meetings' }).click();
  await expect(page.getByRole('region', { name: 'Live agent meetings' })).toBeVisible();
  await page.getByRole('tab', { name: 'Meeting records' }).click();
  await expect(page.locator('.meetingRows li').first()).toBeVisible();
});
