import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Web localization (docs/web-localization.md): the browser's language is the default, a choice in
// Settings > Language is saved in this browser, and Arabic lays the interface out right to left.

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test.describe('a browser that asks for Arabic', () => {
  test.use({ locale: 'ar-SA' });

  test('opens in Arabic, right to left, without a saved choice', async ({ page }) => {
    await mockWorkspaceApi(page);
    await page.goto('/#tasks');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.getByRole('heading', { level: 1, name: 'المهام' })).toBeVisible();
    // Arabic plural forms: two tasks loaded uses the dual.
    await expect(page.getByRole('heading', { name: '2 من مهمتين' })).toBeVisible();
    await expect(page.getByLabel('تعليمات المهمة')).toBeVisible();
    // Step types and statuses from the API are labelled too.
    await page.getByRole('button', { name: /Summarize feedback/ }).click();
    await expect(page.getByText('استدعاء النموذج')).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test('mirrors the shell: the sidebar sits on the right on wide screens', async ({ page, isMobile }) => {
    test.skip(isMobile, 'the sidebar becomes a top bar on narrow screens');
    await mockWorkspaceApi(page);
    await page.goto('/#assistant');
    const sidebar = await page.locator('.sidebar').boundingBox();
    const main = await page.getByRole('main').boundingBox();
    expect(sidebar && main && sidebar.x > main.x).toBe(true);
  });
});

test('Settings > Language switches to Arabic, keeps it after a reload, and can follow the browser again', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#settings');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name: 'Language' }).click();
  await expect(page).toHaveURL(/#language$/);
  await expect(page.getByRole('radio', { name: /Match my browser \(English\)/ })).toBeChecked();

  await page.getByRole('radio', { name: 'العربية' }).check();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
  await expect(page.getByRole('heading', { level: 1, name: 'الإعدادات' })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'محفوظة لهذا المتصفح: العربية.' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('anum.locale'))).toBe('ar');

  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('radio', { name: 'العربية' })).toBeChecked();

  await page.getByRole('radio', { name: /مطابقة لغة المتصفح/ }).check();
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('anum.locale'))).toBeNull();
});

test('the Assistant, Approvals, Memory and Privacy read in Arabic', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');

  await page.goto('/#assistant');
  await expect(page.getByRole('heading', { name: 'المحادثات' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'راسل المساعد' })).toHaveAttribute('placeholder', 'اسأل المساعد...');
  await expect(page.getByRole('article', { name: 'المساعد' }).getByText('Approve the announcement')).toBeVisible();

  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: /الموافقات/ }).click();
  const card = page.getByRole('article', { name: /^موافقة على .?external\.action.?$/ });
  await expect(card.getByRole('heading', { name: 'يريد الوكيل استدعاء external.action' })).toBeVisible();
  await expect(card.getByText('0 من موافقتين')).toBeVisible();
  await expect(card.getByText(/خطورة عالية/)).toBeVisible();
  await expect(card.getByRole('button', { name: 'موافقة' })).toBeEnabled();
  // The pending count on the navigation is announced, not just shown.
  await expect(page.getByLabel('موافقة واحدة معلّقة')).toBeVisible();

  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'الذاكرة' }).click();
  await expect(page.getByText(/خاصة بك · من «.?Release planning.?»/)).toBeVisible();
  await expect(page.getByRole('radio', { name: 'من محادثاتي' })).toBeVisible();

  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'الإعدادات' }).click();
  await page.getByRole('navigation', { name: 'أقسام الإعدادات' }).getByRole('button', { name: 'الخصوصية' }).click();
  await expect(page.getByRole('heading', { name: 'ما يخزّنه أنوم عنك' })).toBeVisible();
  await expect(page.getByText('تُحذف بعد 30 يومًا بلا نشاط، أو عندما تحذفها.')).toBeVisible();
  await expect(page.getByText(/بعد 3 أيام/)).toBeVisible();
  // The confirmation phrase stays exactly as the server expects it, inside the Arabic sentence.
  await expect(page.getByText('اكتب DELETE MY DATA للتأكيد')).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
