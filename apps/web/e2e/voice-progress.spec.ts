import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

// The Voice page never looks stuck (docs/voice.md#progress): a progress line from the server's
// `progress` events, the seconds after 2 s, "taking longer" with Cancel and Try again after 8 s,
// a hard stop after 30 s, and an error with Try again. The API is mocked at http://localhost:8000;
// the streamed answer is driven by the test (a fetch stand-in for /ask/stream) so a slow stream
// can be held open, and the page clock is faked so the waits take no real time.

const now = '2026-10-06T09:00:00.000Z';
const workspace = { tasks_total: 2, running: 1, waiting_approval: 0, pending_approvals: 0 };

async function mockApi(page: Page) {
  let sequence = 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/voice/sessions') {
      return json({ id: 'voice_e2e', locale: 'en-US', retention: 'session', assistant_name: 'Layla', status: 'active', created_at: now, updated_at: now, expires_at: null });
    }
    if (path.endsWith('/transcript')) {
      const body = route.request().postDataJSON() as { text: string };
      return json({ id: `transcript_${sequence++}`, session_id: 'voice_e2e', role: 'user', text: body.text, is_final: true, client_sequence: sequence, created_at: now });
    }
    return route.abort();
  });
  // The streamed answer: every /ask/stream call opens a stream the test writes to.
  await page.addInitScript(() => {
    type Control = { send: (event: string, data: unknown) => void; close: () => void; aborted: boolean };
    const w = window as unknown as { __streams: Control[] };
    w.__streams = [];
    const realFetch = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (!url.endsWith('/ask/stream')) return realFetch(input, init);
      const encoder = new TextEncoder();
      const control = { aborted: false } as Control;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          control.send = (event, data) => controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
          control.close = () => { try { controller.close(); } catch { /* closed */ } };
          init?.signal?.addEventListener('abort', () => { control.aborted = true; try { controller.error(new DOMException('aborted', 'AbortError')); } catch { /* closed */ } });
        },
      });
      w.__streams.push(control);
      return Promise.resolve(new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }));
    };
  });
}

type Frame = [string, unknown];
async function stream(page: Page, index: number, ...frames: Frame[]) {
  await page.waitForFunction((i) => (window as unknown as { __streams: unknown[] }).__streams.length > i, index);
  await page.evaluate(([i, list]) => {
    const streams = (window as unknown as { __streams: { send: (e: string, d: unknown) => void }[] }).__streams;
    for (const [event, data] of list as Frame[]) streams[i as number].send(event, data);
  }, [index, frames] as const);
}
async function end(page: Page, index: number) {
  await page.evaluate((i) => (window as unknown as { __streams: { close: () => void }[] }).__streams[i].close(), index);
}
const meta: Frame = ['meta', { intent: 'question', risk_tier: 'read', proposed_task: null, workspace, vision: false, action_kind: null }];
const tyre: Frame = ['progress', { phase: 'checking', topic: 'tyre' }];

