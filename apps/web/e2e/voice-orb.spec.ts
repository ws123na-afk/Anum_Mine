import { expect, test, type Locator, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { fakeMedia, mockCallApi, startCall } from './support/callMocks';

// The voice assistant's space orb (docs/voice.md#the-space-orb), in the real app: the cinematic
// scene's independent motion, shooting stars, tilt, every state from (mocked) voice events, Pause,
// reduced motion, cleanup after repeated opens and route changes, and the layout at nine sizes.
const now = '2026-10-12T09:00:00.000Z';

interface Snapshot {
  t: number; mode: string; paused: boolean; frames: number; meteorsShown: number; tilt: [number, number];
  astronaut: [number, number, number]; earth: [number, number, number]; satellite: [number, number, boolean]; satellite2: [number, number];
  texturesReady: boolean; crops: { x: number; y: number; w: number; h: number }[];
}
interface Window { __say(text: string): void; __fail(error: string): void; __endSpeech(): void; __frames(): number; __loud(on: boolean): void }

async function mockVoice(page: Page, options: { pendingApprovals?: number } = {}) {
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
      return route.fulfill({ contentType: 'text/event-stream', body: frame('meta', { intent: 'status', risk_tier: 'confirm', proposed_task: null, workspace: { tasks_total: 1, running: 0, waiting_approval: 0, pending_approvals: options.pendingApprovals ?? 0 } }) + frame('delta', { text: 'All quiet.' }) + frame('done', { assistant_segment: { id: 'a1', session_id: 'voice_orb', role: 'assistant', text: 'All quiet.', is_final: true, client_sequence: 0, created_at: now }, risk_tier: 'confirm' }) });
    }
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    return route.abort();
  });
  // Stand-ins for the browser's voice: a recognizer that hears what the test says (or fails), a
  // microphone stream, a system voice that keeps speaking (with word boundaries) until told to stop,
  // and a count of the animation frames waiting to run.
  await page.addInitScript(() => {
    localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', 'off');
    const w = window as unknown as Window & Record<string, unknown>;
    const queue: string[] = [];
    let fail: string | null = null;
    w.__say = (text) => { queue.push(text); };
    w.__fail = (error) => { fail = error; };
    class FakeRecognition {
      continuous = false; interimResults = false; lang = 'en-US';
      onresult: ((event: unknown) => void) | null = null; onerror: ((event: unknown) => void) | null = null; onend: (() => void) | null = null;
      private timer = 0;
      start() {
        const poll = () => {
          if (fail) { const error = fail; fail = null; this.onerror?.({ error }); this.onend?.(); return; }
          const phrase = queue.shift();
          if (phrase === undefined) { this.timer = window.setTimeout(poll, 50); return; }
          this.onresult?.({ results: [Object.assign([{ transcript: phrase }], { isFinal: true })] });
          window.setTimeout(() => this.onend?.(), 20);
        };
        poll();
      }
      stop() { window.clearTimeout(this.timer); this.onend?.(); }
      abort() { window.clearTimeout(this.timer); }
    }
    Object.assign(window, { SpeechRecognition: FakeRecognition, webkitSpeechRecognition: FakeRecognition });
    let gain: GainNode | null = null;
    w.__loud = (on) => { if (gain) gain.gain.value = on ? 0.8 : 0.0005; };
    const media = navigator.mediaDevices ?? ({} as MediaDevices);
    media.getUserMedia = async () => {
      const audio = new AudioContext();
      void audio.resume();
      const oscillator = audio.createOscillator();
      gain = audio.createGain();
      gain.gain.value = 0.0005;
      const destination = audio.createMediaStreamDestination();
      oscillator.connect(gain).connect(destination);
      oscillator.start();
      return destination.stream;
    };
    Object.defineProperty(navigator, 'mediaDevices', { value: media, configurable: true });
    let current: SpeechSynthesisUtterance | null = null;
    let words = 0;
    const end = () => { const utterance = current; current = null; window.clearInterval(words); utterance?.onend?.(new Event('end') as SpeechSynthesisEvent); };
    w.__endSpeech = end;
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      speak(utterance: SpeechSynthesisUtterance) {
        current = utterance;
        window.setTimeout(() => utterance.onstart?.(new Event('start') as SpeechSynthesisEvent), 10);
        words = window.setInterval(() => utterance.onboundary?.(new Event('boundary') as SpeechSynthesisEvent), 250);
      },
      cancel: end,
      getVoices: () => [], addEventListener: () => undefined, removeEventListener: () => undefined, speaking: false, pending: false, paused: false,
    } });
    const pending = new Set<number>();
    const raf = window.requestAnimationFrame.bind(window), caf = window.cancelAnimationFrame.bind(window);
    window.requestAnimationFrame = (fn) => { const id = raf((time) => { pending.delete(id); fn(time); }); pending.add(id); return id; };
    window.cancelAnimationFrame = (id) => { pending.delete(id); caf(id); };
    w.__frames = () => pending.size;
  });
  return { release };
}

