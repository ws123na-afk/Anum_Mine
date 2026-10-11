// Unit tests for the browser error-report scrubbing in src/lib/errorReporting.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { DsnError, REDACTED, TENANT_TAG, dsnOrigin, scrubBreadcrumb, scrubEvent, stripUrl, urlCarriesToken } from '../src/lib/errorReporting.ts';

describe('dsnOrigin', () => {
  test('returns the origin for the CSP', () => {
    assert.equal(dsnOrigin('https://examplekey@errors.example.org/3'), 'https://errors.example.org');
    assert.equal(dsnOrigin(' https://examplekey@errors.example.org:8443/sub/7/ '), 'https://errors.example.org:8443');
    assert.equal(dsnOrigin('http://examplekey@localhost:8000/1'), 'http://localhost:8000');
  });
  test('refuses malformed and plain-http DSNs without echoing them', () => {
    for (const bad of ['', 'not a url', 'https://errors.example.org/3', 'https://examplekey@errors.example.org/project', 'https://examplekey@errors.example.org/3?x=1', 'http://examplekey@errors.example.org/3', 'ftp://examplekey@errors.example.org/3', 'https://examplekey:pw@errors.example.org/3']) {
      assert.throws(() => dsnOrigin(bad), (error: unknown) => error instanceof DsnError && !(error as Error).message.includes('examplekey'), bad);
    }
  });
});

describe('stripUrl and urlCarriesToken', () => {
  test('drops query strings, fragments and credentials', () => {
    assert.equal(stripUrl('https://app.example.org/?code=abc&state=xyz#invitation=tok'), 'https://app.example.org/');
    assert.equal(stripUrl('https://user:pw@api.example.org:8443/api/v1/tasks?query=secret'), 'https://api.example.org:8443/api/v1/tasks');
    assert.equal(stripUrl('/assets/index-abc.js?v=1'), '/assets/index-abc.js');
    assert.equal(stripUrl(undefined), undefined);
  });
  test('spots tokens in URLs', () => {
    assert.ok(urlCarriesToken('https://app.example.org/#invitation=example'));
    assert.ok(urlCarriesToken('https://app.example.org/?code=example&state=example'));
    assert.ok(urlCarriesToken('https://auth.example.org/realms/anum/protocol/openid-connect/token'));
    assert.ok(!urlCarriesToken('https://api.example.org/api/v1/tasks'));
  });
});

describe('scrubBreadcrumb', () => {
  test('keeps method, status and the URL path of network breadcrumbs', () => {
    const crumb = scrubBreadcrumb({ type: 'http', category: 'fetch', level: 'info', timestamp: 1, message: 'GET', data: { method: 'GET', url: 'https://api.example.org/api/v1/memories?query=my+name', status_code: 200, request_body_size: 40, __span: 'x' } });
    assert.deepEqual(crumb, { type: 'http', category: 'fetch', level: 'info', timestamp: 1, data: { method: 'GET', url: 'https://api.example.org/api/v1/memories', status_code: 200 } });
  });
  test('drops breadcrumbs whose URL carried a token, and every message', () => {
    assert.equal(scrubBreadcrumb({ category: 'navigation', data: { from: '/', to: '/?code=example&state=example' } }), null);
    assert.equal(scrubBreadcrumb({ category: 'xhr', data: { method: 'POST', url: 'https://auth.example.org/realms/anum/protocol/openid-connect/token' } }), null);
    assert.deepEqual(scrubBreadcrumb({ category: 'console', level: 'error', message: 'My password is hunter2', data: { arguments: ['x'] } }), { category: 'console', level: 'error' });
    assert.deepEqual(scrubBreadcrumb({ category: 'ui.click', message: 'button[aria-label="Open chat Private title"]' }), { category: 'ui.click' });
  });
});

describe('scrubEvent', () => {
  const event = () => ({
    event_id: 'e1',
    message: 'ANUM web error reporting test',
    user: { id: 'user_1', email: 'someone@example.org', ip_address: '{{auto}}' },
    request: { url: 'https://app.example.org/?code=example#memory', headers: { 'User-Agent': 'x', Referer: 'https://app.example.org/?code=example' }, cookies: 'a=b', data: 'body' },
    exception: { values: [{ type: 'TypeError', value: 'Cannot read "My secret note"', mechanism: { type: 'onerror', handled: false }, stacktrace: { frames: [{ filename: 'https://app.example.org/assets/index.js?x=1', abs_path: 'https://app.example.org/assets/index.js?x=1', function: 'load', lineno: 3, colno: 9, context_line: 'const secret = 1', pre_context: ['a'], post_context: ['b'], vars: { secret: 'x' } }] } }] },
    contexts: { browser: { name: 'Chrome' }, os: { name: 'Linux' }, trace: { trace_id: 't' }, state: { redux: 'x' }, culture: { locale: 'en' } },
    extra: { prompt: 'secret' },
    tags: { other: 'x' },
    breadcrumbs: [
      { category: 'fetch', data: { method: 'GET', url: 'https://api.example.org/api/v1/tasks?x=1', status_code: 500 } },
      { category: 'navigation', data: { from: '/', to: '/#invitation=example' } },
      { category: 'console', message: 'secret' },
    ],
    transaction: '/?code=example',
  });

  test('keeps types, frames, the page path and the tenant tag only', () => {
    const out = scrubEvent(event(), 'tenant_example') as Record<string, any>;
    assert.equal(out.user, undefined);
    assert.equal(out.extra, undefined);
    assert.deepEqual(out.request, { url: 'https://app.example.org/' });
    assert.equal(out.transaction, '/');
    assert.deepEqual(out.tags, { [TENANT_TAG]: 'tenant_example' });
    assert.deepEqual(Object.keys(out.contexts).sort(), ['browser', 'os', 'trace']);
    const value = out.exception.values[0];
    assert.equal(value.type, 'TypeError');
    assert.equal(value.value, REDACTED);
    assert.deepEqual(value.stacktrace.frames[0], { filename: 'https://app.example.org/assets/index.js', abs_path: 'https://app.example.org/assets/index.js', function: 'load', lineno: 3, colno: 9 });
    assert.deepEqual(out.breadcrumbs, [{ category: 'fetch', data: { method: 'GET', url: 'https://api.example.org/api/v1/tasks', status_code: 500 } }, { category: 'console' }]);
    const text = JSON.stringify(out);
    for (const leak of ['secret', 'someone@example.org', 'code=', 'invitation', 'Referer', 'cookies', 'body']) assert.ok(!text.includes(leak), leak);
  });

  test('sends no tenant tag without a tenant', () => {
    assert.deepEqual((scrubEvent(event(), null) as Record<string, any>).tags, {});
  });
});
