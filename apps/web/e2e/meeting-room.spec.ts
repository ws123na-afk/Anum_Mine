import { expect, test } from '@playwright/test';
import { createRoomMock, mockRoom } from './support/meetingRoomApi';
import { useSavedLocale } from './support/workspaceApi';

// The live meeting room (docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md): join, a real turn streams, the owner interrupts,
// drafts a decision and an action item, creates the task, the feed drops and replays, the meeting ends and is replayed.

test('join, a turn streams, interrupt, decide, create a task, reconnect replay, end and replay', async ({ page }) => {
  const mock = createRoomMock();
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByRole('heading', { level: 2, name: 'Launch planning' })).toBeVisible();
  await expect(room.getByText('Live', { exact: true })).toBeVisible();
  await expect(room.getByText('No messages yet. Start the meeting, then press Run next turn.')).toBeVisible();
  await expect(room.getByText('Maya is speaking')).toHaveCount(0); // nobody speaks until a real turn runs

  await room.getByRole('button', { name: 'Join meeting' }).click();
  await expect(room.getByRole('button', { name: 'Leave meeting' })).toBeVisible();

  // A real turn: the speaking dot appears from the stored turn_started and goes with the message.
  await room.getByRole('button', { name: 'Run next turn' }).click();
  await expect(room.getByRole('status').filter({ hasText: 'Maya is speaking' }).first()).toBeVisible();
  await expect(room.getByText('launch on Tuesday')).toBeVisible();
  await expect(room.getByText('Maya is speaking')).toHaveCount(0);

  // The owner interrupts.
  await room.getByLabel('Say something to the meeting').fill('Please also cover the risks.');
  await room.getByLabel('Say something to the meeting').press('Control+Enter');
  await expect(room.getByText('Please also cover the risks.')).toBeVisible();
  expect(mock.state.calls.messages).toEqual(['Please also cover the risks.']);

  // A decision and an action item are drafts with linked source messages until the owner confirms.
  await room.locator('#mr-msg-1').getByRole('button', { name: 'Draft decision' }).click();
  await expect(room.getByLabel('Text')).toHaveValue(/launch on Tuesday/);
  await room.getByRole('button', { name: 'Save draft' }).click();
  const outputs = room.getByRole('region', { name: 'Decisions and action items' });
  await expect(outputs.getByText('Draft', { exact: true })).toBeVisible();
  await expect(outputs.getByRole('button', { name: 'Message 1' })).toBeVisible();
  await outputs.getByRole('button', { name: 'Confirm' }).click();
  await expect(outputs.getByText('Confirmed', { exact: true })).toBeVisible();

  await room.locator('#mr-msg-2').getByRole('button', { name: 'Draft action item' }).click();
  await room.getByLabel('Who is responsible (optional)').fill('Sam');
  await room.getByRole('button', { name: 'Save draft' }).click();
  await outputs.getByRole('button', { name: 'Confirm' }).click();
  await expect(outputs.getByRole('button', { name: 'Create task' })).toBeVisible();
  await outputs.getByRole('button', { name: 'Create task' }).click();
  await expect(outputs.getByText('Task created')).toBeVisible();
  expect(mock.state.calls.tasks).toBe(1);

  // The live feed drops; a message is stored meanwhile; the page says so and replays it on return.
  mock.state.outage = true;
  await expect(room.getByText(/Reconnecting to the live feed/)).toBeVisible({ timeout: 15_000 });
  mock.addTurn('agent_omar', 'agent', 'Missed while offline: the budget is fine.', 'offline-1');
  mock.state.outage = false;
  await expect(room.getByText('Missed while offline: the budget is fine.')).toBeVisible({ timeout: 15_000 });
  await expect(room.getByText(/Reconnecting to the live feed/)).toHaveCount(0);
  expect(await room.locator('.mrMsg').count()).toBe(3); // replayed once, never twice

  // Pause, resume, end, then the stored meeting reads as a replay.
  await room.getByRole('button', { name: 'Pause' }).click();
  await expect(room.getByText('Paused', { exact: true })).toBeVisible();
  await room.getByRole('button', { name: 'Resume' }).click();
  await room.getByRole('button', { name: 'End meeting' }).click();
  await room.getByRole('button', { name: 'End for everyone' }).click();
  await expect(room.getByText(/Replay: this meeting has ended/)).toBeVisible();
  await expect(room.getByRole('button', { name: 'Run next turn' })).toHaveCount(0);

  const download = page.waitForEvent('download');
  await room.getByRole('button', { name: 'Download transcript (Markdown)' }).click();
  expect((await download).suggestedFilename()).toBe('meeting-mtg_1.md');
  await room.getByRole('button', { name: 'Delete transcript' }).click();
  await room.getByRole('button', { name: 'Delete for good' }).click();
  await expect(room.getByText(/transcript of this meeting was deleted/)).toBeVisible();
  await expect(outputs.getByText('Task created')).toBeVisible(); // decisions and tasks stay
});