/** Collects page errors and console errors (failed requests to the mocked API are expected). */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  // The mocked API refuses every other request, so the shell's own fetches fail by design.
  page.on('pageerror', (error) => { if (!/Failed to fetch/.test(error.message)) errors.push(error.message); });
  page.on('console', (message) => { if (message.type() === 'error' && !/Failed to load resource|ERR_FAILED|Failed to fetch/.test(message.text())) errors.push(message.text()); });
  return errors;
}

const snapshot = (orb: Locator) => orb.locator('canvas').evaluate((canvas) => (canvas as HTMLCanvasElement & { anumSpace: { snapshot(): unknown } }).anumSpace.snapshot()) as Promise<Snapshot>;
const say = (page: Page, text: string) => page.evaluate((value) => (window as unknown as Window).__say(value), text);
const animationLoops = (page: Page) => page.evaluate(() => (window as unknown as Window).__frames());
/** Pending frames, sampled until stable (a loop always has exactly one frame waiting). */
async function steadyLoops(page: Page) {
  let last = -1;
  for (let i = 0; i < 20; i += 1) {
    const value = await animationLoops(page);
    if (value === last) return value;
    last = value;
    await page.waitForTimeout(100);
  }
  return last;
}

async function openFull(page: Page) {
  await page.locator('.voiceConsoleMeta .voiceIconButton').first().click();
  const full = page.locator('.orbFullscreen');
  await expect(full).toBeVisible();
  await expect.poll(async () => (await snapshot(full.locator('.spaceOrb.full'))).texturesReady, { timeout: 15_000 }).toBe(true);
  return full;
}

test('the full scene: sprites move on their own, the satellite passes behind and in front of Earth, and shooting stars fall', async ({ page }) => {
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  const atlas: number[] = [];
  page.on('response', (response) => { if (/space-assets.*\.webp/.test(response.url())) atlas.push(response.status()); });
  await mockVoice(page);
  await page.goto('/#voice');
  const full = await openFull(page);
  const orb = full.locator('.spaceOrb.full');
  expect(atlas).toEqual([200]);
  // The atlas is served by the app itself (same-origin CSP).
  const atlasUrl = await page.evaluate(() => performance.getEntriesByType('resource').map((entry) => entry.name).find((name) => name.includes('space-assets'))!);
  expect(new URL(atlasUrl).origin).toBe(new URL(page.url()).origin);

  // Sprites are cut to their opaque pixels: the edges of each crop are mostly transparent, so no
  // square block is drawn around them.
  const first = await snapshot(orb);
  expect(first.crops).toHaveLength(3);
  const transparentEdges = await page.evaluate(async ({ crops, src }) => {
    const image = new Image();
    image.src = src;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d')!;
    context.drawImage(image, 0, 0);
    return crops.map((c) => {
      const data = context.getImageData(c.x, c.y, c.w, c.h).data;
      let edge = 0, clear = 0;
      for (let x = 0; x < c.w; x += 1) for (const y of [0, c.h - 1]) { edge += 1; if (data[(y * c.w + x) * 4 + 3] < 35) clear += 1; }
      for (let y = 0; y < c.h; y += 1) for (const x of [0, c.w - 1]) { edge += 1; if (data[(y * c.w + x) * 4 + 3] < 35) clear += 1; }
      return clear / edge;
    });
  }, { crops: first.crops, src: atlasUrl });
  for (const share of transparentEdges) expect(share).toBeGreaterThan(0.5);

  // Watch for twelve seconds of scene time.
  const samples: Snapshot[] = [first];
  while (samples[samples.length - 1].t - first.t < 12) {
    await page.waitForTimeout(500);
    samples.push(await snapshot(orb));
  }
  const last = samples[samples.length - 1];
  const moved = (pick: (s: Snapshot) => number[]) => Math.max(...samples.map((s) => Math.hypot(...pick(s).map((v, i) => v - pick(first)[i]))));
  expect(moved((s) => s.astronaut.slice(0, 2) as number[])).toBeGreaterThan(5);
  expect(moved((s) => s.earth.slice(0, 2) as number[])).toBeGreaterThan(2);
  expect(moved((s) => s.satellite.slice(0, 2) as number[])).toBeGreaterThan(20);
  expect(moved((s) => s.satellite2)).toBeGreaterThan(50);
  // Independent: each sprite's path differs from the others' (not one group moved together).
  const path = (pick: (s: Snapshot) => number) => samples.map((s) => pick(s) - pick(first)).join();
  expect(new Set([path((s) => s.astronaut[1]), path((s) => s.earth[1]), path((s) => s.satellite[1]), path((s) => s.satellite2[1])]).size).toBe(4);
  expect(new Set(samples.map((s) => s.satellite[2]))).toEqual(new Set([true, false]));
  expect(last.meteorsShown - first.meteorsShown).toBeGreaterThanOrEqual(3);

  // The trigger sends one more straight away.
  const before = (await snapshot(orb)).meteorsShown;
  await full.getByRole('button', { name: 'Shooting star' }).click();
  expect((await snapshot(orb)).meteorsShown).toBeGreaterThan(before);
  expect(errors).toEqual([]);
});

