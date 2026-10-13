import { expect, test, type Page } from '@playwright/test';
import { fakeMedia, mockCallApi, startCall } from './support/callMocks';

// The Voice page listens at once and reliably, has a focus mode with nothing but the scene and the
// orb, and stops listening when the tab is left (docs/voice.md#listening-quickly-and-reliably,
// #full-screen-focus, #leaving-the-tab). The browser's speech APIs are fakes that keep one
// recognition session per page (a second start while one runs is refused) and log timestamps.
const now = '2026-10-12T09:00:00.000Z';

interface Probe {
  log: [string, number][]; say(text: string): void; endSpeech(): void; speaking(): boolean; live(): number; tracks(): string[];
  hide(): void; show(): void; blurWindow(): void; endActive(): void;
}
const probe = <T>(page: Page, run: (p: Probe) => T) => page.evaluate(`(${run.toString()})(window.__probe)`) as Promise<T>;

// What the server measured for the turn (the `timing` event, docs/voice.md#latency).
const serverTiming = { first_token_ms: 640, tool_ms: 120, total_ms: 910, tool_rounds: 1, tool_calls: 1, grounding: 'tools', sources: ['workspace_tasks'] };

async function mockVoice(page: Page, { wake = 'off', probeMs = 400, reply = 'All quiet.', task = null as string | null, pending = 0 } = {}) {
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/voice/sessions') return json({ id: 'v', locale: 'en-US', retention: 'session', assistant_name: 'Layla', status: 'active', created_at: now, updated_at: now, expires_at: null });
    if (path.endsWith('/transcript')) return json({ id: 's', session_id: 'v', role: 'user', text: 'hi', is_final: true, client_sequence: 1, created_at: now });
    if (path.endsWith('/ask/stream')) {
      const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      return route.fulfill({ contentType: 'text/event-stream', body: frame('meta', { intent: task ? 'create_task' : 'status', risk_tier: 'confirm', proposed_task: task, workspace: { tasks_total: 1, running: 0, waiting_approval: 0, pending_approvals: pending } }) + frame('delta', { text: reply }) + frame('timing', serverTiming) + frame('done', { assistant_segment: { id: 'a', session_id: 'v', role: 'assistant', text: reply, is_final: true, client_sequence: 0, created_at: now }, risk_tier: 'confirm' }) });
    }
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    return route.abort();
  });
  await page.addInitScript(({ wake, probeMs }) => {
    // A real (non-automated) browser probes on-device recognition; the probe takes `probeMs`.
    Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false, configurable: true });
    localStorage.setItem('anum.voice.reply', ''); localStorage.setItem('anum.voice.name', 'Layla'); localStorage.setItem('anum.voice.wake', wake);
    const log: [string, number][] = [];
    const mark = (what: string) => log.push([what, performance.now()]);
    document.addEventListener('pointerdown', () => mark('tap'), true);
    const queue: string[] = [];
    let active: Recognizer | null = null;
    const all: Recognizer[] = [];
    class Recognizer {
      static available() { return new Promise((resolve) => setTimeout(() => resolve('unavailable'), probeMs)); }
      continuous = false; interimResults = false; lang = 'en-US';
      onresult: ((event: unknown) => void) | null = null; onerror: ((event: unknown) => void) | null = null; onend: (() => void) | null = null;
      live = false; timer = 0;
      start() {
        if (active) { mark('refused'); throw new DOMException('recognition has already started', 'InvalidStateError'); }
        active = this; this.live = true; all.push(this);
        mark(this.interimResults ? 'talk-start' : 'wake-start');
        mark(`lang-${this.lang}`);
        const poll = () => {
          if (!this.live) return;
          const phrase = queue.shift();
          if (phrase === undefined) { this.timer = window.setTimeout(poll, 30); return; }
          this.onresult?.({ results: [Object.assign([{ transcript: phrase }], { isFinal: true })] });
          if (this.interimResults) window.setTimeout(() => this.finish(), 20); else this.timer = window.setTimeout(poll, 30);
        };
        poll();
      }
      finish() {
        if (!this.live) return;
        this.live = false; window.clearTimeout(this.timer);
        window.setTimeout(() => { if (active === this) active = null; mark('end'); this.onend?.(); }, 40);
      }
      stop() { this.finish(); }
      abort() { this.finish(); }
    }
    Object.assign(window, { SpeechRecognition: Recognizer, webkitSpeechRecognition: Recognizer });
    const tracks: MediaStreamTrack[] = [];
    const media = navigator.mediaDevices ?? ({} as MediaDevices);
    media.getUserMedia = async () => {
      const audio = new AudioContext();
      const destination = audio.createMediaStreamDestination();
      audio.createOscillator().connect(destination);
      tracks.push(...destination.stream.getTracks());
      return destination.stream;
    };
    Object.defineProperty(navigator, 'mediaDevices', { value: media, configurable: true });
    let current: SpeechSynthesisUtterance | null = null;
    const end = () => { const utterance = current; current = null; utterance?.onend?.(new Event('end') as SpeechSynthesisEvent); };
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
      speak(utterance: SpeechSynthesisUtterance) { current = utterance; mark('speak'); window.setTimeout(() => utterance.onstart?.(new Event('start') as SpeechSynthesisEvent), 5); },
      cancel() { if (current) mark('speech-cancelled'); end(); },
      getVoices: () => [], addEventListener: () => undefined, removeEventListener: () => undefined,
    } });
    let hidden = false, focused = true;
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    document.hasFocus = () => focused;
    (window as unknown as { __probe: Probe }).__probe = {
      log, say: (text) => queue.push(text), endSpeech: end, speaking: () => current !== null,
      live: () => all.filter((r) => r.live).length,
      endActive: () => active?.finish(),
      tracks: () => tracks.map((track) => track.readyState),
      hide: () => { hidden = true; document.dispatchEvent(new Event('visibilitychange')); },
      show: () => { hidden = false; focused = true; document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')); },
      blurWindow: () => { focused = false; window.dispatchEvent(new Event('blur')); },
    };
    new MutationObserver(() => {
      const state = document.querySelector('.voiceSpace')?.getAttribute('data-state');
      if (state && log[log.length - 1]?.[0] !== `ui-${state}`) mark(`ui-${state}`);
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ['data-state'] });
  }, { wake, probeMs });
}

