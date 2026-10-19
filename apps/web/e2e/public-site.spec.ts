import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Public pages (docs/legal-pages.md): legal documents, Trust and status, all without sign-in.
// The e2e build has no VITE_ANUM_LEGAL_PUBLISHED and no OIDC issuer, so every document is a draft
// and the sign-in component reads "Not set up".
const API = 'http://localhost:8000';
const legal = [
  { path: '/legal/terms', title: 'Terms of Service' },
  { path: '/legal/privacy', title: 'Privacy Policy' },
  { path: '/legal/dpa', title: 'Data Processing Addendum' },
  { path: '/legal/aup', title: 'Acceptable Use Policy' },
  { path: '/legal/subprocessors', title: 'Subprocessor List' },
];
const pages = [...legal.map((l) => l.path), '/trust', '/status'];
const viewports = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];

/** Answers GET /health like the API and records every other API call, which a public page must never make. */
async function mockHealth(page: Page, answer: 'ok' | 'down' | 'error' = 'ok'): Promise<string[]> {
  const others: string[] = [];
  await page.route(`${API}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/health') { others.push(url.pathname); return route.fulfill({ status: 401, json: { detail: 'Not signed in' } }); }
    if (answer === 'down') return route.abort('connectionrefused');
    if (answer === 'error') return route.fulfill({ status: 503, json: { detail: 'unavailable' } });
    return route.fulfill({ status: 200, json: { status: 'ok', environment: 'e2e' } });
  });
  return others;
}

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.slice(0, 5).map((n) => n.target.join(' ')).join('\n  ')}`);
}

for (const doc of legal) {
  test(`${doc.path} renders the document with the DRAFT banner, without signing in`, async ({ page }) => {
    const calls = await mockHealth(page);
    await page.goto(doc.path);
    await expect(page.getByRole('heading', { level: 1, name: doc.title })).toBeVisible();
    await expect(page).toHaveTitle(new RegExp(`\\| ANUM$`));
    const banner = page.getByRole('note').filter({ hasText: 'Draft, not yet published' });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('The owner and a lawyer must review this document before it is published.');
    // The source's own DRAFT line is replaced by the banner; owner facts are marked.
    await expect(page.locator('.legalBody')).not.toContainText('DRAFT, owner and a lawyer');
    await expect(page.locator('.legalBody mark.placeholder').first()).toBeVisible();
    await expect(page.getByText('Last updated: {{EFFECTIVE_DATE}}')).toBeVisible();
    // Every section is in the contents list and the list jumps to it.
    const firstEntry = page.locator('.legalToc a').first();
    await expect(firstEntry).toBeVisible();
    const target = (await firstEntry.getAttribute('href'))!;
    await firstEntry.click();
    await expect(page.locator(target)).toBeInViewport();
    await expect(page.getByRole('button', { name: /sign in|continue/i })).toHaveCount(0);
    expect(calls).toEqual([]);
  });
}

test('links between the documents stay on the site', async ({ page }) => {
  await mockHealth(page);
  await page.goto('/legal/terms');
  await page.locator('.legalBody').getByRole('link', { name: 'Data Processing Addendum' }).first().click();
  await expect(page).toHaveURL(/\/legal\/dpa$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Data Processing Addendum' })).toBeVisible();
  await page.getByRole('contentinfo').getByRole('link', { name: 'Subprocessor list' }).click();
  await expect(page).toHaveURL(/\/legal\/subprocessors$/);
  await expect(page.getByRole('table').first()).toBeVisible();
});

test('/trust lists the security facts, the missing certification and the disclosure contact', async ({ page }) => {
  const calls = await mockHealth(page);
  await page.goto('/trust');
  await expect(page.getByRole('heading', { level: 1, name: 'Trust and security' })).toBeVisible();
  for (const heading of ['Each organization’s data is kept apart', 'Encryption in transit and at rest', 'Two-factor sign-in', 'An audit log nobody can edit', 'Nightly backups', 'Export and deletion', 'Security checks on every change', 'Report a vulnerability']) {
    await expect(page.getByRole('heading', { level: 2, name: heading })).toBeVisible();
  }
  await expect(page.getByText('ANUM holds no third-party security certification')).toBeVisible();
  await expect(page.getByText(/gitleaks/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'info@shahzebrahman.com' })).toHaveAttribute('href', 'mailto:info@shahzebrahman.com?subject=Security');
  expect(calls).toEqual([]);
});

