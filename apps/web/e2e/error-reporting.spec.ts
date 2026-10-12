import { expect, test, type Page } from '@playwright/test';

// Browser error reporting (docs/observability.md#error-reporting). The build on :4175 has
// VITE_SENTRY_DSN=https://examplekey@errors.anum.test/7 (playwright.config.ts); the default build
// on :4173 has none. The error server is replaced by route interception.
const reportingOrigin = process.env.ANUM_E2E_SENTRY_ORIGIN ?? 'http://127.0.0.1:4175';
const errorsHost = 'https://errors.anum.test';

async function mockApi(page: Page) {
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks' || path === '/api/v1/memories' || path === '/api/v1/conversations') return json([]);
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: true, updated_at: null, conversation_memory: 'on' });
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'anum-local-hash-v1', indexed: 0, failed: 0, skipped: 0, stale: 0, chunks: 0, sources: [] });
    return route.abort();
  });
}

/** The JSON items of a Sentry envelope (newline-delimited: envelope header, then item header and payload pairs). */
function envelopeItems(body: string): Record<string, any>[] {
  const lines = body.split('\n').filter(Boolean).map((line) => JSON.parse(line) as Record<string, any>);
  const items: Record<string, any>[] = [];
  for (let index = 1; index + 1 < lines.length; index += 2) items.push({ ...lines[index + 1], __type: lines[index].type });
  return items;
}

/** The event of the error the test throws from a timer (caught by the SDK's setTimeout wrapper or
 * window.onerror), not of other failures on the page such as a refused fetch. */
function thrown(body: string): Record<string, any> | undefined {
  return envelopeItems(body).find((item) => item.__type === 'event' && /setTimeout|onerror/.test(String(item.exception?.values?.[0]?.mechanism?.type ?? '')));
}

test('an unhandled error is reported scrubbed, with the tenant tag only', async ({ page }) => {
  const envelopes: { url: string; body: string }[] = [];
  await page.route(`${errorsHost}/**`, async (route) => {
    envelopes.push({ url: route.request().url(), body: route.request().postData() ?? '' });
    await route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: '{}' });
  });
  await mockApi(page);
  await page.goto(`${reportingOrigin}/?code=example&state=example#memory`);
  await expect(page.getByRole('heading', { level: 1, name: 'Knowledge Base' })).toBeVisible();

  // The SDK loads after the first render; throw until it reports (each error differs, so Dedupe keeps it).
  let attempt = 0;
  await expect.poll(async () => {
    attempt += 1;
    await page.evaluate((n) => { setTimeout(() => { throw new TypeError(`My secret note ${n} for someone@example.org`); }, 0); }, attempt);
    await page.waitForTimeout(200);
    return envelopes.filter((e) => thrown(e.body)).length;
  }, { timeout: 15_000 }).toBeGreaterThan(0);

  const { url, body } = envelopes.find((e) => thrown(e.body))!;
  expect(new URL(url).pathname).toBe('/api/7/envelope/');
  const event = thrown(body);
  expect(event).toBeTruthy();
  expect(event!.exception.values[0].type).toBe('TypeError');
  expect(event!.exception.values[0].value).toBe('[redacted]');
  expect(event!.tags).toEqual({ 'anum.tenant_id': 'tenant_local' });
  expect(event!.environment).toBe('e2e');
  expect(event!.request).toEqual({ url: `${reportingOrigin}/` });
  expect(event!.user).toBeUndefined();
  for (const frame of event!.exception.values[0].stacktrace?.frames ?? []) {
    expect(frame.context_line).toBeUndefined();
    expect(frame.vars).toBeUndefined();
    expect(String(frame.filename ?? '')).not.toContain('?');
  }
  for (const leak of ['secret note', 'someone@example.org', 'code=example', 'state=example', 'User-Agent']) {
    expect(body).not.toContain(leak);
  }
  // The DSN's public key is public by design (it is in the bundle); nothing else identifies anyone.
  // No session tracking, no tracing: only error events go out.
  expect(envelopes.flatMap((e) => envelopeItems(e.body)).map((item) => item.__type).filter((type) => type !== 'event' && type !== 'client_report')).toEqual([]);
});

test('a build without VITE_SENTRY_DSN loads no reporter and sends nothing', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await mockApi(page);
  await page.goto('/#memory');
  await expect(page.getByRole('heading', { level: 1, name: 'Knowledge Base' })).toBeVisible();
  await page.evaluate(() => { setTimeout(() => { throw new Error('unreported'); }, 0); });
  await page.waitForTimeout(500);
  expect(requests.filter((url) => url.includes('errors.anum.test') || url.includes('errorReportingStart'))).toEqual([]);
});
