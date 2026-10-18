import { expect as baseExpect, test, type Browser, type BrowserContext, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { CallRelay, type Teammate } from './support/appCallsRelay';

// In-app calls between teammates (docs/in-app-calls.md). Two browser contexts are two people;
// Chromium's fake microphone and camera feed real WebRTC peer connections between them, and
// CallRelay stands in for the API's signaling.
// ANUM_E2E_CHROMIUM points a sandbox without Playwright's own browser at a local Chromium; CI leaves it unset.
test.use({
  launchOptions: {
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
    ...(process.env.ANUM_E2E_CHROMIUM ? { executablePath: process.env.ANUM_E2E_CHROMIUM } : {}),
  },
});

// Ringing crosses two browsers and a long-poll relay: allow for a busy CI runner.
const expect = baseExpect.configure({ timeout: 15_000 });

const OMAR: Teammate = { user_id: 'user_local', name: 'Omar Owner' };
const AHMED: Teammate = { user_id: 'user_ahmed', name: 'Ahmed Saleh' };

async function person(browser: Browser, relay: CallRelay, me: Teammate, peers: Teammate[], hash: string, options: { denyMedia?: boolean; locale?: 'ar' } = {}): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ permissions: ['microphone', 'camera'] });
  const page = await context.newPage();
  if (options.locale) await useSavedLocale(page, options.locale);
  if (options.denyMedia) {
    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' }));
    });
  }
  await mockWorkspaceApi(page);
  await relay.attach(page, me, peers);
  await page.goto(hash);
  return { context, page };
}

async function openPeople(page: Page) {
  await page.goto('/#team');
  await page.getByRole('button', { name: 'People', exact: true }).click();
}

test('a teammate is rung, answers, both hear each other, mute works and hanging up ends it for both', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  const ahmed = await person(browser, relay, AHMED, [OMAR], '/#home');
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true }).click();
  const outgoing = omar.page.getByRole('dialog', { name: 'Ahmed Saleh' });
  await expect(outgoing.getByTestId('call-status')).toHaveText('Ringing');

  const incoming = ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' });
  await expect(incoming).toContainText('Voice call · is calling you');
  await incoming.getByRole('button', { name: 'Answer', exact: true }).click();

  // Real media: the timer starts only once the peer connection is connected.
  await expect(outgoing.getByTestId('call-status')).toHaveText(/^\d:\d\d$/, { timeout: 20_000 });
  const ahmedCall = ahmed.page.getByRole('dialog', { name: 'Omar Owner' });
  await expect(ahmedCall.getByTestId('call-status')).toHaveText(/^\d:\d\d$/, { timeout: 20_000 });
  const mute = outgoing.getByRole('button', { name: 'Mute' });
  await mute.click();
  await expect(outgoing.getByRole('button', { name: 'Unmute' })).toHaveAttribute('aria-pressed', 'true');
  await expect(outgoing.getByRole('button', { name: 'Speaker off' })).toHaveAttribute('aria-pressed', 'true');

  await outgoing.getByRole('button', { name: 'Hang up' }).click();
  await expect(omar.page.getByRole('dialog', { name: 'Ahmed Saleh' }).getByRole('status')).toHaveText('Call ended');
  await expect(ahmed.page.getByRole('dialog', { name: 'Omar Owner' }).getByRole('status')).toHaveText('Call ended');
  expect(relay.ended).toEqual(['call_1:completed']);
  // Signaling carried a real offer and answer.
  expect(relay.signals.map((s) => s.kind)).toEqual(expect.arrayContaining(['offer', 'answer']));

  await omar.page.getByRole('dialog', { name: 'Ahmed Saleh' }).getByRole('button', { name: 'Close' }).click();
  await omar.page.getByRole('button', { name: 'Calls', exact: true }).click();
  const history = omar.page.getByRole('list', { name: 'Calls' });
  await expect(history.getByRole('listitem')).toHaveCount(1);
  await expect(history).toContainText('Ahmed Saleh');
  await expect(history).toContainText('Call ended');
  await omar.context.close(); await ahmed.context.close();
});

test('a video call shows both cameras and the camera can be turned off', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  const ahmed = await person(browser, relay, AHMED, [OMAR], '/#home');
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Video call Ahmed Saleh' }).click();
  const incoming = ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' });
  await incoming.getByRole('button', { name: 'Answer with video' }).click();
  const call = omar.page.getByRole('dialog', { name: 'Ahmed Saleh' });
  await expect(call.getByTestId('call-status')).toHaveText(/^\d:\d\d$/, { timeout: 20_000 });
  const remote = call.getByLabel('Video from Ahmed Saleh');
  await expect.poll(() => remote.evaluate((video: HTMLVideoElement) => video.videoWidth), { timeout: 15_000 }).toBeGreaterThan(0);
  await call.getByRole('button', { name: 'Turn camera off' }).click();
  await expect(call.getByRole('button', { name: 'Turn camera on' })).toHaveAttribute('aria-pressed', 'true');
  await call.getByRole('button', { name: 'Hang up' }).click();
  await omar.context.close(); await ahmed.context.close();
});

