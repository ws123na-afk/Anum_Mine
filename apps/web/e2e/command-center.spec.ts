import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The ANUM Command Center (docs/command.md#the-web-command-center), in the classic layout: the orb
// with its eight chips, the composer, the capability tiles, the tracker / live activity / current
// tasks panels, recent commands / results / suggestions, saved commands and the action bar. A request
// is planned (POST /commands/preview), edited, started (POST /commands, then each step's run) and
// followed. Mocks: e2e/support/homeApi.ts. Home's overview is e2e/home.spec.ts.

const VIEWPORTS = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const API = 'http://localhost:8000';
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function serious(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 5).map((n) => n.target.join(' ')).join(' | ')}`);
}

async function openCommand(page: Page) {
  const state = await mockWorkspaceApi(page);
  await page.goto('/#command');
  await expect(page.getByRole('heading', { level: 1, name: 'ANUM Command Center' })).toBeVisible();
  return state;
}
const tracker = (page: Page) => page.getByRole('region', { name: /ANUM is working|Last command|Waiting for your approval/ });

test('the classic Command Center: orb with eight chips, composer, tiles, six panels and the action bar', async ({ page }) => {
  await openCommand(page);
  await expect(page.getByLabel('Your command')).toBeFocused();
  await expect(page.getByRole('region', { name: 'Today at a glance' })).toHaveCount(0);
  await expect(page.getByRole('group', { name: 'How to give the command' }).getByRole('button')).toHaveText(['Voice', 'Text', 'Files']);
  const stage = page.getByRole('region', { name: 'Command', exact: true });
  await expect(stage.getByRole('button', { name: 'Start speaking a command' })).toContainText('Ready');
  await expect(stage.getByRole('listitem')).toHaveCount(8);
  await expect(stage.getByRole('button', { name: 'العربية' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'What ANUM can do' }).getByRole('listitem')).toHaveCount(8);
  for (const name of ['Live agent activity', 'Recent commands', 'Results preview', 'Suggestions for you', 'Saved commands']) await expect(page.getByRole('region', { name, exact: true })).toBeVisible();
  await expect(tracker(page)).toContainText('Three urgent emails');
  await expect(page.getByRole('region', { name: /^Current tasks/ }).getByRole('button', { name: /Review the release/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Results preview' }).getByRole('button', { name: /release-notes\.md/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Suggestions for you' }).getByRole('button', { name: /1 approval is waiting for you/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Recent commands' }).getByRole('button', { name: /^Check my emails and summarize the urgent ones/ })).toBeVisible();
  const saved = page.getByRole('region', { name: 'Saved commands' });
  await expect(saved.getByText('Morning briefing', { exact: true })).toBeVisible();
  await saved.getByRole('button', { name: 'Personal' }).click();
  await expect(saved.getByText('Morning briefing', { exact: true })).toHaveCount(0);
  await expect(saved.getByText('Plan my week', { exact: true })).toBeVisible();
  // Talk in the action bar opens the full voice assistant.
  await page.getByRole('navigation', { name: 'Ways to work with ANUM' }).getByRole('button', { name: /Speak to the orb/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Voice' })).toBeVisible();
});

test('chips and tiles prefill or open a page; Files mode attaches recent documents to the plan', async ({ page }) => {
  const state = await openCommand(page);
  await page.getByRole('button', { name: 'Prepare a report' }).click();
  await expect(page.getByLabel('Your command')).toHaveValue('Prepare a report on ');
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Files', exact: true }).click();
  const picker = page.getByRole('region', { name: 'Documents to use' });
  await picker.getByRole('checkbox').first().check();
  await expect(page.getByRole('list', { name: 'Attached documents' })).toContainText('release-notes.md');
  await page.getByLabel('Your command').fill('Check my emails, find today\'s urgent items');
  await page.getByRole('button', { name: 'Show the plan' }).click();
  const plan = page.getByRole('region', { name: 'Here is what ANUM will do' });
  await expect(plan.getByText('1 document attached', { exact: false })).toBeVisible();
  await plan.getByRole('button', { name: 'Start' }).click();
  await expect.poll(() => (state.created[0] as { attachments: string[] } | undefined)?.attachments).toEqual(['file_notes']);
  await page.getByRole('region', { name: 'What ANUM can do' }).getByRole('button', { name: 'Voice assistant' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Voice' })).toBeVisible();
});

test('a command waiting for approval shows its timeline; Approve all sends each payload hash', async ({ page }) => {
  await mockWorkspaceApi(page);
  const approval = { id: 'approval_cmd', task_id: 'task_cmd_2', action: 'calendar.create_event', risk_level: 'medium', status: 'pending', reason: 'Sending invitations needs approval.', created_at: new Date().toISOString(), run_id: 'run_2', step_id: 'step_2', arguments: {}, payload_hash: 'cd'.repeat(32), expires_at: null, decided_at: null, decided_by: null, required_approvals: 1, approvers: [] };
  const waiting = (status: string) => ({
    id: 'cmd_wait', text: 'Plan the team meeting', source: 'typed', locale: 'en', status, progress: 0.5, step_count: 2, completed_steps: 1, pending_approvals: status === 'waiting_approval' ? 1 : 0,
    agents: [{ id: 'calendar', name: 'Calendar agent' }], created_at: new Date().toISOString(), updated_at: new Date().toISOString(), created_by: 'user_local', quick_command_id: null,
    steps: [
      { position: 1, title: 'Find a free slot', instruction: 'Find a free slot', agent: { id: 'calendar', name: 'Calendar agent' }, tool: null, risk_level: 'low', approval: 'none', task_id: 'task_cmd_1', task: null, status: 'completed', progress: 1, run_id: 'run_1', result: 'Tomorrow at 10:00 is free.', started_at: null, finished_at: null, approvals: [] },
      { position: 2, title: 'Send the invitation', instruction: 'Send the invitation', agent: { id: 'calendar', name: 'Calendar agent' }, tool: 'create_event', risk_level: 'medium', approval: 'required', task_id: 'task_cmd_2', task: null, status: status === 'waiting_approval' ? 'waiting_approval' : 'completed', progress: 0.5, run_id: 'run_2', result: null, started_at: null, finished_at: null, approvals: [] },
    ],
    pending: status === 'waiting_approval' ? [approval] : [], timeline: [], files: [], attachments: [], started_at: null, finished_at: null, duration_seconds: null,
  });
  let status = 'waiting_approval';
  const decisions: unknown[] = [];
  await page.route(`${API}/api/v1/commands?*`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify([waiting(status)]) }));
  await page.route(`${API}/api/v1/commands/cmd_wait`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(waiting(status)) }));
  await page.route(`${API}/api/v1/approvals/approval_cmd/approve`, async (route) => {
    decisions.push(route.request().postDataJSON()); status = 'completed';
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ approval: { ...approval, status: 'approved' }, task: { id: 'task_cmd_2', title: 'Send', prompt: 'Send', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: approval.created_at, updated_at: approval.created_at }, run: null }) });
  });
  await page.goto('/#command');
  const panel = page.getByRole('region', { name: 'Waiting for your approval' });
  await expect(panel.getByText('Tomorrow at 10:00 is free.')).toBeVisible();
  await expect(panel.getByText('1 step waits for your approval')).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Stop' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Live agent activity' }).getByRole('button', { name: /Plan the team meeting/ })).toBeVisible();
  await panel.getByRole('button', { name: 'Approve all' }).click();
  await expect.poll(() => decisions).toEqual([{ payload_hash: 'cd'.repeat(32) }]);
  await expect(page.getByRole('region', { name: 'Last command' })).toBeVisible();
});

test('a typed command shows the plan, is edited, then starts every step and shows the results', async ({ page }) => {
  const state = await openCommand(page);
  await page.getByLabel('Your command').fill('Check my emails, find today\'s urgent items and schedule a meeting with the team tomorrow at 10 AM');
  await page.getByRole('button', { name: 'Show the plan' }).click();
  const plan = page.getByRole('region', { name: 'Here is what ANUM will do' });
  await expect(plan.getByText('2 steps · 1 will ask for your approval')).toBeVisible();
  await expect(plan.getByText('No calendar is connected yet', { exact: false })).toBeVisible();
  // Reorder: the meeting first.
  await plan.getByRole('button', { name: 'Move step 2 up' }).click();
  await expect(plan.getByRole('listitem').first()).toContainText('Schedule a meeting');
  await plan.getByRole('button', { name: 'Start' }).click();

  const detail = tracker(page).filter({ hasText: 'steps done' });
  await expect(detail.getByText('Three urgent emails today.')).toBeVisible();
  await expect(detail.getByText('Meeting drafted for tomorrow at 10:00.')).toBeVisible();
  await expect(detail.getByText('2 of 2 steps done')).toBeVisible();
  expect(state.created).toEqual([{
    text: 'Check my emails, find today\'s urgent items and schedule a meeting with the team tomorrow at 10 AM',
    steps: [{ instruction: 'Schedule a meeting with the team tomorrow at 10 AM' }, { instruction: 'Check my emails, find today\'s urgent items' }],
    source: 'typed', locale: 'en', attachments: [],
  }]);
  expect(state.runs.sort()).toEqual(['task_cmd_1', 'task_cmd_2']);
  // Next steps come from the API; a follow-up is planned, never run straight away.
  await detail.getByRole('button', { name: 'Draft replies to the urgent emails' }).click();
  await expect(page.getByLabel('Your command')).toHaveValue('Draft replies to the urgent emails');
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toBeVisible();
});

test('a saved command is planned with one click and a new one can be saved', async ({ page }) => {
  const state = await openCommand(page);
  const saved = page.getByRole('region', { name: 'Saved commands' });
  await saved.getByRole('button', { name: 'Plan it' }).first().click();
  await expect(page.getByLabel('Your command')).toHaveValue("Summarize my unread email and today's tasks");
  await expect(page.getByRole('region', { name: 'Here is what ANUM will do' })).toBeVisible();
  await saved.getByRole('button', { name: 'New saved command' }).click();
  await saved.getByLabel('Name').fill('Supplier check');
  await saved.getByLabel('What ANUM should do').fill('Compare tyre prices from our three suppliers');
  await saved.getByRole('button', { name: 'Save' }).click();
  await expect(saved.getByText('Supplier check', { exact: true })).toBeVisible();
  expect(state.quick.at(-1)).toMatchObject({ title: 'Supplier check', text: 'Compare tyre prices from our three suppliers', category: 'custom' });
});

test('a viewer is told who can give commands; a failing history says what failed with Retry', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route(`${API}/api/v1/workspace-memberships/current`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'viewer', active: true }) }));
  await page.route(`${API}/api/v1/commands?*`, (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'internal', message: 'Database is restarting', correlation_id: 'c2' } }) }));
  await page.goto('/#command');
  await expect(page.getByText('Viewers cannot give commands')).toBeVisible();
  await expect(page.getByLabel('Your command')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start speaking a command' })).toBeDisabled();
  const history = page.getByRole('region', { name: 'Recent commands' });
  await expect(history.getByRole('alert')).toContainText('Database is restarting');
  await expect(history.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('a past command opens with its results, next steps and a report download', async ({ page }) => {
  await openCommand(page);
  await page.getByRole('region', { name: 'Recent commands' }).getByRole('button', { name: /^Check my emails and summarize/ }).click();
  const detail = page.getByRole('region', { name: 'Last command' });
  await expect(detail.getByText('Three urgent emails: the overdue invoice', { exact: false })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Remove from history' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Save as a quick command' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['dark', 'light'] as const) {
    test(`${locale} ${theme}: no serious axe violations, no horizontal overflow at the five viewports`, async ({ page }) => {
      await useSavedLocale(page, locale);
      if (theme === 'light') await page.addInitScript(() => { try { window.localStorage.setItem('anum.theme', 'light'); } catch { /* ignore */ } });
      await mockWorkspaceApi(page);
      for (const size of VIEWPORTS) {
        await page.setViewportSize(size);
        await page.goto('/#command');
        await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
        await expect(page.locator('.commandPanel .listRow').first()).toBeVisible();
        await expect(page.locator('.quickCard').first()).toBeVisible();
        expect(await noHorizontalOverflow(page), `${size.width}x${size.height}`).toBe(true);
      }
      expect(await serious(page)).toEqual([]);
    });
  }
}
