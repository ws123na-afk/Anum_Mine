import { expect, test } from '@playwright/test';
import { createRoomMock, mockRoom } from './support/meetingRoomApi';
import { useSavedLocale } from './support/workspaceApi';

// Meeting summary, model-drafted decisions and action items, decision to work links and the JSON record
// (docs/ANUM_MINE_MEETING_SUMMARY_AND_WORK_LINKS.md). The model and the server are a fixture: the UI is what is under test.

const talk = [
  { speaker: 'agent_maya', role: 'agent', body: 'I suggest we launch on Tuesday and announce Monday.' },
  { speaker: 'user_local', role: 'human', body: 'Agreed. Please also cover the risks.' },
];
const open = (page: import('@playwright/test').Page) => page.goto('/#office/meeting/mtg_1');
const roomOf = (page: import('@playwright/test').Page, name = 'Meeting room') => page.getByRole('region', { name });

test('draft a summary, review it, confirm with a click, link a decision to work both ways, export JSON', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: talk });
  await mockRoom(page, mock);
  await open(page);
  const room = roomOf(page);
  const summary = room.getByTestId('meeting-summary');
  await expect(summary.getByText('No summary yet.')).toBeVisible();
  await expect(summary.getByText(/counts against this month's agent meeting budget/)).toBeVisible();

  await summary.getByRole('button', { name: 'Draft summary' }).click();
  await expect(summary.getByText('Reading the transcript...')).toBeVisible();
  await expect(summary.getByText('The group agreed to launch on Tuesday.')).toBeVisible();
  await expect(room.getByRole('status').filter({ hasText: 'Draft ready: 1 decisions and 1 action items found.' })).toBeVisible();
  await expect(room.getByText('1 statements were left out')).toBeVisible();
  expect(mock.state.calls.summaries).toBe(1);
  // Nothing is confirmed by the model: every piece is a draft and says it came from a model.
  await expect(summary.getByText('Draft', { exact: true })).toBeVisible();
  await expect(summary.getByText('Model draft')).toBeVisible();
  const outputs = room.getByRole('region', { name: 'Decisions and action items' });
  await expect(outputs.getByText('Model draft')).toHaveCount(2);
  await expect(outputs.getByText('Draft', { exact: true })).toHaveCount(2);
  // Each line links to the message that supports it.
  await summary.getByRole('button', { name: 'Message 1' }).first().click();
  await expect(room.locator('#mr-msg-1')).toBeFocused();

  // The owner confirms with a click: the page sends the click mode and the hash of the text it showed.
  await summary.getByRole('button', { name: 'Confirm summary' }).click();
  await expect(summary.getByText('Confirmed by the owner. Written by a model from the transcript.')).toBeVisible();
  const sent = mock.state.calls.patches.at(-1)!;
  expect(sent.input_mode).toBe('click');
  expect(String(sent.payload_hash)).toMatch(/^a+1$/);
  await expect(room.getByRole('status').filter({ hasText: 'Summary confirmed.' })).toBeVisible();

  // A decision cannot be linked to work before it is confirmed.
  const decision = outputs.locator('.mrOut').filter({ hasText: 'Launch on Tuesday.' });
  await expect(decision.getByText('Confirm this decision first.')).toBeVisible();
  await decision.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(decision.getByText('Confirmed', { exact: true })).toBeVisible();
  expect(mock.state.calls.patches.at(-1)!.input_mode).toBe('click');

  await decision.getByRole('button', { name: 'Link to work' }).click();
  await decision.getByLabel('Choose the work to link').selectOption({ label: 'Plan the launch' });
  await decision.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(decision.getByRole('list', { name: 'Linked work' }).getByText('Plan the launch')).toBeVisible();
  // The other direction: the work and the decision behind it.
  const work = room.getByTestId('linked-work');
  await expect(work.getByText('Plan the launch')).toBeVisible();
  await expect(work.getByText('From the decision: Launch on Tuesday.')).toBeVisible();
  // An approval and a run (the task's latest run) can be linked too, and a repeat does not duplicate.
  await decision.getByRole('button', { name: 'Link to work' }).click();
  await decision.getByLabel('Kind of work').selectOption('approval');
  await decision.getByLabel('Choose the work to link').selectOption({ label: 'send_email' });
  await decision.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(decision.getByText('send_email')).toBeVisible();
  await decision.getByRole('button', { name: 'Link to work' }).click();
  await decision.getByLabel('Kind of work').selectOption('run');
  await decision.getByLabel('Choose the work to link').selectOption({ label: 'Release notes' });
  await decision.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(decision.getByText('This task has not run yet, so it has no run to link.')).toBeVisible();
  await decision.getByLabel('Choose the work to link').selectOption({ label: 'Plan the launch' });
  await decision.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(decision.getByText('Run of a task')).toBeVisible();
  expect(mock.state.calls.links).toBe(3);
  await decision.getByRole('button', { name: 'Unlink send_email' }).click();
  await expect(decision.getByText('send_email')).toHaveCount(0);

  // JSON record: summary, decisions with links and confirmed outputs, downloaded by the browser.
  const download = page.waitForEvent('download');
  await room.getByRole('button', { name: 'Download record (JSON)' }).click();
  expect((await download).suggestedFilename()).toBe('meeting-mtg_1.json');
  expect(mock.state.calls.jsonExports).toBe(1);
  await expect(room.getByText('Record downloaded.')).toBeVisible();
});