const elapsed = (log: [string, number][], from: string, to: string) => {
  const start = [...log].reverse().find((entry) => entry[0] === from)!;
  const end = log.find((entry) => entry[0] === to && entry[1] >= start[1]);
  return end ? end[1] - start[1] : Infinity;
};

test('tapping the orb shows listening at once and starts the microphone without waiting for the on-device probe', async ({ page }) => {
  await mockVoice(page, { probeMs: 400 });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await expect(voice.locator('.voiceSpaceTalk')).toBeVisible();
  await page.waitForTimeout(700); // the probe ran in the background when the page opened
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await expect.poll(() => probe(page, (p) => p.log.some(([what]) => what === 'talk-start'))).toBe(true);
  const log = await probe(page, (p) => p.log);
  // Before: about 670 ms to show listening and 430 ms to start (the probe ran on the first tap).
  expect(elapsed(log, 'tap', 'ui-listening')).toBeLessThan(150);
  expect(elapsed(log, 'tap', 'talk-start')).toBeLessThan(250);
});

test('saying the name alone hands the microphone to a listening turn (no refused start, no stuck state)', async ({ page, context }) => {
  await context.grantPermissions(['microphone']);
  await mockVoice(page, { wake: 'on' });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await expect.poll(() => probe(page, (p) => p.log.some(([what]) => what === 'wake-start'))).toBe(true);
  await probe(page, (p) => { p.log.push(['said-name', performance.now()]); p.say('Layla'); });
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await expect.poll(() => probe(page, (p) => p.log.some(([what]) => what === 'talk-start'))).toBe(true);
  const log = await probe(page, (p) => p.log);
  // Before: the new recognizer was refused while the wake listener was ending, and nothing listened.
  expect(log.filter(([what]) => what === 'refused')).toEqual([]);
  expect(elapsed(log, 'said-name', 'talk-start')).toBeLessThan(250);
  // A whole turn: what was heard is answered, spoken, then the page listens for a follow-up.
  await probe(page, (p) => p.say('How is the workspace?'));
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(true);
  await probe(page, (p) => p.endSpeech());
  await expect(voice).toHaveAttribute('data-state', /idle|listening/);
});