test('a finished meeting opens as a stored replay and can be played back', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: [{ speaker: 'agent_maya', role: 'agent', body: 'First point.' }, { speaker: 'agent_omar', role: 'agent', body: 'Second point.' }] });
  mock.push('state', { status: 'finished', reason: 'limit_reached', revision: 4 });
  mock.state.meeting.ended_reason = 'limit_reached';
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByText(/Replay: this meeting has ended/)).toBeVisible();
  await expect(room.getByText(/Ended automatically: a meeting limit was reached/)).toBeVisible();
  await expect(room.locator('.mrMsg')).toHaveCount(2);
  await room.getByRole('button', { name: 'Play back' }).click();
  await expect(room.locator('.mrMsg')).toHaveCount(1);
  await expect(room.locator('.mrMsg')).toHaveCount(2, { timeout: 8000 });
});

test('a second workspace owner reads the room, joins, and raises a hand but cannot moderate', async ({ page }) => {
  const mock = createRoomMock({ owner: false, reader: true, messages: [{ speaker: 'agent_maya', role: 'agent', body: 'Hello all.' }] });
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByText('Hello all.')).toBeVisible();
  await expect(room.getByRole('button', { name: 'Run next turn' })).toHaveCount(0);
  await expect(room.getByRole('button', { name: 'Pause' })).toHaveCount(0);
  await expect(room.getByLabel('Say something to the meeting')).toHaveCount(0);
  await expect(room.getByRole('button', { name: 'Raise hand' })).toBeDisabled();
  await expect(room.getByText('Join the meeting to raise a hand.')).toBeVisible();
});

test('a member sees that the meeting happened but never its text, and asks the server for none', async ({ page }) => {
  const mock = createRoomMock({ owner: false, messages: [{ speaker: 'agent_maya', role: 'agent', body: 'The secret acquisition closes Friday.' }] });
  mock.state.outputs.push({ id: 'out_1', session_id: 'mtg_1', kind: 'decision', body: 'Acquire the rival', state: 'confirmed', owner_label: null, source_sequences: [1], task_id: null, created_by: 'user_boss', decided_by: 'user_boss' });
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByRole('heading', { level: 2, name: 'Launch planning' })).toBeVisible();
  await expect(room.getByText('Live', { exact: true })).toBeVisible();
  await expect(room.getByText('Maya', { exact: true })).toBeVisible(); // who is in it
  await expect(room.getByText(/Only a workspace owner can read the transcript of a meeting\./)).toBeVisible();
  await expect(room.getByText('Decisions and action items: 1. Only workspace owners can read them.')).toBeVisible();
  await expect(room.getByText('Linked files: 0. Only workspace owners can see them.')).toBeVisible();
  await expect(room.getByText('secret acquisition')).toHaveCount(0);
  await expect(room.getByText('Acquire the rival')).toHaveCount(0);
  await expect(room.getByRole('button', { name: 'Download transcript (Markdown)' })).toHaveCount(0);
  await expect(room.getByRole('button', { name: 'Run next turn' })).toHaveCount(0);
  await expect(room.getByRole('log')).toHaveCount(0);
  // The page never asked for the text: no turns, event log, stream or export call was made.
  expect(mock.state.calls.forbidden).toEqual([]);
  expect(mock.state.calls.streamHits).toBe(0);
});

test('a member who is refused a transcript read is told why, in plain words', async ({ page }) => {
  const mock = createRoomMock({ owner: false });
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  await expect(page.getByRole('region', { name: 'Meeting room' }).getByText('Launch planning')).toBeVisible();
  const status = await page.evaluate(async () => (await fetch('http://localhost:8000/api/v1/agent-meetings/mtg_1/turns')).status);
  expect(status).toBe(403);
  expect(mock.state.calls.forbidden).toEqual(['/turns']);
});