test('Pause freezes the scene and Resume continues without a jump', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const full = await openFull(page);
  const orb = full.locator('.spaceOrb.full');
  await full.getByRole('button', { name: 'Pause' }).click();
  await expect(full.getByRole('button', { name: 'Resume' })).toHaveAttribute('aria-pressed', 'true');
  await expect(full.getByRole('button', { name: 'Shooting star' })).toBeDisabled();
  // Let the tilt settle (the pointer moved to the button), then nothing may move.
  await page.waitForTimeout(1500);
  const paused = await snapshot(orb);
  await page.waitForTimeout(1500);
  const still = await snapshot(orb);
  expect(still.t).toBe(paused.t);
  expect(still.meteorsShown).toBe(paused.meteorsShown);
  // Only the last of the pointer tilt's easing may remain: well under a pixel.
  for (const key of ['astronaut', 'earth', 'satellite2'] as const) {
    expect(Math.hypot(still[key][0] - paused[key][0], still[key][1] - paused[key][1])).toBeLessThan(0.5);
  }
  // Resumed: time runs again from where it stopped, covering only the time since Resume (the
  // three seconds spent paused are not added).
  const resumedAt = Date.now();
  await full.getByRole('button', { name: 'Resume' }).click();
  await page.waitForTimeout(300);
  const resumed = await snapshot(orb);
  expect(resumed.t).toBeGreaterThan(paused.t);
  expect(resumed.t - paused.t).toBeLessThanOrEqual((Date.now() - resumedAt) / 1000 + 0.05);
});

test('the cursor tilts the orb', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const full = await openFull(page);
  const orb = full.locator('.spaceOrb.full');
  const box = (await orb.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.95, box.y + box.height * 0.2, { steps: 4 });
  await expect.poll(async () => (await snapshot(orb)).tilt[0]).toBeGreaterThan(0.6);
  expect((await snapshot(orb)).tilt[1]).toBeLessThan(-0.4);
  await page.mouse.move(box.x + box.width * 0.05, box.y + box.height * 0.6, { steps: 4 });
  await expect.poll(async () => (await snapshot(orb)).tilt[0]).toBeLessThan(-0.6);
});

test('touch tilts the orb', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await mockVoice(page);
  await page.goto('/#voice');
  const orb = page.locator('.voiceStage .spaceOrb.compact');
  await expect(orb).toBeVisible();
  const box = (await orb.boundingBox())!;
  // A touch on the orb's corner tilts it (the tap also starts listening, as the orb is the button).
  await orb.dispatchEvent('pointerdown', { clientX: box.x + box.width - 2, clientY: box.y + 2, pointerType: 'touch', isPrimary: true });
  await expect.poll(async () => (await snapshot(orb)).tilt[0]).toBeGreaterThan(0.6);
  await context.close();
});

