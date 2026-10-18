import { expect, test, type Page } from '@playwright/test';
import { auditButtons } from './support/deadButtons';
import { GATE_PAGES } from './support/gatePages';
import { mockWorkspaceApi } from './support/workspaceApi';
import { createCalendarMock } from './support/calendarApi';
import { mockDeveloper } from './support/developerApi';

// Journeys (CLAUDE.md, definition of done): no dead buttons on any page, and the main path of each
// module through create, read, update and delete against the mocked API. The module specs
// (business.spec.ts, files.spec.ts, email-center.spec.ts, finance*.spec.ts, ...) go deeper on each
// module; this spec makes sure every page is covered by the same rule.
//
// The audit runs once, at a desktop size, in the desktop project (playwright.config.ts leaves the
// @gate tests out of the mobile project): each button is judged by what it does, not by the size.

test.describe('no dead buttons @gate', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const item of GATE_PAGES) {
    test(`#${item.hash}: every button does something`, async ({ page }) => {
      test.setTimeout(240_000);
      await item.mock(page);
      expect(await auditButtons(page, `/#${item.hash}`, item.ready)).toEqual([]);
    });
  }

  test('the shared header and sidebar: every button does something', async ({ page }) => {
    test.setTimeout(240_000);
    await mockWorkspaceApi(page);
    expect(await auditButtons(page, '/#home', '.commandPanel .listRow', { shell: true })).toEqual([]);
  });
});

async function openCalendar(page: Page) {
  await mockWorkspaceApi(page);
  const calendar = createCalendarMock();
  await page.route('http://localhost:8000/api/v1/calendar/**', async (route) => { if (!(await calendar.handle(route))) await route.fallback(); });
  await page.goto('/#calendar');
  return calendar.state;
}

test('Calendar: create, read, update and delete an event', async ({ page }) => {
  const state = await openCalendar(page);
  await page.getByRole('button', { name: 'New event' }).first().click();
  const add = page.getByRole('dialog', { name: 'New event' });
  await add.getByLabel('Title').fill('Journey check');
  await add.getByRole('button', { name: 'Add event' }).click();
  await expect(page.getByText('Added "Journey check".')).toBeVisible();
  expect(state.created).toHaveLength(1);
  const detail = page.getByRole('article', { name: 'Journey check' });
  await expect(detail).toBeVisible();
  await detail.getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('dialog', { name: 'Edit event' });
  await edit.getByLabel('Place').fill('Warehouse');
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('Saved "Journey check".')).toBeVisible();
  await page.getByRole('article', { name: 'Journey check' }).getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this event?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Deleted "Journey check".')).toBeVisible();
  expect(state.deleted).toHaveLength(1);
});

test('Developer projects: create, read, update and delete a project', async ({ page }) => {
  const api = await mockDeveloper(page);
  await page.goto('/#developer');
  await page.getByRole('navigation', { name: 'Developer sections' }).getByRole('button', { name: /Projects/ }).click();
  await page.getByRole('button', { name: 'Add a project' }).first().click();
  const form = page.getByRole('form', { name: 'Add a project' });
  await form.getByLabel('Name').fill('TyrePulse');
  await form.getByLabel('GitHub repository').fill('acme/tyrepulse');
  await form.getByLabel('Fine-grained token').fill('example');
  await form.getByRole('button', { name: 'Save project' }).click();
  await expect(page.getByText(/Added TyrePulse\./)).toBeVisible();
  const card = page.getByRole('article', { name: 'Project TyrePulse' });
  await expect(card.getByText('acme/tyrepulse')).toBeVisible();

  await card.getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('form', { name: 'Edit TyrePulse' });
  await edit.getByLabel('Branch to build on').fill('develop');
  await edit.getByRole('button', { name: 'Save project' }).click();
  await expect(page.getByText('Saved TyrePulse.')).toBeVisible();
  expect(api.projects.find((p) => p.name === 'TyrePulse')?.default_branch).toBe('develop');

  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('article', { name: 'Project TyrePulse' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('article', { name: 'Project TyrePulse' })).toHaveCount(0);
  expect(api.projects.map((p) => p.name)).toEqual(['ANUM']);
});