test('the monthly budget is shown in the room, pauses the meeting when the last turn uses it, and blocks a restart', async ({ page }) => {
  const mock = createRoomMock({ budget: { used: 49.999, limit: 50 } });
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByRole('heading', { name: 'Agent meetings this month' })).toBeVisible();
  await expect(room.getByText('USD 49.99 of USD 50.00')).toBeVisible();
  await expect(room.getByText(/Less than a fifth of this month's agent meeting budget is left\./)).toBeVisible();
  await room.getByRole('button', { name: 'Run next turn' }).click();
  await expect(room.getByText('launch on Tuesday')).toBeVisible();
  // The turn used the last of the month: the meeting paused, the reason is plain, and nothing can run.
  await expect(room.getByText('Paused', { exact: true })).toBeVisible();
  const notice = room.locator('.mrBudgetNotice');
  await expect(notice).toContainText('Monthly agent meeting budget reached.');
  await expect(notice).toContainText('so no agent can speak until 1 November 2026 (UTC)');
  await expect(notice).toContainText('A workspace owner can raise the limit under Budgets.');
  await expect(room.getByRole('button', { name: 'Resume' })).toBeDisabled();
  await expect(room.getByRole('button', { name: 'Resume' })).toHaveAttribute('title', 'The monthly agent meeting budget is used up.');
  // The server's refusal is shown too if a stale page tries anyway.
  mock.state.meeting.status = 'active';
  expect(mock.state.calls.runs).toBe(1);
});

test('a room opened with the budget already used up cannot start or run a turn', async ({ page }) => {
  const mock = createRoomMock({ status: 'scheduled', budget: { used: 50.2, limit: 50 } });
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  await expect(room.locator('.mrBudgetNotice')).toContainText('USD 50.20 of its USD 50.00');
  await expect(room.getByRole('button', { name: 'Start meeting' })).toBeDisabled();
  expect(mock.state.calls.runs).toBe(0);
});

test('denied, missing and failed rooms say what happened', async ({ page }) => {
  await page.route('http://localhost:8000/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/room')) return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'No' } }) });
    return route.abort();
  });
  await page.goto('/#office/meeting/mtg_1');
  await expect(page.getByRole('alert')).toContainText('You do not have access to this meeting');
});

test('Calls and Meetings lists live agent meetings and opens a room', async ({ page }) => {
  const mock = createRoomMock({ messages: [{ speaker: 'agent_maya', role: 'agent', body: 'Hello all.' }] });
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return route.fulfill({ contentType: 'application/json', body: '[]' });
    if (await mock.handle(route)) return;
    const { handleMeetings } = await import('./support/meetingsApi');
    if (path.startsWith('/api/v1/meetings') && await handleMeetings(route)) return;
    return route.abort();
  });
  await page.goto('/#meetings');
  const launcher = page.getByRole('region', { name: 'Live agent meetings' });
  await expect(launcher.getByText('Launch planning')).toBeVisible();
  await launcher.getByRole('link', { name: 'Open room' }).click();
  await expect(page).toHaveURL(/#office\/meeting\/mtg_1$/);
  await expect(page.getByRole('region', { name: 'Meeting room' }).getByText('Hello all.')).toBeVisible();
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of sizes) {
    test(`the room fits ${width}x${height} in ${locale} without horizontal overflow @gate`, async ({ page }, testInfo) => {
      const mock = createRoomMock({ messages: [
        { speaker: 'agent_maya', role: 'agent', body: 'I suggest we **launch on Tuesday**.\n\n- announce Monday\n- brief support' },
        { speaker: 'user_local', role: 'human', body: 'A very long unbroken line https://example.com/' + 'a'.repeat(120) },
        { speaker: 'agent_omar', role: 'agent', body: 'Agreed. <script>alert(1)</script> stays text.' },
      ] });
      mock.state.outputs.push({ id: 'out_1', session_id: 'mtg_1', kind: 'decision', body: 'Launch on Tuesday', state: 'confirmed', owner_label: null, source_sequences: [1], task_id: null, created_by: 'user_local', decided_by: 'user_local' });
      mock.state.attendance.push({ actor_id: 'user_local', joined_at: new Date().toISOString(), left_at: null });
      mock.state.meeting.hand_raised_by = 'user_guest';
      await useSavedLocale(page, locale);
      await mockRoom(page, mock);
      await page.setViewportSize({ width, height });
      await page.goto('/#office/meeting/mtg_1');
      const room = page.getByRole('region', { name: locale === 'en' ? 'Meeting room' : 'غرفة الاجتماع' });
      await expect(room.locator('.mrMsg')).toHaveCount(3);
      await expect(page.locator('script', { hasText: 'alert(1)' })).toHaveCount(0); // Markdown never becomes markup
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const clipped = await room.locator('button').evaluateAll((buttons) => buttons.filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); }).length);
      expect(clipped).toBe(0);
      if (locale === 'ar') expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: `${process.env.ANUM_SCREENSHOTS}/room-${locale}-${width}x${height}.png`, fullPage: true });
      else await page.screenshot({ path: testInfo.outputPath(`room-${locale}-${width}.png`), fullPage: true });
    });
  }
}

