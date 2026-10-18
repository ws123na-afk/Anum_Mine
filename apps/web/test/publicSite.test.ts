// Public pages (docs/legal-pages.md): routes, the legal documents in docs/legal, the DRAFT banner
// logic and the status checks.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  LEGAL_DOCS, apiHealthy, checkEndpoint, discoveryHealthy, headingIds, internalPath, legalState, overallState, parsePublished,
  placeholdersIn, publicRoute, readLegalDocument, rewriteDocLinks, splitPlaceholders, statusTargets, type CheckDeps, type ComponentCheck,
} from '../src/lib/publicSite.ts';

const repo = new URL('../../../', import.meta.url);
const read = (path: string) => readFileSync(new URL(path, repo), 'utf8');
const sources = Object.fromEntries(LEGAL_DOCS.map((d) => [d.id, read(`docs/legal/${d.file}`)]));
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0x2015)}]`);

test('each public path maps to its page; anything else is the signed-in app', () => {
  assert.deepEqual(publicRoute('/legal/terms'), { kind: 'legal', doc: 'terms' });
  assert.deepEqual(publicRoute('/legal/privacy/'), { kind: 'legal', doc: 'privacy' });
  assert.deepEqual(publicRoute('/legal/dpa'), { kind: 'legal', doc: 'dpa' });
  assert.deepEqual(publicRoute('/legal/aup'), { kind: 'legal', doc: 'aup' });
  assert.deepEqual(publicRoute('/legal/subprocessors'), { kind: 'legal', doc: 'subprocessors' });
  assert.deepEqual(publicRoute('/trust'), { kind: 'trust' });
  assert.deepEqual(publicRoute('/status/'), { kind: 'status' });
  for (const path of ['/', '/index.html', '/legal', '/legal/other', '/privacy', '/terms', '/trusty', '/meetings/connected']) assert.equal(publicRoute(path), null, path);
});

test('main.tsx renders the public pages without the sign-in gate', () => {
  const main = read('apps/web/src/main.tsx');
  assert.match(main, /const publicPage=publicRoute\(window\.location\.pathname\);/);
  assert.match(main, /return publicPage\?<Suspense fallback=\{null\}><PublicSite route=\{publicPage\}\/><\/Suspense>:<AuthGate\/>/);
});

test('the build flag lists published documents; unknown names are ignored', () => {
  assert.deepEqual([...parsePublished(undefined)], []);
  assert.deepEqual([...parsePublished('')], []);
  assert.deepEqual([...parsePublished(' Terms, privacy ,nope')].sort(), ['privacy', 'terms']);
  assert.equal(parsePublished('all').size, LEGAL_DOCS.length);
});

test('every legal document is a marked draft with a title, a date line and no long dashes', () => {
  for (const d of LEGAL_DOCS) {
    const md = sources[d.id];
    assert.match(md, /^# .+\n\n> \*\*DRAFT, owner and a lawyer must review before publishing\.\*\*/, d.file);
    assert.match(md, /\nLast updated: \{\{EFFECTIVE_DATE\}\}\n/, d.file);
    assert.doesNotMatch(md, LONG_DASH, d.file);
    const doc = readLegalDocument(md);
    assert.ok(doc.title.length > 3, d.file);
    assert.equal(doc.updated, '{{EFFECTIVE_DATE}}', d.file);
    assert.doesNotMatch(doc.body, /DRAFT, owner and a lawyer/, `${d.file}: the page shows its own banner instead`);
    assert.ok(doc.placeholders.length > 0, `${d.file} still has owner facts to fill in`);
  }
});

test('the documents never claim a certification or a compliance status', () => {
  for (const d of LEGAL_DOCS) {
    const md = sources[d.id];
    assert.doesNotMatch(md, /\b(SOC ?2|ISO(\/IEC)? ?27001|PCI[ -]DSS|HIPAA)\b[^.\n]*\b(certified|compliant|attested|audited)\b/i, d.file);
    assert.doesNotMatch(md, /\b(we are|ANUM is) (fully )?(certified|compliant)\b/i, d.file);
  }
  assert.match(sources.dpa, /We hold no third-party security certification/);
});

test('every link between documents resolves to a legal page', () => {
  for (const d of LEGAL_DOCS) {
    for (const m of sources[d.id].matchAll(/\]\(([^)]+)\)/g)) {
      const target = m[1];
      if (/^(https?|mailto):/.test(target)) continue;
      assert.ok(LEGAL_DOCS.some((x) => x.file === target.split('#')[0]), `${d.file}: ${target}`);
      assert.ok(existsSync(new URL(`docs/legal/${target.split('#')[0]}`, repo)), target);
    }
  }
  const rewritten = rewriteDocLinks('See [the DPA](data-processing-addendum.md#annex-2), [Trust](https://{{WEB_HOST}}/trust), [x](https://example.com).');
  const hrefs = [...rewritten.matchAll(/\]\(([^)]+)\)/g)].map((m) => m[1]);
  assert.deepEqual(hrefs.map((h) => internalPath(h)), ['/legal/dpa#annex-2', '/trust', null]);
});

test('the facts in the documents match the code and configuration', () => {
  const settings = read('services/api/anum_api/settings.py');
  assert.match(settings, /conversation_retention_days: int = Field\(default=30,/);
  assert.match(settings, /learning_signal_retention_days: int = Field\(default=180,/);
  assert.match(settings, /calls_retention_days: int = Field\(default=365,/);
  assert.match(read('services/api/anum_api/calls/api.py'), /recording_retention_days: int = Field\(default=30, ge=1, le=365\)/);
  assert.match(read('infra/oracle/.env.example'), /^ANUM_BACKUP_LOCAL_KEEP_DAYS=7$/m);
  assert.match(read('docs/deploy-oracle.md'), /hide files 35 days after upload/);
  assert.match(sources.privacy, /ANUM's default is 30/);
  assert.match(sources.privacy, /default 180/);
  assert.match(sources.privacy, /default 365/);
  assert.match(sources.privacy, /Saudi Personal Data Protection Law/);
  assert.match(sources.privacy, /Federal Decree-Law No\. 45 of 2021/);
  for (const provider of ['Oracle Cloud Infrastructure', 'Backblaze', 'Brevo', 'Anthropic', 'Twilio', 'Firebase Cloud Messaging']) assert.match(sources.subprocessors, new RegExp(provider), provider);
  // Cookies: the web app and the API set none (docs/legal-pages.md#cookies).
  assert.match(sources.privacy, /The ANUM web app and API set no cookies\./);
});

test('SECURITY.md and the Trust page give the same disclosure contact', () => {
  const security = read('SECURITY.md');
  const contact = /export const SECURITY_CONTACT = '([^']+)'/.exec(read('apps/web/src/PublicSite.tsx'))?.[1];
  assert.ok(contact);
  assert.ok(security.includes(`**${contact}**`));
});

test('a document marked published with placeholders left stays a draft', () => {
  const doc = readLegalDocument('# T\n\n> **DRAFT, x.**\n> more\n\nLast updated: 1 May 2027\n\n## A\n\nHello {{NAME}}.\n');
  assert.equal(doc.title, 'T');
  assert.equal(doc.updated, '1 May 2027');
  assert.equal(doc.body, '## A\n\nHello {{NAME}}.');
  assert.equal(legalState('terms', doc, new Set()), 'draft');
  assert.equal(legalState('terms', doc, new Set(['terms'])), 'blocked');
  assert.equal(legalState('terms', { ...doc, placeholders: [] }, new Set(['terms'])), 'published');
  assert.deepEqual(placeholdersIn('{{A}} {{B}} {{A}} {{lower}}'), ['{{A}}', '{{B}}']);
  assert.deepEqual(splitPlaceholders('By {{NAME}}, at {{X}}'), [
    { text: 'By ', placeholder: false }, { text: '{{NAME}}', placeholder: true }, { text: ', at ', placeholder: false }, { text: '{{X}}', placeholder: true },
  ]);
  assert.deepEqual(headingIds(['1. Who we are', 'Who we are', 'Who we are', '{{X}}']), ['section-1-who-we-are', 'who-we-are', 'who-we-are-2', 'x']);
});

type Fetch = CheckDeps['fetch'];
const answer = (status: number, body: unknown): Fetch => async () => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test('a check reads healthy, degraded and unreachable answers', async () => {
  let clock = 0;
  const deps = (fetch: Fetch): CheckDeps => ({ fetch, now: () => (clock += 25), timeoutMs: 50 });
  assert.deepEqual(await checkEndpoint('api', 'u', apiHealthy, deps(answer(200, { status: 'ok', environment: 'production' }))), { id: 'api', state: 'operational', latencyMs: 25 });
  assert.deepEqual(await checkEndpoint('api', 'u', apiHealthy, deps(answer(503, { status: 'ok' }))), { id: 'api', state: 'degraded', latencyMs: 25, httpStatus: 503 });
  assert.equal((await checkEndpoint('api', 'u', apiHealthy, deps(answer(200, '<html>')))).state, 'degraded');
  assert.deepEqual(await checkEndpoint('api', 'u', apiHealthy, deps(async () => { throw new TypeError('Failed to fetch'); })), { id: 'api', state: 'unreachable' });
  const hangs: Fetch = (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))));
  assert.deepEqual(await checkEndpoint('auth', 'u', () => true, deps(hangs)), { id: 'auth', state: 'unreachable' });
});

test('sign-in is healthy only when discovery names the configured issuer', () => {
  assert.equal(discoveryHealthy({ issuer: 'https://auth.example.com/realms/anum' }, 'https://auth.example.com/realms/anum/'), true);
  assert.equal(discoveryHealthy({ issuer: 'https://evil.example.com/realms/anum' }, 'https://auth.example.com/realms/anum'), false);
  assert.equal(discoveryHealthy(null, 'x'), false);
  assert.deepEqual(statusTargets('https://api.example.com/', ' https://auth.example.com/realms/anum/ '), {
    api: 'https://api.example.com/health', auth: 'https://auth.example.com/realms/anum/.well-known/openid-configuration', issuer: 'https://auth.example.com/realms/anum',
  });
  assert.deepEqual(statusTargets('http://localhost:8000', ''), { api: 'http://localhost:8000/health', auth: null, issuer: null });
});

test('the headline follows the API first, then every other component', () => {
  const c = (api: ComponentCheck['state'], auth: ComponentCheck['state']): ComponentCheck[] => [{ id: 'web', state: 'operational' }, { id: 'api', state: api }, { id: 'auth', state: auth }];
  assert.equal(overallState(c('checking', 'operational')), 'checking');
  assert.equal(overallState(c('operational', 'operational')), 'operational');
  assert.equal(overallState(c('operational', 'not_configured')), 'operational');
  assert.equal(overallState(c('operational', 'unreachable')), 'degraded');
  assert.equal(overallState(c('unreachable', 'operational')), 'outage');
  assert.equal(overallState(c('degraded', 'operational')), 'outage');
});
