import { expect, test, type Page } from '@playwright/test';

// The voice assistant's 3D orb (docs/voice.md#the-orb): a WebGL scene that shows each state, with
// the reduced-motion fallback, no layout shift and an amber rim while an approval waits.
const now = '2026-10-12T09:00:00.000Z';

async function mockVoice(page: Page, options: { pendingApprovals?: number; replyDelayMs?: number } = {}) {
  let release: () => void = () => undefined;
  const replied = new Promise<void>((resolve) => { release = resolve; });
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/voice/sessions') return json({ id: 'voice_orb', locale: 'en-US', retention: 'session', assistant_name: 'Layla', status: 'active', created_at: now, updated_at: now, expires_at: null });
    if (path.endsWith('/transcript')) return json({ id: 'seg_1', session_id: 'voice_orb', role: 'user', text: 'hi', is_final: true, client_sequence: 1, created_at: now });
    if (path.endsWith('/ask/stream')) {
      await replied;
      const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      return route.fulfill({ contentType: 'text/event-stream', body: frame('meta', { intent: 'status', risk_tier: 'read', proposed_task: null, workspace: { tasks_total: 1, running: 0, waiting_approval: 0, pending_approvals: options.pendingApprovals ?? 0 } }) + frame('delta', { text: 'All quiet.' }) + frame('done', { assistant_segment: { id: 'a1', session_id: 'voice_orb', role: 'assistant', text: 'All quiet.', is_final: true, client_sequence: 0, created_at: now } }) });
    }
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    return route.abort();
  });
  await page.addInitScript(() => { localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', 'off'); });
  return { release };
}

const webgl = (page: Page) => page.evaluate(() => {
  const canvas = document.createElement('canvas');
  const gl = (canvas.getContext('webgl2') ?? canvas.getContext('webgl')) as WebGLRenderingContext | null;
  gl?.getExtension('WEBGL_lose_context')?.loseContext();
  return Boolean(gl);
});

test('the orb is a 3D scene that follows the assistant state without moving the layout', async ({ page }) => {
  const api = await mockVoice(page);
  await page.goto('/#voice');
  const talk = page.getByRole('button', { name: 'Tap to talk' });
  await expect(talk).toBeVisible();
  if (!(await webgl(page))) {
    // No WebGL in this browser: the drawn fallback stands in, inside the same button.
    await expect(talk.locator('.voiceOrbBlob')).toHaveCount(3);
    return;
  }
  const orb = talk.locator('.orb3d');
  await expect(orb.locator('canvas')).toHaveCount(1);
  await expect(orb).toHaveAttribute('data-orb-state', 'idle');
  await expect(orb).toHaveAttribute('aria-hidden', 'true');
  const before = await talk.boundingBox();
  const greeting = await page.getByRole('heading', { level: 2 }).filter({ hasText: 'Layla' }).boundingBox();
  // The canvas reaches past the button for the rings and halo, but never takes clicks or space.
  const canvas = await orb.boundingBox();
  expect(canvas!.width).toBeGreaterThan(before!.width);
  expect(await orb.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none');
  expect(await orb.evaluate((el) => getComputedStyle(el).position)).toBe('absolute');

  await page.getByRole('textbox', { name: 'Your message' }).fill('How is the workspace?');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(orb).toHaveAttribute('data-orb-state', 'thinking');
  api.release();
  await expect(page.getByText('All quiet.')).toBeVisible();
  await expect(orb).not.toHaveAttribute('data-orb-state', 'thinking');
  // Same size and the same distance to the greeting (the page may scroll to the new reply).
  const after = await talk.boundingBox();
  const greetingAfter = await page.getByRole('heading', { level: 2 }).filter({ hasText: 'Layla' }).boundingBox();
  expect([after!.width, after!.height]).toEqual([before!.width, before!.height]);
  expect(Math.round(greetingAfter!.y - after!.y)).toBe(Math.round(greeting!.y - before!.y));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('with reduced motion the orb is the still fallback, not a WebGL scene', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockVoice(page);
  await page.goto('/#voice');
  const talk = page.getByRole('button', { name: 'Tap to talk' });
  await expect(talk.locator('.voiceOrbBlob')).toHaveCount(3);
  await expect(talk.locator('canvas')).toHaveCount(0);
});

test('a pending approval marks the orb for the amber rim', async ({ page }) => {
  const api = await mockVoice(page, { pendingApprovals: 1 });
  api.release();
  await page.goto('/#voice');
  await page.getByRole('textbox', { name: 'Your message' }).fill('Anything waiting?');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('button', { name: '1 approval waiting for you' })).toBeVisible();
  if (!(await webgl(page))) return;
  await expect(page.locator('.orb3d')).toHaveAttribute('data-orb-alert', 'true');
});

test('the orb opens full screen: big, modal, keyboard-safe, and it closes back to the page', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const open = page.getByRole('button', { name: 'Full screen' });
  await open.click();
  const full = page.getByRole('dialog', { name: 'Layla voice assistant' });
  await expect(full).toBeVisible();
  await expect(full).toHaveAttribute('aria-modal', 'true');
  await expect(full.getByRole('status')).toHaveText(/Ready|Listening for/);
  const talk = full.getByRole('button', { name: 'Tap to talk' });
  await expect(talk).toBeFocused();
  // Focus stays in the dialog.
  for (let i = 0; i < 4; i += 1) {
    await page.keyboard.press('Tab');
    expect(await full.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  }
  const box = (await talk.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.width).toBeGreaterThan(Math.min(200, viewport.width * 0.5));
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  // Only one live scene: the page orb rests while the full screen is open.
  if (await webgl(page)) {
    await expect(full.locator('.orb3d canvas')).toHaveCount(1);
    await expect(page.locator('.voiceStage .orb3d')).toHaveCount(0);
  }
  await page.keyboard.press('Escape');
  await expect(full).toHaveCount(0);
  await expect(open).toBeFocused();
  if (await webgl(page)) await expect(page.locator('.orb3d canvas')).toHaveCount(1);

  // The close button works too, and tapping the page orb opens it.
  await page.getByRole('button', { name: 'Tap to talk' }).click();
  await expect(full).toBeVisible();
  await full.getByRole('button', { name: 'Close full screen' }).click();
  await expect(full).toHaveCount(0);
});

test('opening and closing the full screen ten times never crashes the tab', async ({ page }) => {
  // Regression: tapping the orb started listening, whose on-device probe
  // (SpeechRecognition.available) crashed the renderer in headless Chromium. Alternate the orb and
  // the Full screen button; each close frees the dialog's WebGL scene before the next one starts.
  test.setTimeout(120_000);
  await mockVoice(page);
  let crashed = false;
  page.on('crash', () => { crashed = true; });
  await page.goto('/#voice');
  const full = page.getByRole('dialog', { name: 'Layla voice assistant' });
  for (let i = 0; i < 10; i += 1) {
    if (i % 2) await page.locator('.voiceStage .voiceOrb').click();
    else await page.getByRole('button', { name: 'Full screen' }).click();
    await expect(full).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(full).toHaveCount(0);
    await expect(page.locator('.voiceStage').getByRole('button', { name: 'Tap to talk' })).toBeEnabled();
  }
  expect(crashed).toBe(false);
  if (await webgl(page)) await expect(page.locator('.orb3d canvas')).toHaveCount(1);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`the full screen fits ${width}x${height} without overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockVoice(page);
    await page.goto('/#voice');
    await page.getByRole('button', { name: 'Full screen' }).click();
    const full = page.getByRole('dialog', { name: 'Layla voice assistant' });
    await expect(full).toBeVisible();
    const fits = await full.evaluate((el) => ({ x: el.scrollWidth - el.clientWidth, y: el.scrollHeight - el.clientHeight, doc: document.documentElement.scrollWidth - document.documentElement.clientWidth }));
    expect(fits).toEqual({ x: 0, y: 0, doc: 0 });
    for (const name of ['Tap to talk', 'Close full screen']) {
      const b = (await full.getByRole('button', { name }).boundingBox())!;
      expect(b.y).toBeGreaterThanOrEqual(0);
      expect(b.y + b.height).toBeLessThanOrEqual(height);
    }
  });
}