test('every state follows the real voice events: standby, listening, thinking, speaking, alert', async ({ page }) => {
  const errors = watchErrors(page);
  const api = await mockVoice(page, { pendingApprovals: 1 });
  await page.goto('/#voice');
  const compact = page.locator('.voiceStage .spaceOrb.compact');
  await expect(compact).toHaveAttribute('data-space-mode', 'standby');
  await expect.poll(async () => (await snapshot(compact)).mode).toBe('standby');

  // Tapping the orb opens the full screen and starts the microphone: listening.
  await page.locator('.voiceStage .voiceOrb').click();
  const full = page.locator('.orbFullscreen .spaceOrb.full');
  await expect(full).toHaveAttribute('data-space-mode', 'listening');
  await expect.poll(async () => (await snapshot(full)).mode).toBe('listening');
  await expect(page.locator('.voiceStage .spaceOrb')).toHaveCount(0);

  // Something was heard and sent: thinking until the reply arrives.
  await say(page, 'How is the workspace?');
  await expect(full).toHaveAttribute('data-space-mode', 'thinking');
  // The reply is spoken: speaking, with the bars following the voice.
  api.release();
  await expect(full).toHaveAttribute('data-space-mode', 'speaking');
  await expect.poll(async () => (await snapshot(full)).mode).toBe('speaking');
  // The reply ends; an approval is waiting, so the orb rests in amber.
  await page.evaluate(() => (window as unknown as Window).__endSpeech());
  await expect(full).toHaveAttribute('data-space-mode', 'alert');
  await page.keyboard.press('Escape');
  await expect(compact).toHaveAttribute('data-space-mode', 'alert');
  await expect.poll(async () => (await snapshot(compact)).mode).toBe('alert');
  expect(errors).toEqual([]);
});

test('a denied microphone, a silence timeout and a cancel return the orb to standby', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  await page.locator('.voiceConsoleMeta .voiceIconButton').first().click();
  const full = page.locator('.orbFullscreen .spaceOrb.full');
  const talk = page.locator('.orbFullscreenTalk');
  await expect(full).toHaveAttribute('data-space-mode', 'standby');
  for (const error of ['not-allowed', 'no-speech']) {
    await talk.click();
    await expect(full).toHaveAttribute('data-space-mode', 'listening');
    await page.evaluate((value) => (window as unknown as Window).__fail(value), error);
    await expect(full).toHaveAttribute('data-space-mode', 'standby');
  }
  // Tapping the orb again while listening cancels: standby.
  await talk.click();
  await expect(full).toHaveAttribute('data-space-mode', 'listening');
  await talk.click();
  await expect(full).toHaveAttribute('data-space-mode', 'standby');
});

test('a call drives the same orb: listening, then speaking while the reply plays', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  const orb = page.locator('.callOrb .spaceOrb.compact');
  await expect(orb).toHaveAttribute('data-space-mode', 'listening');
  await page.evaluate(() => (window as unknown as Window).__loud(true));
  await expect(page.getByText('Hearing you', { exact: true })).toBeVisible();
  await expect(orb).toHaveAttribute('data-space-mode', 'listening');
  await page.evaluate(() => (window as unknown as Window).__say("What's the status?"));
  await page.waitForTimeout(400);
  await page.evaluate(() => (window as unknown as Window).__loud(false));
  await expect(orb).toHaveAttribute('data-space-mode', 'speaking');
  await expect.poll(async () => (await snapshot(orb)).mode).toBe('speaking');
});

test('with reduced motion the scene is still and sends no shooting stars', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockVoice(page);
  await page.goto('/#voice');
  const full = await openFull(page);
  const orb = full.locator('.spaceOrb.full');
  await page.waitForTimeout(1500);
  const still = await snapshot(orb);
  expect(still.t).toBe(0);
  expect(still.meteorsShown).toBe(0);
  await expect(full.getByRole('button', { name: 'Shooting star' })).toBeDisabled();
});