test('the wake listener listens again at once when the browser ends its session', async ({ page, context }) => {
  await context.grantPermissions(['microphone']);
  await mockVoice(page, { wake: 'on' });
  await page.goto('/#voice');
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
  await page.waitForTimeout(1100); // a normal session, not one that failed at once
  // The browser ends the continuous session (silence, its session limit).
  await probe(page, (p) => { p.log.push(['browser-ended', performance.now()]); p.endActive(); });
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
  const log = await probe(page, (p) => p.log);
  // Before: 250 ms of deafness after every end (plus the browser's own end delay, 40 ms here).
  expect(elapsed(log, 'browser-ended', 'wake-start')).toBeLessThan(150);
  expect(log.filter(([what]) => what === 'refused')).toEqual([]);
});

test('a tap while the assistant speaks stops the reply and listens at once (barge-in)', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  const talk = voice.locator('.voiceSpaceTalk');
  await talk.click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await probe(page, (p) => p.say('How is the workspace?'));
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(true);
  await talk.click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(false);
  const log = await probe(page, (p) => p.log);
  expect(log.some(([what]) => what === 'speech-cancelled')).toBe(true);
  expect(elapsed(log, 'tap', 'ui-listening')).toBeLessThan(150);
  expect(elapsed(log, 'tap', 'talk-start')).toBeLessThan(300);
});

test('saying the name over a reply interrupts it, but the reply itself never does', async ({ page, context }) => {
  await context.grantPermissions(['microphone']);
  await mockVoice(page, { wake: 'on', reply: 'I am Layla and all is quiet.' });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
  await probe(page, (p) => p.say('Layla what is running'));
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(true);
  // The speakers playing the reply ("... Layla ...") do not interrupt it.
  await probe(page, (p) => p.say('Layla'));
  await page.waitForTimeout(400);
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  // The person saying the name with something new does.
  await probe(page, (p) => p.say('Layla stop'));
  await expect(voice).toHaveAttribute('data-state', /thinking|speaking/);
  await expect.poll(async () => (await probe(page, (p) => p.log)).filter(([what]) => what === 'speech-cancelled').length).toBeGreaterThan(0);
});

test('leaving the tab stops listening and releases the microphone; coming back waits for a tap', async ({ page, context }) => {
  await context.grantPermissions(['microphone']);
  await mockVoice(page, { wake: 'on' });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await expect.poll(() => probe(page, (p) => p.tracks())).toEqual(['live']);

  await probe(page, (p) => p.hide());
  await expect(voice).toHaveAttribute('data-state', 'idle');
  await expect.poll(() => probe(page, (p) => p.live())).toBe(0);
  await expect.poll(() => probe(page, (p) => p.tracks())).toEqual(['ended']);
  await probe(page, (p) => p.show());
  await page.waitForTimeout(600);
  // Nothing listens again on its own: the page explains and offers the tap.
  expect(await probe(page, (p) => p.live())).toBe(0);
  await expect(voice.getByText('Listening stopped while you were away. Tap the orb to talk.')).toBeVisible();
  await expect(voice.getByRole('button', { name: 'Start listening for “Layla”' })).toBeVisible();
  await voice.getByRole('button', { name: 'Start listening for “Layla”' }).click();
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
});

test('another window taking focus stops listening too, and so does leaving the page', async ({ page, context }) => {
  await context.grantPermissions(['microphone']);
  await mockVoice(page, { wake: 'on' });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await expect.poll(() => probe(page, (p) => p.live())).toBe(1);
  await probe(page, (p) => p.blurWindow());
  await expect.poll(() => probe(page, (p) => p.live())).toBe(0);
  await expect(voice.getByText('Listening stopped while you were away. Tap the orb to talk.')).toBeVisible();
  // Back, tap, listening; then leaving the page for Tasks stops it all.
  await probe(page, (p) => p.show());
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Tasks', exact: true }).click();
  await expect(page.locator('.voiceSpace')).toHaveCount(0);
  await expect.poll(() => probe(page, (p) => p.live())).toBe(0);
  await expect.poll(() => probe(page, (p) => p.tracks().every((state) => state === 'ended'))).toBe(true);
});

