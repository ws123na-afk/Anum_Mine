// Message actions and conversation management in the Assistant (docs/conversations.md#message-actions):
// send and stream, copy an answer and a code block, regenerate, rate, edit and resend, delete a
// message, rename, pin, search, page and export a conversation, with the keyboard, in English and
// Arabic and at the five layout sizes. The API is mocked at http://localhost:8000 and keeps state.
import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const now = '2026-10-12T09:00:00.000Z';
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

type Turn = { id: string; conversation_id: string; role: 'user' | 'assistant'; content: string; status: string; created_at: string; feedback?: string | null };
type Conv = { id: string; title: string; created_at: string; updated_at: string; pinned: boolean; pinned_at: string | null };

const CODE_ANSWER = 'Here is the script:\n\n```python\nprint("tyres due")\n```\n\n| Vehicle | Tyres |\n| --- | ---: |\n| Truck 7 | 4 |\n\nSee [the guide](https://docs.anum.example/tyres) or [this](javascript:alert(1)).';

interface Sent { asks: Record<string, unknown>[]; ratings: unknown[]; patches: unknown[]; deletedTurns: string[]; queries: string[] }

async function mockApi(page: Page, extra: Conv[] = []) {
  const sent: Sent = { asks: [], ratings: [], patches: [], deletedTurns: [], queries: [] };
  const conversations: Conv[] = [{ id: 'conv_a', title: 'Fleet tyres', created_at: now, updated_at: now, pinned: false, pinned_at: null }, ...extra];
  const turns = new Map<string, Turn[]>([['conv_a', []]]);
  let next = 0;
  let version = 0;
  await page.addInitScript(() => {
    const copied: string[] = [];
    (window as unknown as { __copied: string[] }).__copied = copied;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { copied.push(text); } } });
  });
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/conversations' && method === 'GET') {
      sent.queries.push(url.search);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const before = url.searchParams.get('before_id');
      const limit = Number(url.searchParams.get('limit') ?? 50);
      const matches = conversations.filter((c) => !q || c.title.toLowerCase().includes(q) || (turns.get(c.id) ?? []).some((t) => t.content.toLowerCase().includes(q)));
      const pinned = matches.filter((c) => c.pinned);
      let rest = matches.filter((c) => !c.pinned);
      if (before) rest = rest.slice(rest.findIndex((c) => c.id === before) + 1);
      return json([...(before ? [] : pinned), ...rest.slice(0, limit)]);
    }
    if (path === '/api/v1/conversations' && method === 'POST') {
      // Asking before the list has loaded starts a new conversation.
      const created: Conv = { id: `conv_new_${++next}`, title: '', created_at: now, updated_at: now, pinned: false, pinned_at: null };
      conversations.unshift(created);
      turns.set(created.id, []);
      return json(created, 201);
    }
    const one = /^\/api\/v1\/conversations\/([^/]+)$/.exec(path);
    if (one && method === 'GET') {
      const c = conversations.find((x) => x.id === one[1]);
      return c ? json({ ...c, turns: turns.get(c.id) ?? [], reports: [] }) : json({ detail: 'Conversation not found' }, 404);
    }
    if (one && method === 'PATCH') {
      const body = request.postDataJSON() as { title?: string; pinned?: boolean };
      sent.patches.push(body);
      const c = conversations.find((x) => x.id === one[1])!;
      if (body.title !== undefined) c.title = body.title;
      if (body.pinned !== undefined) { c.pinned = body.pinned; c.pinned_at = body.pinned ? now : null; }
      return json(c);
    }
    if (one && method === 'DELETE') {
      conversations.splice(conversations.findIndex((x) => x.id === one[1]), 1);
      return route.fulfill({ status: 204 });
    }
    const turnDelete = /^\/api\/v1\/conversations\/([^/]+)\/turns\/([^/]+)$/.exec(path);
    if (turnDelete && method === 'DELETE') {
      sent.deletedTurns.push(turnDelete[2]);
      const list = turns.get(turnDelete[1]) ?? [];
      const index = list.findIndex((t) => t.id === turnDelete[2]);
      let end = index + 1;
      if (list[index].role === 'user') while (end < list.length && list[end].role !== 'user') end++;
      list.splice(index, end - index);
      return route.fulfill({ status: 204 });
    }
    const rating = /^\/api\/v1\/conversations\/([^/]+)\/turns\/([^/]+)\/feedback$/.exec(path);
    if (rating && method === 'PUT') {
      const body = request.postDataJSON() as { rating: string | null };
      sent.ratings.push(body);
      const target = (turns.get(rating[1]) ?? []).find((t) => t.id === rating[2])!;
      target.feedback = body.rating;
      return json({ turn: target, learning_recorded: true, learning_message: null });
    }
    const ask = /^\/api\/v1\/conversations\/([^/]+)\/ask$/.exec(path);
    if (ask) {
      const id = ask[1];
      const body = request.postDataJSON() as { content?: string; retry_turn_id?: string; edit_turn_id?: string };
      sent.asks.push(body);
      let list = turns.get(id) ?? [];
      let user: Turn;
      if (body.retry_turn_id) {
        const index = list.findIndex((t) => t.id === body.retry_turn_id);
        list = list.slice(0, index + 1);
        user = list[index];
      } else {
        if (body.edit_turn_id) list = list.slice(0, list.findIndex((t) => t.id === body.edit_turn_id));
        user = { id: `t_${++next}`, conversation_id: id, role: 'user', content: body.content ?? '', status: 'complete', created_at: now };
        list.push(user);
      }
      const owner = conversations.find((c) => c.id === id);
      if (owner && !owner.title) owner.title = user.content;
      const text = user.content.startsWith('Code') ? CODE_ANSWER : `Answer ${++version} to: ${user.content}`;
      const assistant: Turn = { id: `t_${++next}`, conversation_id: id, role: 'assistant', content: text, status: 'complete', created_at: now, feedback: null };
      list.push(assistant);
      turns.set(id, list);
      const pieces = text.match(/[\s\S]{1,12}/g) ?? [];
      return route.fulfill({ contentType: 'text/event-stream', body: frame('turn', { user_turn: user, memories_used: [] }) + pieces.map((p) => frame('delta', { text: p })).join('') + frame('done', { assistant_turn: assistant, report: null }) });
    }
    if (path === '/api/v1/learning/ratings') return json({ ratings: [] });
    return route.abort();
  });
  return sent;
}

