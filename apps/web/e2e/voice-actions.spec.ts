import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

// The voice assistant acts (docs/voice.md#actions): a spoken request for work streams an
// `action` card (task, agent, where it stands) with links to the task and to Approvals.
const now = '2026-10-12T09:00:00.000Z';
const workspace = { tasks_total: 3, running: 0, waiting_approval: 1, pending_approvals: 1 };
const task = {
  id: 'task_voice_1', title: "Follow up with yesterday's leads", prompt: "follow up with yesterday's leads", status: 'waiting_approval',
  tenant_id: 'tenant_local', workspace_id: 'workspace_local', created_at: now, updated_at: now, created_by: 'user_local', agent_id: 'agent_sales',
};
const running = {
  kind: 'assign', state: 'running', title: task.title, task_id: task.id, task_status: 'running', agent: { id: 'agent_sales', name: 'Sales agent' },
  command_id: null, steps: 0, approvals_pending: 0, approval: null, summary: '', candidates: [], error_code: null,
};
const waiting = {
  ...running, state: 'approval', task_status: 'waiting_approval', approvals_pending: 1,
  approval: { id: 'approval_voice_1', action: 'external.action', risk_level: 'high', status: 'pending', reason: 'High-risk tools require approval.', payload_hash: 'a'.repeat(64), arguments: {}, target: null, expires_at: null, can_decide: true },
};
const reply = `Created task "${task.title}" and assigned it to Sales agent. It needs an approval first, so I've sent it to Approvals.`;
const notFound = {
  kind: 'cancel_task', state: 'ambiguous', title: null, task_id: null, task_status: null, agent: null, command_id: null, steps: 0, approvals_pending: 0,
  approval: null, summary: '2 tasks match: Weekly report and Weekly report for sales. Which one?', candidates: ['Weekly report', 'Weekly report for sales'], error_code: 'ambiguous',
};

async function mockVoiceApi(page: Page) {
  let sequence = 0;
  const texts = new Map<string, string>();
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/voice/sessions') {
      return json({ id: 'voice_actions_e2e', locale: 'en-US', retention: 'session', assistant_name: 'Layla', status: 'active', created_at: now, updated_at: now, expires_at: null });
    }
    if (path.endsWith('/transcript')) {
      const body = route.request().postDataJSON() as { text: string };
      const id = `transcript_${sequence++}`;
      texts.set(id, body.text);
      return json({ id, session_id: 'voice_actions_e2e', role: 'user', text: body.text, is_final: true, client_sequence: sequence, created_at: now });
    }
    if (path.endsWith('/ask/stream')) {
      const { transcript_segment_id: id } = route.request().postDataJSON() as { transcript_segment_id: string };
      const text = texts.get(id) ?? '';
      const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      const [cards, said] = text.startsWith('Ask the Sales agent')
        ? [[running, waiting], reply]
        : [[notFound], notFound.summary];
      const kind = text.startsWith('Ask') ? 'assign' : 'cancel_task';
      const body = frame('meta', { intent: 'action', risk_tier: 'action', proposed_task: null, workspace, vision: false, action_kind: kind })
        + cards.map((card) => frame('action', { card })).join('')
        + frame('delta', { text: said })
        + frame('done', { assistant_segment: { id: `assistant_${id}`, session_id: 'voice_actions_e2e', role: 'assistant', text: said, is_final: true, client_sequence: 0, created_at: now } });
      return route.fulfill({ contentType: 'text/event-stream', body });
    }
    if (path === `/api/v1/tasks/${task.id}`) return json(task);
    // Everything else behaves as if the API were offline; the shell falls back to demo data.
    return route.abort();
  });
}

async function ask(page: Page, text: string) {
  // By class, so the same helper works in English and Arabic.
  await page.locator('.voiceDraft textarea').fill(text);
  await page.locator('button.voiceAsk').click();
}