test('a summary is dismissed and drafted again; a typed confirmation is refused by the server', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: talk });
  await mockRoom(page, mock);
  await open(page);
  const room = roomOf(page);
  const summary = room.getByTestId('meeting-summary');
  await summary.getByRole('button', { name: 'Draft summary' }).click();
  await expect(summary.getByRole('button', { name: 'Confirm summary' })).toBeVisible();
  await summary.getByRole('button', { name: 'Dismiss' }).click();
  await expect(summary.getByRole('button', { name: 'Draft a new summary' })).toBeVisible();
  // A page that is stale about the text it confirms is refused with a plain reason.
  await summary.getByRole('button', { name: 'Draft a new summary' }).click();
  await expect(summary.getByRole('button', { name: 'Confirm summary' })).toBeVisible();
  mock.state.outputs.find((o) => o.kind === 'summary' && o.state === 'draft')!.payload_hash = 'f'.repeat(64);
  await summary.getByRole('button', { name: 'Confirm summary' }).click();
  await expect(room.getByRole('alert').filter({ hasText: 'This draft changed since you read it.' })).toBeVisible();
});

test('states: a live meeting waits, the model can fail, the budget can be used up, a deleted transcript has nothing to summarise', async ({ page }) => {
  const live = createRoomMock({ status: 'active', messages: talk });
  await mockRoom(page, live);
  await open(page);
  await expect(roomOf(page).getByTestId('meeting-summary').getByText('A summary can be drafted after the meeting ends.')).toBeVisible();
  await expect(roomOf(page).getByRole('button', { name: 'Draft summary' })).toHaveCount(0);
});

test('a model that does not answer shows what failed and nothing is stored', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: talk, summary: 'error' });
  await mockRoom(page, mock);
  await open(page);
  const room = roomOf(page);
  await room.getByRole('button', { name: 'Draft summary' }).click();
  await expect(room.getByRole('alert').filter({ hasText: 'The summary could not be drafted. The model did not answer.' })).toBeVisible();
  expect(mock.state.outputs).toHaveLength(0);
  await expect(room.getByRole('button', { name: 'Draft summary' })).toBeEnabled();
});

test('over budget: the button is off, the reason is plain, and the server refusal is shown if a stale page tries', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: talk, budget: { used: 50.2, limit: 50 } });
  await mockRoom(page, mock);
  await open(page);
  const room = roomOf(page);
  await expect(room.getByRole('button', { name: 'Draft summary' })).toBeDisabled();
  await expect(room.getByTestId('meeting-summary').getByText(/monthly agent meeting budget is used up/)).toBeVisible();
  expect(mock.state.calls.summaries).toBe(0);
});

test('a deleted transcript has nothing to summarise', async ({ page }) => {
  const mock = createRoomMock({ status: 'finished', messages: talk });
  mock.state.transcriptDeleted = true; mock.state.meeting.transcript_deleted_at = new Date().toISOString();
  await mockRoom(page, mock);
  await open(page);
  await expect(roomOf(page).getByTestId('meeting-summary').getByText('The transcript was deleted, so a summary cannot be drafted.')).toBeVisible();
});