test('/status shows every component operational when the API answers', async ({ page }) => {
  await mockHealth(page, 'ok');
  await page.goto('/status');
  await expect(page.getByRole('heading', { level: 2, name: 'All systems operational' })).toBeVisible();
  await expect(page.locator('[data-component="api"]')).toContainText('Operational');
  await expect(page.locator('[data-component="api"]')).toContainText(/Answered in \d+ ms/);
  await expect(page.locator('[data-component="web"]')).toContainText('Operational');
  await expect(page.locator('[data-component="auth"]')).toContainText('Not set up');
  await expect(page.getByText(/Last checked/)).toBeVisible();
});

test('/status says the API cannot be reached, and recovers on Check again', async ({ page }) => {
  let down = true;
  await page.route(`${API}/health`, (route) => down ? route.abort('connectionrefused') : route.fulfill({ status: 200, json: { status: 'ok', environment: 'e2e' } }));
  await page.goto('/status');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('ANUM’s API cannot be reached');
  await expect(alert).toContainText('Signing in and using ANUM will not work until it answers.');
  await expect(page.locator('[data-component="api"]')).toContainText('Not reachable');
  await expect(page.locator('[data-component="web"]')).toContainText('Operational');
  down = false;
  await page.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'All systems operational' })).toBeVisible();
});

test('/status reports an API that answers with an error', async ({ page }) => {
  await mockHealth(page, 'error');
  await page.goto('/status');
  await expect(page.getByRole('alert')).toContainText('ANUM’s API cannot be reached');
  await expect(page.locator('[data-component="api"]')).toContainText('Problem');
  await expect(page.locator('[data-component="api"]')).toContainText('Answered with HTTP 503');
});

test('/status says so when the device is offline', async ({ page, context }) => {
  await mockHealth(page, 'ok');
  await page.goto('/status');
  await expect(page.getByRole('heading', { level: 2, name: 'All systems operational' })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByRole('alert')).toContainText('You are offline');
  await context.setOffline(false);
  await expect(page.getByRole('heading', { level: 2, name: 'All systems operational' })).toBeVisible();
});

test('Arabic: the chrome is right to left and the English-only document says so', async ({ page }) => {
  await mockHealth(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/legal/privacy');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByText('هذا المستند متاح بالإنجليزية فقط.')).toBeVisible();
  await expect(page.locator('.legalBody')).toHaveAttribute('dir', 'ltr');
  await page.getByRole('button', { name: 'اعرض هذه الصفحة بالإنجليزية' }).click();
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.getByRole('note')).toContainText('Draft, not yet published');
});

test('keyboard: the skip link moves focus to the page content', async ({ page }) => {
  await mockHealth(page);
  await page.goto('/trust');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main')).toBeFocused();
});

for (const locale of ['en', 'ar'] as const) {
  for (const path of pages) {
    test(`${path} (${locale}) has no serious or critical axe violations`, async ({ page }) => {
      await mockHealth(page);
      await useSavedLocale(page, locale);
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      if (path === '/status') await expect(page.locator('[data-component="api"]')).toHaveAttribute('data-state', 'operational');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('light theme: the public pages have no serious or critical axe violations', async ({ page }) => {
  test.setTimeout(120_000);
  await mockHealth(page);
  await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
  for (const path of pages) {
    await page.goto(path);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    if (path === '/status') await expect(page.locator('[data-component="api"]')).toHaveAttribute('data-state', 'operational');
    expect(await seriousViolations(page), path).toEqual([]);
  }
});

test('responsive: no horizontal overflow or clipped header at every gate size', async ({ page }) => {
  // 35 page loads: five sizes times seven pages.
  test.setTimeout(180_000);
  await mockHealth(page);
  for (const size of viewports) {
    await page.setViewportSize(size);
    for (const path of pages) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const layout = await page.evaluate(() => {
        const nav = document.querySelector('.publicNav')!.getBoundingClientRect();
        return { scroll: document.documentElement.scrollWidth, width: window.innerWidth, navRight: nav.right, navLeft: nav.left };
      });
      const where = `${path} at ${size.width}x${size.height}`;
      expect(layout.scroll, where).toBeLessThanOrEqual(layout.width);
      expect(layout.navLeft, where).toBeGreaterThanOrEqual(0);
      expect(layout.navRight, where).toBeLessThanOrEqual(layout.width);
      await expect(page.getByRole('link', { name: 'Open ANUM' })).toBeInViewport();
    }
  }
});
