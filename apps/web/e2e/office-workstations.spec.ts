import { expect, test, type Route } from '@playwright/test';
import { mockOrgChart } from './support/orgChartApi';
import { mockOfficeSpaces } from './support/hqApi';

// #office-preview opens the authenticated headquarters (HeadquartersEntrypoint); the workstation
// directory is its "Live workstations" panel.
async function openWorkstations(page: import('@playwright/test').Page) {
  await mockOrgChart(page);
  await mockOfficeSpaces(page);
  await page.goto('/#office-preview');
  await page.getByRole('button', { name: 'Office tools' }).click();
  await page.getByRole('button', { name: 'Live workstations' }).click();
}

const agentsUrl = (url: URL) => url.origin === 'http://localhost:8000' && url.pathname === '/api/v1/agents';

test('the office removes employee evidence when access is revoked and recovers after access returns', async ({ page }) => {
  await openWorkstations(page);
  const office = page.getByRole('dialog', { name: 'Live Employee Workstations' });
  await expect(office.getByRole('button', { name: /Fleet Planner/ })).toBeVisible();
  await office.getByRole('button', { name: /Fleet Planner/ }).click();
  await expect(office.getByRole('region', { name: 'Selected employee evidence' })).toContainText('Plan October tyre rotation');

  const deny = (route: Route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'Membership revoked.' } }) });
  await page.route(agentsUrl, deny);
  await office.getByRole('button', { name: 'Refresh' }).click();
  await expect(office.getByRole('alert')).toContainText('choose a workspace you can access');
  await expect(office.getByRole('button', { name: /Fleet Planner/ })).toHaveCount(0);
  await expect(office.getByRole('region', { name: 'Selected employee evidence' })).toHaveCount(0);

  await page.unroute(agentsUrl, deny);
  await office.getByRole('button', { name: 'Refresh' }).click();
  await expect(office.getByRole('button', { name: /Fleet Planner/ })).toBeVisible();
  await expect(office.getByRole('alert')).toHaveCount(0);
});

test('a failed refresh keeps the last authorized snapshot visibly stale until retry succeeds', async ({ page }) => {
  await openWorkstations(page);
  const office = page.getByRole('dialog', { name: 'Live Employee Workstations' });
  await office.getByRole('button', { name: /Fleet Planner/ }).click();
  const evidence = office.getByRole('region', { name: 'Selected employee evidence' });
  await expect(evidence).toContainText('Plan October tyre rotation');

  const disconnect = (route: Route) => route.abort('failed');
  await page.route(agentsUrl, disconnect);
  await office.getByRole('button', { name: 'Refresh' }).click();
  await expect(office.getByRole('alert')).toContainText('Check your connection and try Refresh');
  await expect(office.getByText('Snapshot stale')).toBeVisible();
  await expect(evidence.locator('.workstationStatus')).toHaveText('Stale');
  await expect(evidence).toContainText('Plan October tyre rotation');

  await page.unroute(agentsUrl, disconnect);
  await office.getByRole('button', { name: 'Refresh' }).click();
  await expect(office.getByText('Snapshot current')).toBeVisible();
  await expect(evidence.locator('.workstationStatus')).toHaveText('working');
});