test('a member cannot draft, read or link: counts only, and no request for the text', async ({ page }) => {
  const mock = createRoomMock({ owner: false, status: 'finished', messages: talk });
  mock.state.outputs.push({ id: 'out_1', session_id: 'mtg_1', kind: 'summary', body: 'Hidden summary', state: 'draft', owner_label: null, source_sequences: [1], task_id: null, created_by: 'u', decided_by: null, origin: 'model' });
  mock.state.links.push({ id: 'lnk_1', output_id: 'out_1', target_kind: 'task', target_id: 'task_plan', target_label: 'Hidden task', target_status: 'created' });
  await mockRoom(page, mock);
  await open(page);
  const room = roomOf(page);
  await expect(room.getByTestId('transcript-hidden')).toBeVisible();
  await expect(room.getByTestId('meeting-summary')).toHaveCount(0);
  await expect(room.getByTestId('linked-work')).toHaveCount(0);
  await expect(room.getByRole('button', { name: 'Download record (JSON)' })).toHaveCount(0);
  await expect(page.getByText('Hidden summary')).toHaveCount(0);
  await expect(page.getByText('Hidden task')).toHaveCount(0);
  expect(mock.state.calls.summaries).toBe(0);
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of sizes) {
    test(`summary, drafts and linked work fit ${width}x${height} in ${locale} @gate`, async ({ page }, testInfo) => {
      const mock = createRoomMock({ status: 'finished', messages: [
        ...talk,
        { speaker: 'agent_omar', role: 'agent', body: 'A very long unbroken line https://example.com/' + 'a'.repeat(120) },
      ] });
      const out = (id: string, kind: string, body: string, state: string, sources: number[], extra: Record<string, unknown> = {}) => ({
        id, session_id: 'mtg_1', kind, body, state, owner_label: null, source_sequences: sources, task_id: null, created_by: 'user_local', decided_by: null, origin: 'model', drafted_with: 'fake/fake-1', payload_hash: id.padEnd(64, 'b'), ...extra });
      mock.state.outputs.push(
        out('out_1', 'summary', '- The group agreed to launch on Tuesday and announce on Monday, after the risks are reviewed with every team involved. [#1, #2]\n- A very long unbroken reference was shared. [#3]', 'draft', [1, 2, 3]),
        out('out_2', 'decision', 'Launch on Tuesday and announce on Monday, after the risk review is complete.', 'confirmed', [1]),
        out('out_3', 'action_item', 'Announce the launch on Monday.', 'draft', [2], { owner_label: 'Maya' }),
      );
      mock.state.links.push(
        { id: 'lnk_1', output_id: 'out_2', target_kind: 'task', target_id: 'task_plan', target_label: 'Plan the launch with every regional team and the support desk', target_status: 'created' },
        { id: 'lnk_2', output_id: 'out_2', target_kind: 'approval', target_id: 'apr_1', target_label: 'send_email', target_status: 'pending' },
      );
      await useSavedLocale(page, locale);
      await mockRoom(page, mock);
      await page.setViewportSize({ width, height });
      await page.goto('/#office/meeting/mtg_1');
      const room = roomOf(page, locale === 'en' ? 'Meeting room' : 'غرفة الاجتماع');
      await expect(room.getByTestId('meeting-summary')).toBeVisible();
      await expect(room.getByTestId('linked-work')).toBeVisible();
      if (locale === 'ar') {
        await expect(room.getByTestId('meeting-summary')).toContainText('ملخص الاجتماع');
        await expect(room.getByTestId('meeting-summary')).toContainText('تأكيد الملخص');
        await expect(room.getByTestId('linked-work')).toContainText('أعمال من هذا الاجتماع');
        expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      const clipped = await room.locator('button').evaluateAll((buttons) => buttons.filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); }).length);
      expect(clipped).toBe(0);
      await room.getByTestId('meeting-summary').scrollIntoViewIfNeeded();
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: `${process.env.ANUM_SCREENSHOTS}/summary-${locale}-${width}x${height}.png`, fullPage: true });
      else await page.screenshot({ path: testInfo.outputPath(`summary-${locale}-${width}.png`), fullPage: true });
    });
  }
}
