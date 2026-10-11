import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { fakeMedia, mockCallApi, startCall } from './support/callMocks';

const call = (page: Page) => page.evaluate(() => (window as unknown as { __call: { cancels: number; spoken: string[] } }).__call);

test('a spoken turn is answered and spoken; talking over the reply stops it', async ({ page }) => {
  const mock = await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  await expect(page.getByText('Listening', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Call time')).toHaveText(/^\d+:\d\d$/);

  // Speak: loud input starts the turn, the recognizer hears the words, silence ends the turn.
  await page.evaluate(() => (window as unknown as { __loud: (on: boolean) => void }).__loud(true));
  await expect(page.getByText("Hearing you", { exact: true })).toBeVisible();
  await page.evaluate(() => (window as unknown as { __say: (text: string) => void }).__say("What's the status?"));
  await page.waitForTimeout(400);
  await page.evaluate(() => (window as unknown as { __loud: (on: boolean) => void }).__loud(false));
  await expect.poll(() => mock.asked).toEqual(["What's the status?"]);
  await expect(page.getByText('Speaking', { exact: true })).toBeVisible();
  await expect.poll(async () => (await call(page)).spoken[0]).toBe('You have two tasks and one is running right now.');

  // Barge-in: talking while the reply plays cancels the speech within the target.
  const before = (await call(page)).cancels;
  await page.evaluate(() => (window as unknown as { __loud: (on: boolean) => void }).__loud(true));
  await expect.poll(async () => (await call(page)).cancels, { timeout: 2000 }).toBeGreaterThan(before);
  await expect(page.getByText('Hearing you', { exact: true })).toBeVisible();
  await page.evaluate(() => (window as unknown as { __loud: (on: boolean) => void }).__loud(false));

  // The transcript panel shows both sides; mute is a pressed toggle.
  await page.getByRole('button', { name: 'Show transcript' }).click();
  const transcript = page.getByRole('complementary', { name: 'Transcript' });
  await expect(transcript.getByText("What's the status?")).toBeVisible();
  await page.locator('.callControls').getByRole('button', { name: 'Mute microphone' }).click();
  await expect(page.locator('.callControls').getByRole('button', { name: 'Unmute microphone' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Muted', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('voice proposes tasks and never approves; typing works in the call', async ({ page }) => {
  const mock = await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  await page.getByRole('button', { name: 'Show transcript' }).click();
  const box = page.getByRole('textbox', { name: 'Message' });
  await box.fill('Create a task: call the tyre supplier');
  await box.press('Enter');
  await expect(page.getByRole('button', { name: 'Create task' })).toBeVisible();
  await page.getByRole('button', { name: 'Create task' }).click();
  await expect(page.getByText('Task created', { exact: true })).toBeVisible();
  await box.fill('Approve the invoice');
  await box.press('Enter');
  await expect(page.getByRole('button', { name: 'Open Approvals' })).toBeVisible();
  expect(mock.asked).toEqual(['Create a task: call the tyre supplier', 'call the tyre supplier', 'Approve the invoice']);
});

test('the camera shows a clear indicator and sends a frame only when asked', async ({ page }) => {
  const mock = await mockCallApi(page, { vision: true });
  await fakeMedia(page);
  await startCall(page);
  await page.getByRole('button', { name: 'Turn camera on' }).click();
  await expect(page.getByText('Camera on', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Camera preview')).toBeVisible();
  await page.waitForTimeout(300);
  expect(mock.looked).toBe(0);
  await page.getByRole('button', { name: 'Send frame' }).click();
  await expect.poll(() => mock.looked).toBe(1);
  expect(mock.frames[0]).toBe('/9j/4AAQ');
  expect(mock.asked).toEqual(['What do you see?']);
  await expect(page.getByRole('link', { name: 'Save last frame to this device' })).toBeVisible();
  await page.getByRole('button', { name: 'Turn camera off' }).click();
  await expect(page.getByText('Camera on', { exact: true })).toHaveCount(0);
});

test('without a vision model the camera explains how to get one', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  await page.getByRole('button', { name: 'Turn camera on' }).click();
  await expect(page.getByRole('alert')).toContainText('Camera answers need a vision model');
  await expect(page.getByText('Camera on', { exact: true })).toHaveCount(0);
});

test('ending the call saves a summary as a conversation, or keeps nothing', async ({ page }) => {
  const mock = await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  await page.getByRole('button', { name: 'Show transcript' }).click();
  await page.getByRole('textbox', { name: 'Message' }).fill("What's the status?");
  await page.getByRole('textbox', { name: 'Message' }).press('Enter');
  await expect(page.getByRole('complementary', { name: 'Transcript' }).getByText(/You have two tasks/)).toBeVisible();
  await page.getByRole('button', { name: 'End call' }).click();
  await expect(page.getByRole('dialog', { name: 'Save this call?' })).toBeVisible();
  await page.getByRole('button', { name: 'Save summary' }).click();
  await expect(page.getByText('- Asked about the workspace status.')).toBeVisible();
  expect(mock.summaries).toEqual([{ save: true }]);
  expect(mock.completed).toBe(1);
  await page.getByRole('button', { name: 'Open conversation' }).click();
  await expect(page).toHaveURL(/#assistant/);
});

test('a blocked microphone, a model failure and a browser without recognition are explained', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page, { denyMic: true });
  await page.goto('/#call');
  await page.getByRole('button', { name: 'Start call' }).click();
  await expect(page.getByRole('alert')).toContainText('The microphone is blocked');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('model failures and typing-only browsers are explained during the call', async ({ page }) => {
  await mockCallApi(page, { failAsk: true });
  await fakeMedia(page, { noRecognition: true });
  await startCall(page);
  await expect(page.getByRole('alert')).toContainText('This browser has no speech recognition');
  const box = page.getByRole('textbox', { name: 'Message' });
  await box.fill('Hello');
  await box.press('Enter');
  await expect(page.getByRole('alert')).toContainText('I could not answer. Try again in a moment.');
});

test('the call works in Arabic', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#call');
  await expect(page.getByLabel('لغة المكالمة')).toHaveValue('ar-SA');
  await page.getByRole('button', { name: 'ابدأ المكالمة' }).click();
  await expect(page.getByRole('button', { name: 'إنهاء المكالمة' })).toBeVisible();
  await expect(page.getByText('يستمع', { exact: true })).toBeVisible();
});

test('the call screen with the transcript open has no serious accessibility violations', async ({ page }) => {
  await mockCallApi(page);
  await fakeMedia(page);
  await startCall(page);
  await page.getByRole('button', { name: 'Show transcript' }).click();
  await page.getByRole('button', { name: 'More call details' }).click();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
});