async function open(page: Page) {
  await mockApi(page);
  await page.addInitScript(() => { localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', 'off'); localStorage.setItem('anum.voice.chat', 'on'); });
  await page.clock.install({ time: new Date(now) });
  await page.goto('/#voice');
  await expect(page.getByRole('button', { name: 'Tap to talk' })).toBeVisible();
}
async function ask(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Your message' }).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}
const thinking = (page: Page) => page.locator('.voiceThinking');

test('shows what is being checked, the seconds, then a slow notice with Cancel and Try again', async ({ page }) => {
  await open(page);
  await ask(page, 'How many tyres are due?');
  await expect(thinking(page)).toContainText('One moment');
  await stream(page, 0, meta, tyre);
  await expect(thinking(page)).toContainText('Checking tyre data...');
  await expect(thinking(page)).not.toContainText(/\d s/);
  await page.clock.fastForward(2600);
  await expect(thinking(page)).toContainText(/Checking tyre data\.\.\. [2-3] s/);
  await expect(thinking(page).locator('.voiceThinkingSlow')).toHaveCount(0);
  await page.clock.fastForward(6000);
  await expect(thinking(page).locator('.voiceThinkingSlow')).toHaveText('This is taking longer than usual.');
  await expect(thinking(page).getByRole('button', { name: 'Cancel' })).toBeVisible();
  await expect(thinking(page).getByRole('button', { name: 'Try again' })).toBeVisible();
  // The answer arrives after all: the progress goes away and the reply shows.
  await stream(page, 0, ['delta', { text: '1,948 tyres are due in 30 days.' }], ['done', { assistant_segment: { id: 'a1', session_id: 'voice_e2e', role: 'assistant', text: '1,948 tyres are due in 30 days.', is_final: true, client_sequence: 0, created_at: now } }]);
  await end(page, 0);
  await expect(page.getByText('1,948 tyres are due in 30 days.').first()).toBeVisible();
  await expect(thinking(page)).toHaveCount(0);
});

test('the live region announces the step, not every second', async ({ page }) => {
  await open(page);
  await ask(page, 'How many tyres are due?');
  await stream(page, 0, meta, tyre);
  const region = thinking(page).locator('[role="status"]');
  await expect(region).toHaveText('Checking tyre data...');
  await page.clock.fastForward(4000);
  await expect(region).toHaveText('Checking tyre data...');
});

test('Cancel stops the request and offers Try again', async ({ page }) => {
  await open(page);
  await ask(page, 'How many tyres are due?');
  await stream(page, 0, meta, tyre);
  await thinking(page).getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByText('Cancelled. Ask again whenever you like.').first()).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __streams: { aborted: boolean }[] }).__streams[0].aborted)).toBe(true);
  await expect(thinking(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Try again' }).click();
  await stream(page, 1, meta, tyre);
  await expect(thinking(page)).toContainText('Checking tyre data...');
});

test('a stream that never answers stops after 30 seconds with a clear message', async ({ page }) => {
  await open(page);
  await ask(page, 'How many tyres are due?');
  await stream(page, 0, meta, tyre);
  await page.clock.fastForward(31000);
  await expect(page.getByText(/No answer came in 30 seconds/).first()).toBeVisible();
  await expect(thinking(page)).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __streams: { aborted: boolean }[] }).__streams[0].aborted)).toBe(true);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('an error event says so and offers Try again', async ({ page }) => {
  await open(page);
  await ask(page, 'How many tyres are due?');
  await stream(page, 0, meta, ['error', { code: 'model_unavailable', message: 'I could not answer. Try again in a moment.' }]);
  await end(page, 0);
  await expect(page.getByText('I could not answer. Try again in a moment.').first()).toBeVisible();
  await expect(thinking(page)).toHaveCount(0);
  await page.getByRole('button', { name: 'Try again' }).click();
  await stream(page, 1, meta);
  await expect(thinking(page)).toBeVisible();
});

test('the slow state fits every viewport without overflow @shots', async ({ page }, info) => {
  const dir = process.env.VOICE_SHOTS;
  await open(page);
  await ask(page, 'How many tyres are due?');
  await stream(page, 0, meta, tyre);
  await page.clock.fastForward(8600);
  await expect(thinking(page).getByRole('button', { name: 'Try again' })).toBeVisible();
  for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
    await page.setViewportSize({ width, height });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const box = await thinking(page).boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width && box.y + box.height <= height).toBe(true);
    for (const button of await thinking(page).getByRole('button').all()) {
      const rect = await button.boundingBox();
      expect(rect && rect.x >= 0 && rect.x + rect.width <= width && rect.height >= 40).toBe(true);
    }
    if (dir) { mkdirSync(dir, { recursive: true }); await page.screenshot({ path: `${dir}/${info.project.name}-${width}x${height}.png` }); }
  }
});