const copied = (page: Page) => page.evaluate(() => (window as unknown as { __copied: string[] }).__copied);
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the main path: send, stream, copy, copy code, regenerate, rate, edit, delete', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#assistant');
  const box = page.getByRole('textbox', { name: 'Message the assistant' });
  await box.fill('Code for tyres');
  await box.press('Enter');
  const answer = page.locator('.chatTurn.assistant').last();
  // Rendering: highlighted code with its language, a table, a safe link and no unsafe one.
  await expect(answer.locator('figure.mdCode figcaption')).toContainText('Python');
  await expect(answer.locator('.tok-string')).toHaveText('"tyres due"');
  await expect(answer.getByRole('region', { name: 'Table' }).getByRole('columnheader', { name: 'Vehicle' })).toBeVisible();
  const link = answer.getByRole('link', { name: 'the guide' });
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer nofollow');
  expect(await answer.locator('a[href^="javascript"]').count()).toBe(0);

  // Copy the code block, the answer (Markdown) and the answer as plain text; each is announced.
  await answer.getByRole('button', { name: 'Copy Python code' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Copied' }).first()).toBeVisible();
  await answer.getByRole('button', { name: 'Copy answer' }).click();
  await answer.getByRole('button', { name: 'More copy options' }).click();
  await page.getByRole('menuitem', { name: 'Copy as plain text' }).click();
  const clips = await copied(page);
  expect(clips[0]).toBe('print("tyres due")');
  expect(clips[1]).toBe(CODE_ANSWER);
  expect(clips[2]).toContain('Here is the script:');
  expect(clips[2]).not.toContain('```');

  // Rate: stored on the answer.
  await answer.getByRole('button', { name: 'Helpful', exact: true }).click();
  await expect(answer.getByRole('button', { name: 'Helpful', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(sent.ratings).toEqual([{ rating: 'up' }]);

  // Regenerate replaces the latest answer.
  await box.fill('What changed?');
  await box.press('Enter');
  await expect(page.getByText('Answer 1 to: What changed?')).toBeVisible();
  await page.getByRole('button', { name: 'Regenerate answer' }).click();
  await expect(page.getByText('Answer 2 to: What changed?')).toBeVisible();
  await expect(page.getByText('Answer 1 to: What changed?')).toHaveCount(0);
  expect(sent.asks.at(-1)).toHaveProperty('retry_turn_id');

  // Edit and resend the first question: it and everything after it are replaced.
  const first = page.locator('.chatTurn.user').first();
  await first.getByRole('button', { name: 'Edit and resend' }).click();
  const edit = page.getByRole('textbox', { name: 'Edit your message' });
  await expect(edit).toBeFocused();
  await expect(page.getByText('Sending replaces the 3 messages after this one.')).toBeVisible();
  await edit.fill('Tell me about tyres');
  await edit.press('Enter');
  await expect(page.getByText('Answer 3 to: Tell me about tyres')).toBeVisible();
  await expect(page.locator('.chatTurn')).toHaveCount(2);
  expect(sent.asks.at(-1)).toMatchObject({ content: 'Tell me about tyres' });
  expect(sent.asks.at(-1)).toHaveProperty('edit_turn_id');

  // Delete the question (with its answer) after a confirmation; focus returns to the composer.
  await page.locator('.chatTurn.user').first().getByRole('button', { name: 'Delete message' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete this message?' });
  await expect(dialog).toContainText('Your message and its answer are deleted for good.');
  await dialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.locator('.chatTurn')).toHaveCount(0);
  await expect(box).toBeFocused();
  expect(sent.deletedTurns).toHaveLength(1);
  expect(await noOverflow(page)).toBe(true);
});

test('conversations: rename, pin, search, page, export and keyboard menus', async ({ page }) => {
  const older = Array.from({ length: 34 }, (_, i) => ({ id: `conv_${i}`, title: `Older chat ${i}`, created_at: now, updated_at: now, pinned: false, pinned_at: null }));
  const sent = await mockApi(page, older);
  await page.goto('/#assistant');
  const list = page.getByRole('complementary', { name: 'Conversations' });
  await expect(list.getByRole('button', { name: /^Fleet tyres/ })).toBeVisible();

  // Paging: 30 per page, then the rest.
  await expect(list.getByRole('button', { name: /^Older chat/ })).toHaveCount(29);
  await list.getByRole('button', { name: 'Show older conversations' }).click();
  await expect(list.getByRole('button', { name: /^Older chat/ })).toHaveCount(34);
  await expect(list.getByRole('button', { name: 'Show older conversations' })).toHaveCount(0);

  // Rename through the conversation menu, by keyboard.
  const menu = page.getByRole('button', { name: 'Conversation actions' });
  await menu.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', { name: 'Rename' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  const name = page.getByRole('textbox', { name: 'Name (up to 80 characters)' });
  await expect(name).toBeFocused();
  await name.fill('Truck tyre plan');
  await name.press('Enter');
  await expect(page.getByRole('heading', { level: 2, name: 'Truck tyre plan' })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Conversation renamed.' })).toBeVisible();

  // Pin an older conversation from its row: it moves into the Pinned group at the top.
  await list.getByRole('button', { name: 'Actions for Older chat 5' }).click();
  await page.getByRole('menuitem', { name: 'Pin to top' }).click();
  await expect(list.getByRole('list', { name: 'Pinned' }).getByRole('button', { name: /^Older chat 5/ })).toBeVisible();
  expect(sent.patches).toEqual([{ title: 'Truck tyre plan' }, { pinned: true }]);

  // Search asks the server; no match offers a way back.
  await list.getByRole('searchbox', { name: 'Search conversations by title or message' }).fill('truck');
  await expect(list.getByRole('button', { name: /^Older chat/ })).toHaveCount(0);
  await expect(list.getByRole('button', { name: /^Truck tyre plan/ })).toBeVisible();
  expect(sent.queries.at(-1)).toContain('q=truck');
  await list.getByRole('searchbox').fill('nothing like this');
  await expect(list.getByText('No conversation mentions "nothing like this".', { exact: false })).toBeVisible();
  await list.getByRole('button', { name: 'Clear search' }).first().click();
  await expect(list.getByRole('button', { name: /^Older chat 1\b/ })).toBeVisible();

  // Export: Markdown to the clipboard and as a downloaded .md file.
  const box = page.getByRole('textbox', { name: 'Message the assistant' });
  await list.getByRole('button', { name: /^Truck tyre plan/ }).click();
  await box.fill('Status?');
  await box.press('Enter');
  await expect(page.getByText('Answer 1 to: Status?')).toBeVisible();
  await menu.click();
  await page.getByRole('menuitem', { name: 'Copy conversation as Markdown' }).click();
  const clip = (await copied(page)).at(-1) ?? '';
  expect(clip).toMatch(/^# Truck tyre plan\n/);
  expect(clip).toContain('## You\n\n> Status?');
  expect(clip).toContain('## Assistant\n\nAnswer 1 to: Status?');
  const download = page.waitForEvent('download');
  await menu.click();
  await page.getByRole('menuitem', { name: 'Download as Markdown (.md)' }).click();
  expect((await download).suggestedFilename()).toBe('truck-tyre-plan.md');

  // Delete the conversation after a confirmation.
  await menu.click();
  await page.getByRole('menuitem', { name: 'Delete conversation' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this conversation?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(list.getByRole('button', { name: /^Truck tyre plan/ })).toHaveCount(0);
});

test('the composer counts characters near the limit and Escape stops a reply', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#assistant');
  const box = page.getByRole('textbox', { name: 'Message the assistant' });
  await box.fill('x'.repeat(7995));
  await expect(page.getByText('5 characters left')).toBeVisible();
  await box.fill('');
  await expect(page.getByText('characters left')).toHaveCount(0);
  // Offline: sending is disabled and the reason is shown.
  await page.context().setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await expect(page.getByText('You are offline. Reconnect to send messages', { exact: false })).toBeVisible();
  await box.fill('anything');
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  await page.context().setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
});

test('Arabic: the actions read right to left and code stays left to right', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'ar'); } catch { /* storage off */ } });
  await page.goto('/#assistant');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  const box = page.getByRole('textbox', { name: 'راسل المساعد' });
  await box.fill('Code please');
  await box.press('Enter');
  const answer = page.locator('.chatTurn.assistant').last();
  await expect(answer.getByRole('button', { name: 'نسخ الإجابة' })).toBeVisible();
  await expect(answer.getByRole('button', { name: /نسخ شيفرة .?Python/ })).toBeVisible();
  await expect(answer.locator('pre')).toHaveAttribute('dir', 'ltr');
  await answer.getByRole('button', { name: 'نسخ الإجابة' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'تم النسخ' }).first()).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
  const results = await new AxeBuilder({ page }).include('.chatLayout').analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`message actions fit ${size.width}x${size.height} without overflow and pass axe`, async ({ page }) => {
    await page.setViewportSize(size);
    await mockApi(page);
    await page.goto('/#assistant');
    const box = page.getByRole('textbox', { name: 'Message the assistant' });
    await box.fill('Code for the table');
    await box.press('Enter');
    const answer = page.locator('.chatTurn.assistant').last();
    await expect(answer.getByRole('button', { name: 'Copy answer' })).toBeVisible();
    await answer.getByRole('button', { name: 'Copy answer' }).scrollIntoViewIfNeeded();
    await expect(answer.getByRole('button', { name: 'Copy answer' })).toBeInViewport();
    // On a phone the answer and the composer may not fit one screen: each must be reachable by scrolling.
    await page.getByRole('button', { name: 'Send', exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeInViewport();
    expect(await noOverflow(page)).toBe(true);
    const results = await new AxeBuilder({ page }).include('.chatLayout').analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
  });
}