test('opening and closing ten times and changing routes leave no animation loop behind', async ({ page }) => {
  test.setTimeout(120_000);
  const errors = watchErrors(page);
  await mockVoice(page);
  let crashed = false;
  page.on('crash', () => { crashed = true; });
  const nav = page.getByRole('navigation', { name: 'Primary' });
  await page.goto('/#tasks');
  await expect(page.locator('main')).toBeVisible();
  const elsewhere = await steadyLoops(page);
  await nav.getByRole('button', { name: 'Voice', exact: true }).click();
  await expect(page.locator('.voiceStage .spaceOrb.compact')).toBeVisible();
  // One loop for the page orb.
  expect(await steadyLoops(page)).toBe(elsewhere + 1);
  const full = page.locator('.orbFullscreen');
  for (let i = 0; i < 10; i += 1) {
    await page.locator('.voiceConsoleMeta .voiceIconButton').first().click();
    await expect(full.locator('.spaceOrb.full')).toBeVisible();
    // Still one loop: the page orb rests while the full screen runs.
    expect(await steadyLoops(page)).toBe(elsewhere + 1);
    await page.keyboard.press('Escape');
    await expect(full).toHaveCount(0);
  }
  expect(await steadyLoops(page)).toBe(elsewhere + 1);
  // Leaving for the Call lobby and then Tasks releases the page orb's loop.
  await nav.getByRole('button', { name: 'Call', exact: true }).click();
  await expect(page.locator('.spaceOrb')).toHaveCount(0);
  await nav.getByRole('button', { name: 'Tasks', exact: true }).click();
  await expect(page.locator('.spaceOrb')).toHaveCount(0);
  expect(await steadyLoops(page)).toBe(elsewhere);
  expect(crashed).toBe(false);
  expect(errors).toEqual([]);
});

const SIZES = [[320, 640], [390, 844], [768, 1024], [1024, 768], [1366, 768], [1440, 900], [1280, 720], [1920, 1080], [412, 915]] as const;
for (const locale of ['en', 'ar'] as const) {
  test(`the page orb and the full screen fit nine sizes without overflow (${locale})`, async ({ page }) => {
    test.setTimeout(120_000);
    await useSavedLocale(page, locale);
    await mockVoice(page);
    await page.goto('/#voice');
    for (const [width, height] of SIZES) {
      await page.setViewportSize({ width, height });
      const compact = page.locator('.voiceStage .spaceOrb.compact');
      await expect(compact).toBeVisible();
      const round = (await compact.boundingBox())!;
      expect(Math.abs(round.width - round.height)).toBeLessThan(2);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
      await page.locator('.voiceConsoleMeta .voiceIconButton').first().click();
      const full = page.locator('.orbFullscreen');
      await expect(full).toBeVisible();
      const fits = await full.evaluate((el) => ({ x: el.scrollWidth - el.clientWidth, y: el.scrollHeight - el.clientHeight, doc: document.documentElement.scrollWidth - document.documentElement.clientWidth }));
      expect(fits, `${width}x${height}`).toEqual({ x: 0, y: 0, doc: 0 });
      for (const control of [full.locator('.orbFullscreenTalk'), full.locator('.orbFullscreenClose'), ...await full.locator('.spaceControls button').all()]) {
        const b = (await control.boundingBox())!;
        expect(b.x, `${width}x${height}`).toBeGreaterThanOrEqual(0);
        expect(b.y).toBeGreaterThanOrEqual(0);
        expect(b.x + b.width).toBeLessThanOrEqual(width);
        expect(b.y + b.height).toBeLessThanOrEqual(height);
      }
      // The talk button sits on the orb and the controls do not cover it.
      const talk = (await full.locator('.orbFullscreenTalk').boundingBox())!;
      const bar = (await full.locator('.spaceControls').boundingBox())!;
      expect(talk.width, `${width}x${height}`).toBeGreaterThan(80);
      expect(talk.y + talk.height <= bar.y || bar.x + bar.width <= talk.x || talk.x + talk.width <= bar.x).toBe(true);
      await page.keyboard.press('Escape');
      await expect(full).toHaveCount(0);
    }
  });
}

test('the full screen is modal and keyboard-safe, and closes back to the page', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const open = page.getByRole('button', { name: 'Full screen' });
  await open.click();
  const full = page.getByRole('dialog', { name: 'Layla voice assistant' });
  await expect(full).toHaveAttribute('aria-modal', 'true');
  await expect(full.getByRole('status')).toHaveText(/Ready|Listening for/);
  await expect(full.getByRole('button', { name: 'Tap to talk' })).toBeFocused();
  for (let i = 0; i < 6; i += 1) {
    await page.keyboard.press('Tab');
    expect(await full.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(full).toHaveCount(0);
  await expect(open).toBeFocused();
  await page.getByRole('button', { name: 'Full screen' }).click();
  await full.getByRole('button', { name: 'Close full screen' }).click();
  await expect(full).toHaveCount(0);
});