async function openVoiceIn(page: Page, language: 'en' | 'ar') {
  await mockVoiceApi(page);
  await page.addInitScript(() => { localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', 'off'); });
  await page.goto('/#voice');
  await expect(page.locator('html')).toHaveAttribute('dir', language === 'ar' ? 'rtl' : 'ltr');
}

async function openVoice(page: Page) {
  await mockVoiceApi(page);
  await page.addInitScript(() => { localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', 'off'); });
  await page.goto('/#voice');
  await expect(page.getByRole('region', { name: 'Layla voice assistant' })).toBeVisible();
}

test('asking an agent by voice shows the task, its agent and the approval it waits for', async ({ page }) => {
  await openVoice(page);
  await ask(page, "Ask the Sales agent to follow up with yesterday's leads");

  const card = page.getByRole('region', { name: 'What Layla did' });
  await expect(page.getByText(reply)).toBeVisible();
  await expect(card.getByRole('heading', { name: 'Work handed over' })).toBeVisible();
  await expect(card.getByText('Waiting for approval')).toBeVisible();
  await expect(card.getByText("Follow up with yesterday's leads")).toBeVisible();
  await expect(card.getByText('Agent: Sales agent')).toBeVisible();
  await expect(card.getByText('1 approval waiting')).toBeVisible();
  await expect(page.getByText('Done through ANUM')).toBeVisible();
  // No approve button here: approvals are decided in Approvals.
  await expect(card.getByRole('button', { name: /^Approve/ })).toHaveCount(0);

  await card.getByRole('button', { name: 'Open Approvals' }).click();
  await expect(page.getByRole('heading', { name: 'Approvals', level: 1 })).toBeVisible();
});

test('the action card opens its task', async ({ page }) => {
  await openVoice(page);
  await ask(page, "Ask the Sales agent to follow up with yesterday's leads");
  await page.getByRole('region', { name: 'What Layla did' }).getByRole('button', { name: 'Open task' }).click();
  await expect(page.getByRole('heading', { name: 'Tasks', level: 1 })).toBeVisible();
});

for (const language of ['en', 'ar'] as const) {
  test(`the action card has no serious accessibility problems (${language})`, async ({ page }) => {
    if (language === 'ar') await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
    await openVoiceIn(page, language);
    await ask(page, "Ask the Sales agent to follow up with yesterday's leads");
    const card = page.locator('.voiceActionCard');
    await expect(card).toBeVisible();
    const results = await new AxeBuilder({ page }).include('.voiceActionCard').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    const serious = results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id);
    expect(serious).toEqual([]);
  });
}

test('an ambiguous request lists the matches and links nowhere', async ({ page }) => {
  await openVoice(page);
  await ask(page, 'Cancel the weekly task');
  const card = page.getByRole('region', { name: 'What Layla did' });
  await expect(card.getByText('Which one?')).toBeVisible();
  await expect(card.getByRole('list', { name: 'Matches' }).getByRole('listitem')).toHaveCount(2);
  await expect(card.getByRole('button')).toHaveCount(0);
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`the action card fits the conversation panel at ${size.width}x${size.height}`, async ({ page }) => {
    await page.setViewportSize(size);
    await openVoice(page);
    await ask(page, "Ask the Sales agent to follow up with yesterday's leads");
    const card = page.getByRole('region', { name: 'What Layla did' });
    await expect(card).toBeVisible();
    await card.scrollIntoViewIfNeeded();
    const box = await card.boundingBox();
    const panel = await page.locator('.voiceLog').boundingBox();
    expect(box && panel).toBeTruthy();
    // Inside the panel's width, and the page never scrolls sideways.
    expect(box!.x).toBeGreaterThanOrEqual(panel!.x - 1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(panel!.x + panel!.width + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const name of ['Open task', 'Open Approvals']) {
      const button = card.getByRole('button', { name });
      await expect(button).toBeVisible();
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    await page.screenshot({ path: test.info().outputPath(`voice-action-${size.width}x${size.height}.png`) });
  });
}
