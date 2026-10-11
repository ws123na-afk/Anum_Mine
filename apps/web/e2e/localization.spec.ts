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

test('the Assistant, Approvals, Memory, Notifications and Privacy read in Arabic', async ({ page }) => {
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
  // Settings › Notifications: the switches, phone push and the registered phone, in Arabic.
  await page.getByRole('navigation', { name: 'أقسام الإعدادات' }).getByRole('button', { name: 'الإشعارات' }).click();
  await expect(page).toHaveURL(/#notifications$/);
  await expect(page.getByRole('checkbox', { name: 'إشعارات الهاتف' })).toBeChecked();
  const push = page.getByRole('region', { name: 'إشعارات الهاتف' });
  await expect(push.getByText('مفعّلة', { exact: true })).toBeVisible();
  await expect(push.getByText(/^هاتف واحد يتلقى الإشعارات/)).toBeVisible();
  await expect(push.getByRole('list', { name: 'هواتفي المسجّلة' }).getByText(/Pixel 8.*Android.?، ينتهي بـ .?1a2b/)).toBeVisible();
  await expect(push.getByRole('button', { name: /^إزالة / })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);

  await page.getByRole('navigation', { name: 'أقسام الإعدادات' }).getByRole('button', { name: 'الخصوصية' }).click();
  await expect(page.getByRole('heading', { name: 'ما يخزّنه أنوم عنك' })).toBeVisible();
  await expect(page.getByText('تُحذف بعد 30 يومًا بلا نشاط، أو عندما تحذفها.')).toBeVisible();
  await expect(page.getByText(/بعد 3 أيام/)).toBeVisible();
  // The confirmation phrase stays exactly as the server expects it, inside the Arabic sentence.
  await expect(page.getByText('اكتب DELETE MY DATA للتأكيد')).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('the audit log and this workspace read in Arabic, right to left, without horizontal overflow', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#audit');
  await expect(page.getByRole('heading', { level: 1, name: 'سجل التدقيق' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'سجل واحد' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'تطبيق عوامل التصفية' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.goto('/#settings');
  await page.reload();
  await expect(page.getByRole('region', { name: 'مساحة العمل هذه' }).getByRole('heading', { name: 'مساحة العمل هذه' })).toBeVisible();

test('Files reads in Arabic, right to left, with Arabic plurals', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#files');
  await expect(page.getByRole('heading', { name: 'الملفات', level: 2 })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'المستخدم من تخزين مساحة العمل' })).toHaveAttribute('aria-valuetext', /· ملفان$/);
  const list = page.getByRole('region', { name: 'الملفات' });
  await expect(list.getByText('مفهرس', { exact: true })).toBeVisible();
  await expect(list.getByText('غير مفهرس', { exact: true })).toBeVisible();
  await list.getByRole('button', { name: /^عرض تفاصيل .?release-notes\.md.?$/ }).click();
  const detail = page.locator('.fileDetail');
  await expect(detail.getByText('تستطيع المهام استخدام هذا الملف (3 مقاطع).')).toBeVisible();
  await expect(detail.getByRole('region', { name: 'معاينة' }).getByRole('heading', { name: 'Release notes' })).toBeVisible();

test('Integrations, Skills and the marketplace read in Arabic, right to left, without overflow', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');

  await page.goto('/#integrations');
  await expect(page.getByRole('heading', { level: 2, name: 'التكاملات' })).toBeVisible();
  await expect(page.getByText('طريقة الإصلاح:')).toBeVisible();
  await expect(page.getByText(/2 متصلة · 1 تحتاج إلى انتباه · 1 غير مستخدمة/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'اختبار الاتصال' }).first()).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);

  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'المهارات' }).click();
  await expect(page.getByLabel('البحث في المهارات')).toBeVisible();
  // Arabic plural forms: two skills uses the dual.
  await expect(page.getByText('2 من مهارتين')).toBeVisible();
  await page.locator('#skill-acme-release-notes').getByRole('button', { name: 'التفاصيل' }).click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: 'الإصدارات' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.keyboard.press('Escape');

  await page.getByRole('navigation', { name: 'التنقل الرئيسي' }).getByRole('button', { name: 'المؤسسة' }).click();
  await expect(page.getByRole('heading', { name: 'كتالوج السوق' })).toBeVisible();
  await expect(page.getByText(/2 من حزمتين · 1 مثبّتة/)).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});
