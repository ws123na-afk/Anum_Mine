import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { createCalendarMock, type CalendarMockOptions } from './support/calendarApi';

// The web Calendar page (docs/calendar.md#web-page) against a stateful calendar API: the week
// agenda and an event's detail, New event, edit, cancel, delete, recorded replies, the next steps,
// and the refused, failed and empty states.
async function openCalendar(page: Page, options: CalendarMockOptions = {}) {
  await mockWorkspaceApi(page);
  const calendar = createCalendarMock(options);
  // Registered after the workspace mock, so this calendar answers first.
  await page.route('http://localhost:8000/api/v1/calendar/**', async (route) => { if (!(await calendar.handle(route))) await route.fallback(); });
  await page.goto('/#calendar');
  return calendar.state;
}
const agenda = (page: Page) => page.getByRole('region', { name: 'Agenda' });
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the week agenda lists events by day and opens one with its people and replies', async ({ page }) => {
  await openCalendar(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Calendar' })).toBeVisible();
  await expect(agenda(page).getByRole('button', { name: /Supplier call: Michelin order/ })).toBeVisible();
  await agenda(page).getByRole('button', { name: /Fleet review with the team/ }).click();
  const detail = page.getByRole('article', { name: 'Fleet review with the team' });
  await expect(detail.getByText('Room 2')).toBeVisible();
  await expect(detail.getByRole('link', { name: /Join/ })).toHaveAttribute('href', 'https://meet.example.com/fleet');
  await expect(detail.getByText('1 not answered · 1 accepted')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Calendar at a glance' })).toContainText('Replies missing1');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('cancelled events show only when asked; search narrows the agenda', async ({ page }) => {
  await openCalendar(page);
  await expect(agenda(page).getByRole('button', { name: /Quarterly budget review/ })).toHaveCount(0);
  await page.getByLabel('Show cancelled').check();
  await expect(agenda(page).getByRole('button', { name: /Quarterly budget review/ })).toContainText('Cancelled');
  await agenda(page).getByRole('searchbox').fill('omar');
  await expect(agenda(page).getByRole('button', { name: /Fleet review with the team/ })).toBeVisible();
  await expect(agenda(page).getByRole('button', { name: /Supplier call/ })).toHaveCount(0);
  await agenda(page).getByRole('searchbox').fill('nothing like this');
  await expect(page.getByRole('heading', { name: 'No events match your search' })).toBeVisible();
});

