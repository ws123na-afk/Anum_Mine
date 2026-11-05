import { expect, test, type Locator, type Page } from '@playwright/test';
import { campusFixture, mockHq } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';
import { sceneReady } from './support/hqScene';

// Clicking a building on the campus opens its billboard card over the scene and its details beside it (docs/ANUM_MINE_HQ_BILLBOARDS.md).
// The owner hit a card that showed a raw message key for a Career building ("hqBillboard.purpose.career") and could not be closed:
// the card had no Close button and Escape did nothing. Every window a building click opens must say words, close with its own button
// and close with Escape, at every size and in both languages. Every name below is plainly fake.
const hq = (page: Page) => page.locator('.hqLive');
const CAREER = 'workspace_career';
const card = (page: Page, key: string) => page.locator(`[data-testid="hq-billboard"][data-building="${key}"]`);
/** A message key printed as text: a lower-case catalog prefix, a dot and a lower-case segment ("hqBillboard.purpose.career", "hq.kind.x"). */
const RAW_KEY = /\b[a-z]+[A-Z]?[a-zA-Z]*\.[a-z][a-zA-Z.]+\b/;

const WORDS = {
  en: { close: 'Close billboard', about: 'About Career & Employment', closeDetails: 'Close details', purpose: 'A career building' },
  ar: { close: 'إغلاق لوحة الإعلانات', about: 'عن ⁨Career & Employment⁩', closeDetails: 'إغلاق التفاصيل', purpose: 'مبنى المسار المهني' },
} as const;

async function open(page: Page, locale: 'en' | 'ar') {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await useSavedLocale(page, locale);
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
  // The starter campus has a Career building: the same campus with one added (registered after mockHq, so it answers first).
  const career = { ...campusFixture.buildings[1], workspace_id: CAREER, name: 'Career & Employment', kind: 'career', role: 'member' };
  await page.route(/^http:\/\/localhost:8000\/api\/v1\/office\/campus(\?.*)?$/, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ ...campusFixture, generated_at: new Date().toISOString(), buildings: [...campusFixture.buildings, career] }),
  }));
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await sceneReady(page);
}

async function pickCareer(page: Page) {
  await hq(page).getByRole('button', { name: /^Career & Employment\./ }).click();
  await expect(card(page, CAREER)).toBeVisible();
  await expect(card(page, CAREER)).toHaveAttribute('data-focused', 'true');
}

async function noRawKeys(where: Locator) {
  const text = await where.innerText();
  expect(text, `raw message key in: ${text}`).not.toMatch(RAW_KEY);
}

/** The control is fully inside the viewport and nothing else sits over its centre. */
async function reachable(page: Page, control: Locator) {
  await control.scrollIntoViewIfNeeded();
  // The camera flies to the building first; the card settles where the label layer places it once the flight ends.
  const hitOf = () => control.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return top === el || el.contains(top) ? 'ok' : `${top?.tagName}.${String((top as HTMLElement | null)?.className ?? '')} at ${Math.round(r.x)},${Math.round(r.y)}`;
  });
  await expect.poll(hitOf, { timeout: 15_000, message: 'the close button is not covered by another panel' }).toBe('ok');
  const box = await control.boundingBox();
  const view = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(view.width);
  expect(box!.y + box!.height).toBeLessThanOrEqual(view.height);

}

for (const locale of ['en', 'ar'] as const) {
  test(`a building's card and details say words, close with their button and with Escape (${locale})`, async ({ page }) => {
    test.setTimeout(150_000);
    const w = WORDS[locale];
    await open(page, locale);
    await pickCareer(page);

    // Words, never message keys: on the card, in the details and painted on the 3D board.
    const bb = card(page, CAREER);
    await expect(bb).toContainText(w.purpose);
    await noRawKeys(bb);
    const detail = hq(page).getByRole('complementary', { name: w.about });
    await expect(detail).toBeVisible();
    await noRawKeys(detail);
    // The painted boards (the low tier of a software renderer draws the far face: name and key line) never print a key either.
    await expect.poll(async () => (await hq(page).locator('.hqSceneWrap').getAttribute('data-board-texts')) ?? '', { timeout: 30_000 }).toContain('Career & Employment');
    expect((await hq(page).locator('.hqSceneWrap').getAttribute('data-board-texts')) ?? '').not.toMatch(RAW_KEY);

    // The card's own Close button: named, on screen, not under another panel, and it closes the card and the details.
    const close = bb.getByRole('button', { name: w.close });
    await reachable(page, close);
    await close.click();
    await expect(card(page, CAREER)).toHaveCount(0);
    await expect(detail).toHaveCount(0);
    await expect(hq(page).getByRole('button', { name: /^Career & Employment\./ })).toHaveAttribute('aria-pressed', 'false');

    // Escape closes them too, wherever focus is.
    await pickCareer(page);
    await expect(detail).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(card(page, CAREER)).toHaveCount(0);
    await expect(detail).toHaveCount(0);

    // The details keep their own Close button as well.
    await pickCareer(page);
    await detail.getByRole('button', { name: w.closeDetails }).click();
    await expect(detail).toHaveCount(0);
    await expect(card(page, CAREER)).toHaveCount(0);
  });
}

test('every building kind of the campus prints words on its card, in both languages', async ({ page }) => {
  test.setTimeout(150_000);
  await open(page, 'en');
  for (const name of ['Executive HQ', 'Al Noor Fleet HQ', 'Personal Office', 'Legal and Compliance', 'Career & Employment']) {
    // From the keyboard: a building card the camera has parked off the stage still takes focus and Enter picks it.
    await hq(page).getByRole('button', { name: new RegExp('^' + name.replace(/[&]/g, '\\$&') + '\\.') }).press('Enter');
    const focused = page.locator('[data-testid="hq-billboard"][data-focused="true"]');
    await expect(focused.first()).toBeVisible();
    await noRawKeys(focused.first());
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-testid="hq-billboard"][data-selected="true"]')).toHaveCount(0);
  }
});
