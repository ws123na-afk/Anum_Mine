import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { openHq } from './support/hqApi';

// F14 security: the scene must run under the REAL production Content-Security-Policy (the one
// nginx sends, apps/web/nginx/default.conf.template) without a single violation, so the office never
// needs a looser policy. vite preview sends no CSP, so the spec adds the production one to the page.
function productionCsp(): string {
  const template = readFileSync(new URL('../nginx/default.conf.template', import.meta.url), 'utf8');
  const match = /set \$csp "([^"]+)";/.exec(template);
  if (!match) throw new Error('CSP not found in nginx template');
  return match[1]
    .replace('${ANUM_CSP_CONNECT_SRC}', 'http://localhost:8000')
    .replace('__ANUM_CSP_ERROR_REPORTING_SRC__', '');
}

test('the headquarters scene runs under the production CSP with no violation', async ({ page }) => {
  const csp = productionCsp();
  await page.route('**/*', async (route) => {
    const request = route.request();
    if (request.resourceType() !== 'document') return route.fallback();
    const response = await route.fetch();
    return route.fulfill({ response, headers: { ...response.headers(), 'content-security-policy': csp } });
  });
  const violations: string[] = [];
  await page.exposeFunction('reportViolation', (text: string) => violations.push(text));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => {
      (window as unknown as { reportViolation: (t: string) => void }).reportViolation(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
  await openHq(page);
  await expect(page.locator('canvas.hqCanvas')).toBeVisible();
  await page.getByRole('button', { name: 'Live workstations' }).click();
  await expect(page.getByRole('dialog', { name: 'Live Employee Workstations' })).toBeVisible();
  expect(violations).toEqual([]);
  // Control: the policy really is active, a forbidden connection is reported.
  await page.evaluate(() => fetch('https://blocked.example.test/x').catch(() => undefined));
  await expect.poll(() => violations.length).toBe(1);
});