test('New event adds an event with people; edit, reply, cancel and delete go through the API', async ({ page }) => {
  const state = await openCalendar(page);
  await page.getByRole('button', { name: 'New event' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New event' });
  await dialog.getByRole('button', { name: 'Add event' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Give the event a title.');
  await dialog.getByLabel('Title').fill('Tyre stock count');
  await dialog.getByLabel('People').fill('Sara Ali <sara@example.com>\nNadia');
  await dialog.getByRole('button', { name: 'Add event' }).click();
  await expect(page.getByText('Added "Tyre stock count".')).toBeVisible();
  expect(state.created).toHaveLength(1);
  expect((state.created[0] as { attendees: unknown[] }).attendees).toEqual([{ name: 'Sara Ali', email: 'sara@example.com' }, { name: 'Nadia', email: null }]);
  const detail = page.getByRole('article', { name: 'Tyre stock count' });
  await detail.getByLabel('Reply from Nadia').selectOption('accepted');
  await expect(page.getByText('Saved the reply from Nadia.')).toBeVisible();
  await detail.getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('dialog', { name: 'Edit event' }).getByLabel('Place').fill('Warehouse');
  await page.getByRole('dialog', { name: 'Edit event' }).getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('Saved "Tyre stock count".')).toBeVisible();
  await page.getByRole('article', { name: 'Tyre stock count' }).getByRole('button', { name: 'Cancel event' }).click();
  await expect(page.getByText('Cancelled "Tyre stock count".')).toBeVisible();
  await page.getByRole('article', { name: 'Tyre stock count' }).getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this event?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Deleted "Tyre stock count".')).toBeVisible();
  expect(state.deleted).toHaveLength(1);
});

test('next steps start a task or open the page they point to', async ({ page }) => {
  await openCalendar(page);
  await page.getByRole('tab', { name: /Next steps/ }).click();
  await expect(page.getByText('Prepare for Fleet review with the team')).toBeVisible();
  await page.getByRole('listitem').filter({ hasText: 'Review 1 approval' }).getByRole('button', { name: 'Open' }).click();
  await expect(page).toHaveURL(/#approvals$/);
});

test('a viewer reads the calendar without Add or Edit, and a failed next steps load says so', async ({ page, isMobile }) => {
  await openCalendar(page, { role: 'viewer', failNextSteps: true });
  await expect(page.getByRole('button', { name: 'New event' })).toHaveCount(0);
  await agenda(page).getByRole('button', { name: /Fleet review with the team/ }).click();
  await expect(page.getByText('Only the person who added this event or a workspace owner can change it.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Calendar at a glance' })).toContainText('Unavailable');
  // On a narrow screen the event replaces the agenda until Back.
  if (isMobile) await page.getByRole('button', { name: 'Back to the list' }).click();
  await page.getByRole('tab', { name: /Next steps/ }).click();
  await expect(page.getByRole('alert')).toContainText('Could not load the next steps');
});

test('a role without calendar access is told who can see it', async ({ page }) => {
  await openCalendar(page, { deny: true });
  await expect(page.getByRole('heading', { name: 'The calendar is not available to your role' })).toBeVisible();
  await expect(page.getByText('Seeing the calendar needs a viewer, member or owner role in this workspace.')).toBeVisible();
});

test('an empty week offers New event; the day view moves one day at a time', async ({ page }) => {
  await openCalendar(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'Nothing planned this week' })).toBeVisible();
  await page.getByRole('button', { name: 'Day', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Nothing planned this day' })).toBeVisible();
  await page.getByRole('button', { name: 'Next day' }).click();
  await expect(page.getByRole('heading', { name: 'Nothing planned this day' })).toBeVisible();
});

test('the Calendar reads right to left in Arabic', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await openCalendar(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'التقويم' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'حدث جديد' }).first()).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

// Calendar sync with Google and Microsoft (docs/calendar.md#sync).
const syncTab = (page: Page) => page.getByRole('tab', { name: 'Sync' });

test('without a connection the Sync tab offers the calendars an owner can connect', async ({ page }) => {
  await openCalendar(page, { sync: 'none' });
  await expect(page.getByText('Events live in ANUM until a Google or Microsoft calendar is connected under Sync.')).toBeVisible();
  await syncTab(page).click();
  await expect(page.getByRole('heading', { name: 'No calendar connected yet' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Google Calendar' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Microsoft 365 Calendar' })).toBeVisible();
  await expect(page.getByText('Address to register with the provider: https://api.example.com/api/v1/calendar/sync/oauth/callback')).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a provider the server has no OAuth client for says which settings are missing', async ({ page }) => {
  await openCalendar(page, { sync: 'unconfigured' });
  await syncTab(page).click();
  await expect(page.getByText('Not set up', { exact: true })).toBeVisible();
  await expect(page.getByText(/ANUM_CALENDAR_GOOGLE_CLIENT_ID, ANUM_CALENDAR_GOOGLE_CLIENT_SECRET/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Google Calendar' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Connect Microsoft 365 Calendar' })).toBeVisible();
});

test('connecting sends the browser to the provider and finishes the connection on return', async ({ page }) => {
  const state = await openCalendar(page, { sync: 'none' });
  await syncTab(page).click();
  await page.getByRole('button', { name: 'Connect Google Calendar' }).click();
  await expect(page.getByText('Connected owner@example.com. 3 events synced.')).toBeVisible();
  expect(state.connectStarts).toEqual(['google']);
  await expect(page).toHaveURL(/#calendar$/);
  await expect(page).not.toHaveURL(/calendar_connect/);
  const connections = page.getByRole('region', { name: 'Connected calendars' });
  await expect(connections.getByText('owner@example.com')).toBeVisible();
});

test('a connected calendar shows its health, syncs on request and disconnects after a confirmation', async ({ page }) => {
  const state = await openCalendar(page, { sync: 'connected' });
  await expect(page.getByText('Synced with Google Calendar (owner@example.com). Changes go both ways.')).toBeVisible();
  await syncTab(page).click();
  const connections = page.getByRole('region', { name: 'Connected calendars' });
  await expect(connections.getByText(/Last sync 5 min ago/)).toBeVisible();
  await expect(connections.getByText('Last pass: 2 added, 1 updated, 0 removed, 1 sent. Conflicts settled: 1 by the calendar, 0 by ANUM.')).toBeVisible();
  await connections.getByRole('button', { name: 'Sync now' }).click();
  await expect(page.getByText('Synced owner@example.com.')).toBeVisible();
  expect(state.syncs).toEqual(['cal_1']);
  await connections.getByRole('button', { name: 'Disconnect' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Disconnect this calendar?' });
  await expect(confirm).toContainText('The events already in ANUM stay here');
  await confirm.getByRole('button', { name: 'Disconnect' }).click();
  await expect(page.getByText(/Google Calendar is disconnected/)).toBeVisible();
  expect(state.disconnected).toEqual(['cal_1']);
  await expect(page.getByRole('heading', { name: 'No calendar connected yet' })).toBeVisible();
});

test('a revoked grant says what to do and offers to connect again', async ({ page }) => {
  await openCalendar(page, { sync: 'reauth' });
  await expect(page.getByText(/Google Calendar sync needs attention/)).toBeVisible();
  await syncTab(page).click();
  const connections = page.getByRole('region', { name: 'Connected calendars' });
  await expect(connections.getByRole('alert')).toContainText('Token has been expired or revoked.');
  await expect(connections.getByRole('alert')).toContainText('Press Connect Google Calendar again and sign in');
  await expect(connections.getByRole('button', { name: 'Connect Google Calendar again' })).toBeVisible();
  await expect(connections.getByRole('button', { name: 'Sync now' })).toBeDisabled();
});

test('a synced event shows where it lives; invitations are only requested, never sent', async ({ page }) => {
  const state = await openCalendar(page, { sync: 'connected' });
  await agenda(page).getByRole('button', { name: /Fleet review with the team/ }).click();
  const detail = page.getByRole('article', { name: 'Fleet review with the team' });
  await expect(detail.getByText('Synced with Google Calendar')).toBeVisible();
  await expect(detail.getByText(/Invitations have not been approved for this event as it is now/)).toBeVisible();
  await detail.getByRole('button', { name: 'Ask to send invitations' }).click();
  await expect(detail.getByText(/Waiting for an owner to approve inviting sara@example.com, omar@example.com. Nothing has been sent./)).toBeVisible();
  expect(state.invitations).toEqual(['evt_1']);
  await expect(detail.getByRole('button', { name: 'Ask to send invitations' })).toHaveCount(0);
  await detail.getByRole('button', { name: 'Review approvals' }).click();
  await expect(page).toHaveURL(/#approvals$/);
});

test('New event can stay in ANUM only when a calendar is connected', async ({ page }) => {
  const state = await openCalendar(page, { sync: 'connected' });
  await page.getByRole('button', { name: 'New event' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'New event' });
  await expect(dialog.getByLabel('Also add to Google Calendar (owner@example.com)')).toBeChecked();
  await dialog.getByLabel('Title').fill('Private planning');
  await dialog.getByLabel('Also add to Google Calendar (owner@example.com)').uncheck();
  await dialog.getByRole('button', { name: 'Add event' }).click();
  await expect(page.getByText('Added "Private planning".')).toBeVisible();
  expect((state.created[0] as { sync?: boolean }).sync).toBe(false);
});

test('a viewer sees the sync status but cannot connect or sync', async ({ page }) => {
  await openCalendar(page, { role: 'viewer', sync: 'connected' });
  await syncTab(page).click();
  await expect(page.getByText('Only workspace owners can connect, sync or disconnect calendars.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sync now' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Connect / })).toHaveCount(0);
});

test('a failed sync status load is explained without breaking the agenda', async ({ page }) => {
  await openCalendar(page, { failSync: true });
  await expect(agenda(page).getByRole('button', { name: /Fleet review with the team/ })).toBeVisible();
  await syncTab(page).click();
  await expect(page.getByRole('alert')).toContainText('Could not load the calendar connections');
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('the Sync tab reads right to left in Arabic without overflow', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await openCalendar(page, { sync: 'connected' });
  await page.getByRole('tab', { name: 'المزامنة' }).click();
  await expect(page.getByRole('button', { name: 'زامن الآن' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
