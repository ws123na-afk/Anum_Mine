import { expect, test, type Page } from '@playwright/test';

const now = '2026-10-11T09:00:00.000Z';
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

type Turn = { id: string; conversation_id: string; role: 'user' | 'assistant'; content: string; status: string; created_at: string };

async function mockChatApi(page: Page, replies: ((question: string) => { text: string; fail?: boolean })) {
  const conversations: { id: string; title: string; created_at: string; updated_at: string }[] = [
    { id: 'conv_old', title: 'Earlier chat', created_at: now, updated_at: now },
  ];
  const turns = new Map<string, Turn[]>([['conv_old', [
    { id: 't_old_q', conversation_id: 'conv_old', role: 'user', content: 'Old question', status: 'complete', created_at: now },
    { id: 't_old_a', conversation_id: 'conv_old', role: 'assistant', content: 'Old **answer**', status: 'complete', created_at: now },
  ]]]);
  let next = 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/conversations' && request.method() === 'GET') return json(conversations);
    if (path === '/api/v1/conversations' && request.method() === 'POST') {
      const created = { id: `conv_${++next}`, title: '', created_at: now, updated_at: now };
      conversations.unshift(created);
      turns.set(created.id, []);
      return json(created, 201);
    }
    const ask = /^\/api\/v1\/conversations\/([^/]+)\/ask$/.exec(path);
    if (ask) {
      const id = ask[1];
      const body = request.postDataJSON() as { content?: string; retry_turn_id?: string };
      const list = turns.get(id) ?? [];
      const user = body.retry_turn_id
        ? list.find((t) => t.id === body.retry_turn_id)!
        : { id: `t_${++next}`, conversation_id: id, role: 'user' as const, content: body.content ?? '', status: 'complete', created_at: now };
      if (!body.retry_turn_id) list.push(user);
      const conversation = conversations.find((c) => c.id === id)!;
      if (!conversation.title) conversation.title = user.content;
      const reply = replies(user.content);
      const pieces = reply.text.split(/(?<= )/);
      const assistant = { id: `t_${++next}`, conversation_id: id, role: 'assistant' as const, content: reply.text, status: reply.fail ? 'failed' : 'complete', created_at: now };
      list.push(assistant);
      turns.set(id, list);
      const stream = frame('turn', { user_turn: user }) + pieces.map((text) => frame('delta', { text })).join('')
        + (reply.fail ? frame('error', { code: 'model_unavailable', message: 'The model did not answer. Try again in a moment.', assistant_turn: assistant }) : frame('done', { assistant_turn: assistant }));
      return route.fulfill({ contentType: 'text/event-stream', body: stream });
    }
    const one = /^\/api\/v1\/conversations\/([^/]+)$/.exec(path);
    if (one) {
      const conversation = conversations.find((c) => c.id === one[1]);
      return conversation ? json({ ...conversation, turns: turns.get(conversation.id) ?? [] }) : json({ detail: 'Conversation not found' }, 404);
    }
    // Everything else behaves as if the API were offline.
    return route.abort();
  });
}

test('the assistant streams answers, remembers the conversation and renders replies safely', async ({ page }) => {
  let attempts = 0;
  await mockChatApi(page, (question) => {
    if (question.startsWith('Flaky')) return ++attempts === 1 ? { text: 'Half an', fail: true } : { text: 'Recovered answer.' };
    if (question.startsWith('Inject')) return { text: 'Here: <img src=x onerror="window.__pwned=1"> and **bold**.' };
    return { text: `You asked: ${question}. Here is a list:\n\n- first point\n- second point` };
  });
  await page.goto('/#assistant');
  // AI Chat opens the most recent conversation.
  await expect(page.getByRole('heading', { name: 'AI Chat', level: 1 })).toBeVisible();
  await expect(page.getByText('Old question')).toBeVisible();
  await expect(page.locator('.chatLog strong', { hasText: 'answer' })).toBeVisible();

  await page.getByRole('button', { name: 'New' }).click();
  const box = page.getByRole('textbox', { name: 'Message the assistant' });
  await box.fill('What is ANUM?');
  await box.press('Enter');
  await expect(page.getByText('You asked: What is ANUM?. Here is a list:')).toBeVisible();
  await expect(page.locator('.chatLog li', { hasText: 'second point' })).toBeVisible();
  await expect(page.locator('.chatListItem[aria-current="true"]')).toContainText('What is ANUM?');

  await box.fill('Inject markup');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('<img src=x onerror="window.__pwned=1">', { exact: false })).toBeVisible();
  expect(await page.locator('.chatLog img').count()).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();

  await box.fill('Flaky question');
  await box.press('Enter');
  await expect(page.getByText('The model did not answer. Try again in a moment.')).toBeVisible();
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByText('Recovered answer.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0);
  // Retry asked the stored question again instead of adding a second copy.
  await expect(page.locator('.chatTurn.user', { hasText: 'Flaky question' })).toHaveCount(1);

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

test('every chat state is finished: list error, budget refusal, Stop and long history', async ({ page }) => {
  let listCalls = 0;
  const long = Array.from({ length: 40 }, (_, i) => ({
    id: `t${i}`, conversation_id: 'conv_long', role: i % 2 ? 'assistant' : 'user', content: i % 2 ? `Answer number ${i}. `.repeat(8) : `Question ${i}`, status: 'complete', created_at: now,
  }));
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/conversations' && request.method() === 'GET') {
      // The first load fails; "Try again" succeeds.
      return ++listCalls === 1 ? json(envelope('internal_error', 'Database unavailable'), 503) : json([{ id: 'conv_long', title: 'Long history', created_at: now, updated_at: now }]);
    }
    if (path === '/api/v1/conversations/conv_long') return json({ id: 'conv_long', title: 'Long history', created_at: now, updated_at: now, turns: long });
    if (path === '/api/v1/conversations/conv_long/ask') {
      const body = request.postDataJSON() as { content?: string };
      if (body.content === 'Spend more') return json(envelope('model_budget_exceeded', 'This workspace has used its monthly model budget (tokens). It resets on 2026-11-01 (UTC).'), 402);
      // A slow model: nothing arrives before the user presses Stop.
      await new Promise((resolve) => setTimeout(resolve, 3000));
      return route.abort().catch(() => undefined);
    }
    return route.abort();
  });
  await page.goto('/#assistant');
  await expect(page.getByRole('alert').filter({ hasText: 'Conversations could not be loaded' })).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();

  // Long history: opens scrolled to the newest message; reading upward offers a way back.
  await expect(page.getByText('Question 38', { exact: true })).toBeInViewport();
  await page.locator('.chatLog').evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
  await page.getByRole('button', { name: 'Jump to latest' }).click();
  await expect(page.getByText('Question 38', { exact: true })).toBeInViewport();

  const box = page.getByRole('textbox', { name: 'Message the assistant' });
  await box.fill('A slow question');
  await box.press('Enter');
  await page.getByRole('button', { name: 'Stop' }).click();
  await expect(page.getByText('Stopped before any answer.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible();

  await box.fill('Spend more');
  await box.press('Enter');
  await expect(page.getByText('It resets on 2026-11-01 (UTC).')).toBeVisible();
  await expect(page.getByText('Not sent: the monthly model budget is used up.')).toBeVisible();

  await page.getByRole('button', { name: 'Open Budgets' }).click();
  await expect(page.getByRole('heading', { name: 'Budgets', level: 1 })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