test('focus mode shows only the scene and the orb, still talks, and Esc restores the page', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await voice.getByRole('button', { name: 'Full screen' }).click();
  await expect(voice).toHaveAttribute('data-focus', /native|overlay/);
  const mode = await voice.getAttribute('data-focus');
  if (mode === 'native') expect(await page.evaluate(() => document.fullscreenElement?.classList.contains('voiceSpace'))).toBe(true);
  // No buttons but the orb, nothing of the app shell over the scene.
  await expect(voice.getByRole('button')).toHaveCount(1);
  await expect(voice.locator('.voiceSpaceTalk')).toBeFocused();
  const box = (await voice.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect([Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)]).toEqual([0, 0, viewport.width, viewport.height]);
  // The hint fades after about two seconds.
  await expect(voice.locator('.voiceFocusHint')).toHaveText(/Press Esc to exit|Double-tap or swipe down to exit/);
  await expect(voice.locator('.voiceFocusHint')).toHaveCount(0, { timeout: 4000 });
  // Enter on the orb talks; what is heard shows faintly.
  await page.keyboard.press('Enter');
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await probe(page, (p) => p.say('How is the workspace?'));
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect(voice.locator('.voiceFocusCaption')).toContainText('All quiet.');
  await probe(page, (p) => p.endSpeech());
  await page.keyboard.press('Escape');
  await expect(voice).not.toHaveAttribute('data-focus', /.+/);
  expect(await page.evaluate(() => document.fullscreenElement)).toBeNull();
  await expect(voice.locator('.voiceSpaceBar')).toBeVisible();
  await expect(voice.locator('.voiceSpaceBottom')).toBeVisible();
});

test('without element full screen (iPhone Safari) focus mode covers the whole window, and a double tap leaves', async ({ page }) => {
  await page.addInitScript(() => {
    for (const key of ['requestFullscreen', 'webkitRequestFullscreen']) Object.defineProperty(Element.prototype, key, { value: undefined, configurable: true });
  });
  await mockVoice(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await voice.getByRole('button', { name: 'Full screen' }).click();
  await expect(voice).toHaveAttribute('data-focus', 'overlay');
  // The overlay is on top of the sidebar and the header everywhere.
  for (const [x, y] of [[4, 4], [195, 60], [386, 840]]) {
    expect(await page.evaluate(([px, py]) => Boolean(document.elementFromPoint(px, py)?.closest('.voiceSpace')), [x, y])).toBe(true);
  }
  await expect(voice.getByRole('button')).toHaveCount(1);
  await page.mouse.dblclick(30, 700);
  await expect(voice).not.toHaveAttribute('data-focus', /.+/);
  await expect(voice.locator('.voiceSpaceBar')).toBeVisible();
});

test('a swipe down leaves focus mode on a phone', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await mockVoice(page);
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await voice.getByRole('button', { name: 'Full screen' }).click();
  await expect(voice).toHaveAttribute('data-focus', /native|overlay/);
  await expect(voice.locator('.voiceFocusHint')).toHaveText('Double-tap or swipe down to exit');
  await voice.dispatchEvent('pointerdown', { clientX: 60, clientY: 120, pointerType: 'touch' });
  await voice.dispatchEvent('pointerup', { clientX: 64, clientY: 320, pointerType: 'touch' });
  await expect(voice).not.toHaveAttribute('data-focus', /.+/);
  await context.close();
});

test('a call is muted and its reply stopped when the tab is left', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page);
  await page.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
    (window as unknown as { __hide: () => void }).__hide = () => { hidden = true; document.dispatchEvent(new Event('visibilitychange')); };
  });
  await startCall(page);
  await expect(page.getByText('Listening', { exact: true })).toBeVisible();
  await page.evaluate(() => (window as unknown as { __hide: () => void }).__hide());
  await expect(page.getByText('Muted', { exact: true })).toBeVisible();
  await expect(page.getByText('Muted while you were away. Unmute to keep talking.')).toBeVisible();
  await expect(page.locator('.callControls').getByRole('button', { name: 'Unmute microphone' })).toHaveAttribute('aria-pressed', 'true');
});

