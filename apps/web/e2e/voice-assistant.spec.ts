import { expect, test, type Page } from '@playwright/test';

const now = '2026-10-06T09:00:00.000Z';
const workspace = { tasks_total: 2, running: 1, waiting_approval: 0, pending_approvals: 1 };

async function mockVoiceApi(page: Page) {
  const created: string[] = [];
  let sequence = 0;
  const texts = new Map<string, string>();
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/voice/sessions') {
      return json({ id: 'voice_e2e', locale: 'en-US', retention: 'session', status: 'active', created_at: now, updated_at: now, expires_at: null });
    }
    if (path.endsWith('/transcript')) {
      const body = route.request().postDataJSON() as { text: string };
      const id = `transcript_${sequence++}`;
      texts.set(id, body.text);
      return json({ id, session_id: 'voice_e2e', role: 'user', text: body.text, is_final: true, client_sequence: sequence, created_at: now });
    }
    if (path.endsWith('/ask')) {
      const { transcript_segment_id: id } = route.request().postDataJSON() as { transcript_segment_id: string };
      const text = texts.get(id) ?? '';
      const reply = text.startsWith('Create a task')
        ? { intent: 'create_task', risk_tier: 'confirm', reply: 'Create the task "draft the weekly report"? Confirm on screen.', proposed_task: 'draft the weekly report' }
        : text.startsWith('Approve')
          ? { intent: 'visual_only', risk_tier: 'visual_only', reply: 'For your safety, approvals are never done by voice.', proposed_task: null }
          : { intent: 'status', risk_tier: 'read', reply: 'You have 2 tasks. 1 running.', proposed_task: null };
      return json({ ...reply, workspace, assistant_segment: { id: `assistant_${id}`, session_id: 'voice_e2e', role: 'assistant', text: reply.reply, is_final: true, client_sequence: 0, created_at: now } });
    }
    if (path.endsWith('/commands')) {
      const { transcript_segment_id: id } = route.request().postDataJSON() as { transcript_segment_id: string };
      created.push(texts.get(id) ?? '');
      return json({ session: {}, transcript_segment_id: id, task: { id: 'task_voice', title: texts.get(id), prompt: texts.get(id), status: 'created', tenant_id: 't', workspace_id: 'w', created_at: now, updated_at: now } });
    }
    // Everything else behaves as if the API were offline; the shell falls back to demo data.
    return route.abort();
  });
  return created;
}

async function ask(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Your question' }).fill(text);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
}

test('voice assistant answers, asks before creating tasks and refuses voice approvals', async ({ page }) => {
  const created = await mockVoiceApi(page);
  await page.goto('/#voice');
  await expect(page.getByRole('region', { name: 'ANUM voice assistant' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hold to talk' })).toBeVisible();

  await ask(page, "What's the status of my workspace?");
  await expect(page.getByText('You have 2 tasks. 1 running.')).toBeVisible();
  await expect(page.getByText('Answer only')).toBeVisible();

  await ask(page, 'Create a task: draft the weekly report');
  await expect(page.getByText('Needs your confirmation')).toBeVisible();
  expect(created).toEqual([]);
  await page.getByRole('button', { name: 'Create task' }).click();
  await expect(page.getByText('Task created', { exact: true })).toBeVisible();
  expect(created).toEqual(['draft the weekly report']);

  await ask(page, 'Approve all pending actions');
  await expect(page.getByText('On-screen only')).toBeVisible();
  await page.getByRole('button', { name: 'Open Approvals' }).click();
  await expect(page.getByRole('heading', { name: 'Approvals', level: 1 })).toBeVisible();

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