test('a declined call tells the caller, and the callee is not left ringing', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  const ahmed = await person(browser, relay, AHMED, [OMAR], '/#home');
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true }).click();
  await ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' }).getByRole('button', { name: 'Decline' }).click();
  await expect(ahmed.page.getByRole('alertdialog')).toHaveCount(0);
  const ended = omar.page.getByRole('dialog', { name: 'Ahmed Saleh' });
  await expect(ended.getByRole('status')).toHaveText('Declined');
  await expect(ended).toContainText('Ahmed Saleh declined the call.');
  expect(relay.ended).toEqual(['call_1:declined']);
  await omar.context.close(); await ahmed.context.close();
});

test('cancelling while it rings leaves the callee a missed call', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  const ahmed = await person(browser, relay, AHMED, [OMAR], '/#team');
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true }).click();
  await expect(ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' })).toBeVisible();
  await omar.page.getByRole('dialog', { name: 'Ahmed Saleh' }).getByRole('button', { name: 'Hang up' }).click();
  await expect(ahmed.page.getByRole('alertdialog')).toHaveCount(0);
  await ahmed.page.getByRole('button', { name: 'Calls', exact: true }).click();
  await ahmed.page.getByRole('button', { name: 'Missed', exact: true }).click();
  const history = ahmed.page.getByRole('list', { name: 'Calls' });
  await expect(history).toContainText('Omar Owner');
  await expect(history).toContainText('Missed call');
  await expect(history.getByRole('button', { name: 'Call Omar Owner', exact: true })).toBeEnabled();
  await omar.context.close(); await ahmed.context.close();
});

test('a refused microphone explains how to allow it and never rings anyone', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team', { denyMedia: true });
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true }).click();
  await expect(omar.page.getByRole('status').filter({ hasText: 'ANUM cannot use your microphone or camera' })).toBeVisible();
  expect(relay.calls).toHaveLength(0);
  await omar.context.close();
});

test('with calls off the buttons are disabled and the Calls tab says who can turn them on', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await page.getByRole('button', { name: 'People', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Calls', exact: true }).click();
  await expect(page.getByText('Calls are off on this server. Ask the person who runs ANUM to set up the calls relay.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No calls yet' })).toBeVisible();
});

test('offline: placing a call says it needs a connection', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  await openPeople(omar.page);
  await omar.context.setOffline(true);
  await expect(omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true })).toBeDisabled();
  await omar.page.getByRole('button', { name: 'Calls', exact: true }).click();
  await expect(omar.page.getByText('You are offline. Calls need an internet connection.')).toBeVisible();
  await omar.context.close();
});

test('the incoming call screen in Arabic', async ({ browser }) => {
  const relay = new CallRelay();
  const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
  const ahmed = await person(browser, relay, AHMED, [OMAR], '/#home', { locale: 'ar' });
  await openPeople(omar.page);
  await omar.page.getByRole('button', { name: 'Call Ahmed Saleh', exact: true }).click();
  const incoming = ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' });
  await expect(incoming).toContainText('مكالمة صوتية · يتصل بك');
  await expect(incoming.getByRole('button', { name: 'رد', exact: true })).toBeVisible();
  await expect(ahmed.page.locator('html')).toHaveAttribute('dir', 'rtl');
  await incoming.getByRole('button', { name: 'رفض' }).click();
  await omar.context.close(); await ahmed.context.close();
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`the call screens fit ${width}x${height} without horizontal scroll`, async ({ browser }) => {
    const relay = new CallRelay();
    const omar = await person(browser, relay, OMAR, [AHMED], '/#team');
    const ahmed = await person(browser, relay, AHMED, [OMAR], '/#home');
    for (const page of [omar.page, ahmed.page]) await page.setViewportSize({ width, height });
    await openPeople(omar.page);
    await omar.page.getByRole('button', { name: 'Video call Ahmed Saleh' }).click();
    const incoming = ahmed.page.getByRole('alertdialog', { name: 'Omar Owner' });
    await expect(incoming).toBeVisible();
    for (const page of [omar.page, ahmed.page]) {
      const { scroll, inner } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
      expect(scroll).toBeLessThanOrEqual(inner);
      for (const button of await page.locator('.appCallPanel button').all()) {
        const box = await button.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width);
        expect(box!.y + box!.height).toBeLessThanOrEqual(height);
      }
    }
    await incoming.getByRole('button', { name: 'Decline' }).click();
    await omar.context.close(); await ahmed.context.close();
  });
}