test('the conversation panel is off by default: the scene, the orb and one line; the choice is remembered', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  const toggle = voice.getByRole('button', { name: 'Conversation' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(voice.locator('.voiceSpacePanel')).toHaveCount(0);
  await expect(voice.locator('.voiceBriefLine')).toContainText('Ready');
  // Voice works fully without the panel: the line follows what is heard and answered.
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice.locator('.voiceBriefLine')).toContainText("I'm listening");
  await probe(page, (p) => p.say('How is the workspace?'));
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect(voice.locator('.voiceBriefLine')).toContainText('All quiet.');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(true);
  await probe(page, (p) => p.endSpeech());
  // Showing the conversation brings the turns and the message box.
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(voice.locator('.voiceSpacePanel .voiceLog')).toContainText('How is the workspace?');
  await expect(voice.getByRole('textbox', { name: 'Your message' })).toBeVisible();
  await page.reload();
  await expect(page.locator('.voiceSpace').getByRole('button', { name: 'Conversation' })).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.voiceSpace').getByRole('button', { name: 'Conversation' }).click();
  await page.reload();
  await expect(page.locator('.voiceSpace .voiceSpacePanel')).toHaveCount(0);
});

test('with the panel hidden, what needs a decision still shows: a proposed task and waiting approvals', async ({ page }) => {
  await mockVoice(page, { reply: 'Create the task?', task: 'draft the weekly report', pending: 2 });
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await voice.locator('.voiceSpaceTalk').click();
  await probe(page, (p) => p.say('Create a task: draft the weekly report'));
  const decision = voice.getByRole('group', { name: 'Waiting for you' });
  await expect(decision).toContainText('Create the task “draft the weekly report”?');
  await expect(decision.getByRole('button', { name: 'Create task' })).toBeVisible();
  await expect(voice.getByRole('button', { name: '2 approvals waiting for you' })).toBeVisible();
  await decision.getByRole('button', { name: 'Not now' }).click();
  await expect(decision).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});

test('a turn shows what was heard at once and how long hearing, answering and speaking took', async ({ page }) => {
  await mockVoice(page);
  await page.addInitScript(() => localStorage.setItem('anum.voice.chat', 'on'));
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await probe(page, (p) => p.say('How is the workspace?'));
  // The recognized words are in the conversation as soon as the turn ends, before the reply.
  await expect(voice.locator('.voiceTurn.you')).toContainText('How is the workspace?');
  await expect(voice).toHaveAttribute('data-state', 'speaking');
  await expect.poll(() => probe(page, (p) => p.speaking())).toBe(true);
  await probe(page, (p) => p.endSpeech());
  const timing = voice.locator('.voiceTurn.assistant .voiceTiming');
  await expect(timing).toHaveText(/^heard \d+\.\d s, answer \d+\.\d s, voice \d+\.\d s\./);
  // The server's side is in the tooltip: the model's first words, the lookups and the total.
  await expect(timing).toHaveAttribute('title', 'first words from the model 0.6 s, lookups 0.1 s (workspace_tasks), server total 0.9 s');
});

test('the EN / Arabic switch is in the bar and recognition listens in the chosen language', async ({ page }) => {
  await mockVoice(page);
  await page.goto('/#voice');
  const voice = page.locator('.voiceSpace');
  const english = voice.getByRole('button', { name: /^EN/ });
  const arabic = voice.getByRole('button', { name: /Speak Arabic/ });
  await expect(english).toHaveAttribute('aria-pressed', 'true');
  await expect(arabic).toBeVisible();
  await arabic.click();
  await expect(arabic).toHaveAttribute('aria-pressed', 'true');
  await expect(english).toHaveAttribute('aria-pressed', 'false');
  await voice.locator('.voiceSpaceTalk').click();
  await expect(voice).toHaveAttribute('data-state', 'listening');
  await expect.poll(async () => (await probe(page, (p) => p.log)).some(([what]) => what === 'lang-ar-SA')).toBe(true);
  // The choice is kept for the next visit, like Voice settings.
  expect(await page.evaluate(() => localStorage.getItem('anum.voice.locale'))).toBe('ar-SA');
});
