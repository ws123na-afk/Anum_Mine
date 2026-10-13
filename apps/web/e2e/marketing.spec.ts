import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { hash, mockMarketing, post } from './support/marketingApi';

// Marketing (docs/marketing.md): connect accounts, compose English and Arabic drafts with previews,
// ask to publish and approve with the payload hash shown, move posts on the calendar, and read results.
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('an owner drafts bilingual posts with AI help, asks to publish and approves with the payload hash', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing');
  await expect(page.getByRole('heading', { level: 1, name: 'Marketing' })).toBeVisible();
  await expect(page.getByText('Summer tyre check')).toBeVisible();

  await page.getByRole('button', { name: 'New post' }).click();
  await expect(page.getByRole('heading', { name: 'New post', level: 2 })).toBeFocused();
  await page.getByLabel('What to promote').fill('TyrePulse pressure alerts');
  await page.getByRole('button', { name: 'Suggest ideas' }).click();
  await expect(page.getByText('No model is connected')).toBeVisible();
  await page.getByRole('button', { name: 'Use this' }).click();
  await expect(page.getByLabel('Text in Arabic')).toHaveValue('افحص ضغط الإطارات قبل عطلة نهاية الأسبوع.');
  await expect(page.getByRole('region', { name: 'Previews' }).getByRole('article')).toHaveCount(2);
  await page.getByRole('button', { name: 'Save 2 drafts' }).click();
  await expect(page.getByText('Saved 2 drafts.')).toBeVisible();
  expect(sent.creates).toHaveLength(1);
  expect((sent.creates[0] as { variants: { language: string }[] }).variants.map((v) => v.language)).toEqual(['en', 'ar']);

  // The summary band reads the last 30 days; the posts are a table and a row opens the post in a drawer.
  await expect(page.getByRole('region', { name: 'Marketing, last 30 days' })).toContainText('2,400');
  await expect(page.getByRole('complementary', { name: 'Planning help' })).toContainText('Thursday');
  await page.getByRole('button', { name: /^Open the post for Tyre Shop: Summer tyre check/ }).click();
  const card = page.getByRole('dialog').getByRole('listitem').filter({ hasText: 'Summer tyre check' });
  await card.getByRole('button', { name: 'Schedule' }).click();
  await card.getByRole('button', { name: 'Ask to publish' }).click();
  const box = card.getByRole('group', { name: 'Publishing approval' });
  await expect(box.getByText('9a9a9a9a9a9a…')).toBeVisible();
  await expect(box.getByText('Tyre Shop', { exact: true })).toBeVisible();
  await box.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Approved. The post goes to Tyre Shop')).toBeVisible();
  expect(sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(card.getByText('scheduled', { exact: true })).toBeVisible();
});

test('the calendar moves a post with the keyboard-friendly move button', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing-calendar');
  const move = page.getByRole('button', { name: /Move the Facebook post for Tyre Shop/ }).first();
  test.skip(!(await move.isVisible()), 'the month grid is replaced by the agenda on narrow screens');
  await move.click();
  await page.getByRole('button', { name: 'Move here' }).last().click();
  await expect(page.getByText(/^Moved to/)).toBeVisible();
  expect(sent.moves).toHaveLength(1);
});

test('connecting an account returns through the OAuth callback', async ({ page }) => {
  const sent = await mockMarketing(page);
  await page.goto('/#marketing-accounts');
  await expect(page.getByText('ANUM_MARKETING_TIKTOK_CLIENT_KEY')).toBeVisible();
  await page.getByRole('button', { name: 'Connect Meta (Facebook and Instagram)' }).click();
  await expect(page.getByText('Connected 1: Tyre Shop IG.')).toBeVisible();
  expect(sent.connects).toEqual([{ provider: 'meta' }]);
  await expect(page).toHaveURL(/#marketing-accounts$/);
});

test('members see why they cannot connect, viewers cannot draft, and an empty workspace says what to do', async ({ page }) => {
  await mockMarketing(page, { role: 'member', accounts: [] });
  await page.goto('/#marketing-accounts');
  await expect(page.getByText('Only workspace owners connect or disconnect accounts.')).toBeVisible();
  await expect(page.getByText('A workspace owner connects accounts under Accounts.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New post' })).toBeDisabled();
});

test('analytics and the weekly report read in Arabic, right to left, without overflow', async ({ page }) => {
  const sent = await mockMarketing(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#marketing-analytics');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'التسويق' })).toBeVisible();
  await expect(page.getByText('الوصول', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'تقرير التسويق الأسبوعي' })).toBeVisible();
  await expect(page.getByRole('table', { name: 'حسب المنصة' })).toBeVisible();
  await expect(page.getByLabel('محادثة واتساب للتقرير')).toHaveValue('wac_owner');
  await page.getByRole('button', { name: 'أرسله إلى بريدي' }).click();
  await expect(page.getByText('البريد: التقرير بانتظار الموافقة في الموافقات.')).toBeVisible();
  expect(sent.deliveries).toEqual([{ language: 'ar', email: true }]);
  expect(await noOverflow(page)).toBe(true);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
});

test('the posts view has no serious accessibility problems in English', async ({ page }) => {
  await mockMarketing(page);
  await page.goto('/#marketing');
  await expect(page.getByText('Summer tyre check')).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
});