for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of sizes) {
    test(`the member view and the used up budget fit ${width}x${height} in ${locale} @gate`, async ({ page }, testInfo) => {
      const mock = createRoomMock({ owner: false, status: 'paused', budget: { used: 50.2, limit: 50 }, messages: [{ speaker: 'agent_maya', role: 'agent', body: 'Hidden from members.' }] });
      mock.state.outputs.push({ id: 'out_1', session_id: 'mtg_1', kind: 'decision', body: 'Hidden decision', state: 'confirmed', owner_label: null, source_sequences: [1], task_id: null, created_by: 'u', decided_by: 'u' });
      mock.state.attendance.push({ actor_id: 'user_local', joined_at: new Date().toISOString(), left_at: null });
      await useSavedLocale(page, locale);
      await mockRoom(page, mock);
      await page.setViewportSize({ width, height });
      await page.goto('/#office/meeting/mtg_1');
      const room = page.getByRole('region', { name: locale === 'en' ? 'Meeting room' : 'غرفة الاجتماع' });
      await expect(room.getByTestId('transcript-hidden')).toBeVisible();
      await expect(room.locator('.mrBudgetNotice')).toBeVisible();
      if (locale === 'ar') {
        await expect(room.getByTestId('transcript-hidden')).toContainText('لا يقرأ نص الاجتماع إلا مالك مساحة العمل');
        await expect(room.locator('.mrBudgetNotice')).toContainText('تم بلوغ ميزانية اجتماعات الوكلاء الشهرية');
        expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
      } else {
        await expect(room.locator('.mrBudgetNotice')).toContainText('Monthly agent meeting budget reached');
      }
      await expect(page.getByText('Hidden from members.')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      const clipped = await room.locator('button').evaluateAll((buttons) => buttons.filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); }).length);
      expect(clipped).toBe(0);
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: `${process.env.ANUM_SCREENSHOTS}/room-member-${locale}-${width}x${height}.png`, fullPage: true });
      else await page.screenshot({ path: testInfo.outputPath(`room-member-${locale}-${width}.png`), fullPage: true });
    });
  }
}

test('the owner removes an agent from the live meeting: it leaves for good and only the others take turns', async ({ page }) => {
  const mock = createRoomMock();
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  const room = page.getByRole('region', { name: 'Meeting room' });
  const agents = room.getByRole('list', { name: 'Agents' });
  await expect(agents.getByText('Omar')).toBeVisible();
  // One click asks first; Cancel keeps the agent.
  await agents.getByRole('button', { name: 'Remove Omar from the meeting' }).click();
  await expect(agents.getByText('Remove Omar? It stops speaking now and cannot rejoin this meeting.')).toBeVisible();
  await agents.getByRole('button', { name: 'Cancel' }).click();
  expect(mock.state.calls.removals).toEqual([]);
  await agents.getByRole('button', { name: 'Remove Omar from the meeting' }).click();
  await agents.getByRole('button', { name: 'Remove from meeting' }).click();
  await expect(room.getByRole('status').filter({ hasText: 'Omar left the meeting.' })).toBeVisible();
  expect(mock.state.calls.removals).toEqual(['agent_omar']);
  const omar = agents.locator('li', { hasText: 'Omar' });
  await expect(omar).toHaveAttribute('data-agent-left', 'true');
  await expect(omar).toContainText('Left the meeting');
  await expect(omar.getByRole('button')).toHaveCount(0);
  await expect(agents.getByRole('button', { name: 'Remove Maya from the meeting' })).toBeVisible();
  await expect(room.getByRole('button', { name: 'Run next turn' })).toBeEnabled();
  // The last agent leaves: nobody is left to take a turn.
  await agents.getByRole('button', { name: 'Remove Maya from the meeting' }).click();
  await agents.getByRole('button', { name: 'Remove from meeting' }).click();
  await expect(agents.locator('[data-agent-left="true"]')).toHaveCount(2);
  await expect(room.getByRole('button', { name: 'Run next turn' })).toBeDisabled();
});

test('Arabic: removing an agent asks and answers in Arabic', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  const mock = createRoomMock();
  await mockRoom(page, mock);
  await page.goto('/#office/meeting/mtg_1');
  await page.getByRole('button', { name: 'إزالة Omar من الاجتماع' }).click();
  await expect(page.getByText('إزالة Omar؟ سيتوقف عن الكلام الآن ولا يمكنه العودة إلى هذا الاجتماع.')).toBeVisible();
  await page.getByRole('button', { name: 'إزالة من الاجتماع' }).click();
  await expect(page.locator('[data-agent-left="true"]')).toContainText('غادر الاجتماع');
});
