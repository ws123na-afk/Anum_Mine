// The public, no-sign-in pages (docs/legal-pages.md): legal documents rendered from docs/legal,
// the Trust page and the status page. Pure helpers only, so `pnpm test:unit` runs them directly;
// the Markdown sources are imported by legalDocs.ts (Vite) and read from disk by the tests.

export type LegalDocId = 'terms' | 'privacy' | 'dpa' | 'aup' | 'subprocessors';
export type PublicRoute = { kind: 'legal'; doc: LegalDocId } | { kind: 'trust' } | { kind: 'status' };

/** Every legal document: its route and its source file in docs/legal. Order is the footer order. */
export const LEGAL_DOCS: readonly { id: LegalDocId; path: string; file: string }[] = [
  { id: 'terms', path: '/legal/terms', file: 'terms-of-service.md' },
  { id: 'privacy', path: '/legal/privacy', file: 'privacy-policy.md' },
  { id: 'dpa', path: '/legal/dpa', file: 'data-processing-addendum.md' },
  { id: 'aup', path: '/legal/aup', file: 'acceptable-use-policy.md' },
  { id: 'subprocessors', path: '/legal/subprocessors', file: 'subprocessors.md' },
];

export const TRUST_PATH = '/trust';
export const STATUS_PATH = '/status';

/** The public page for a location path, or null for the signed-in app. A trailing slash is accepted. */
export function publicRoute(pathname: string): PublicRoute | null {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (path === TRUST_PATH) return { kind: 'trust' };
  if (path === STATUS_PATH) return { kind: 'status' };
  const doc = LEGAL_DOCS.find((d) => d.path === path);
  return doc ? { kind: 'legal', doc: doc.id } : null;
}

export function isLegalDocId(value: string): value is LegalDocId {
  return LEGAL_DOCS.some((d) => d.id === value);
}

/**
 * The documents the build marks published: VITE_ANUM_LEGAL_PUBLISHED, a comma-separated list of
 * ids (`terms,privacy`), or `all`. Unknown names are ignored; empty means every page is a draft.
 */
export function parsePublished(raw: unknown): Set<LegalDocId> {
  if (typeof raw !== 'string') return new Set();
  const names = raw.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (names.includes('all')) return new Set(LEGAL_DOCS.map((d) => d.id));
  return new Set(names.filter(isLegalDocId));
}

/** `{{NAME}}` marks a fact the owner still has to supply. */
export const PLACEHOLDER = /\{\{[A-Z0-9_]+\}\}/g;

export function placeholdersIn(markdown: string): string[] {
  return [...new Set(markdown.match(PLACEHOLDER) ?? [])];
}

export interface LegalDocument {
  title: string;
  updated: string | null;
  body: string;
  placeholders: string[];
}

/**
 * Splits a docs/legal file into what the page shows: the `# Title`, the "Last updated" value and
 * the body. The source's DRAFT notice (a quote starting with **DRAFT) is dropped from the body;
 * the page shows its own banner instead, driven by `legalState`.
 */
export function readLegalDocument(markdown: string): LegalDocument {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  let title = '';
  let updated: string | null = null;
  const body: string[] = [];
  let inDraftNotice = false;
  for (const line of lines) {
    if (!title && /^#\s+/.test(line)) { title = line.replace(/^#\s+/, '').trim(); continue; }
    if (/^>\s*\*\*DRAFT/.test(line)) { inDraftNotice = true; continue; }
    if (inDraftNotice) { if (line.startsWith('>')) continue; inDraftNotice = false; }
    const last = /^Last updated:\s*(.+)$/.exec(line);
    if (last && updated === null) { updated = last[1].trim(); continue; }
    body.push(line);
  }
  return { title, updated, body: body.join('\n').trim(), placeholders: placeholdersIn(markdown) };
}

/**
 * `published`: the build marks it published and no placeholder is left. `blocked`: marked
 * published while placeholders remain, so it still shows as a draft and says why. Else `draft`.
 */
export type LegalState = 'published' | 'draft' | 'blocked';

export function legalState(id: LegalDocId, doc: LegalDocument, published: Set<LegalDocId>): LegalState {
  if (!published.has(id)) return 'draft';
  return doc.placeholders.length ? 'blocked' : 'published';
}

// Links between the documents are written as relative .md links (docs:check verifies them) and as
// https://{{WEB_HOST}}/trust or /status. The safe Markdown reader keeps only http(s) and mailto
// links, so they are rewritten to this marker origin first and mapped back to a path when shown.
const LINK_ORIGIN = 'https://anum-public.invalid';

export function rewriteDocLinks(markdown: string): string {
  return markdown
    .replace(/\]\(([a-z-]+)\.md(#[\w-]+)?\)/g, (whole, file: string, anchor: string | undefined) => {
      const doc = LEGAL_DOCS.find((d) => d.file === `${file}.md`);
      return doc ? `](${LINK_ORIGIN}${doc.path}${anchor ?? ''})` : whole;
    })
    .replace(/\]\(https:\/\/\{\{WEB_HOST\}\}(\/(?:trust|status)|\/legal\/[a-z]+)\)/g, `](${LINK_ORIGIN}$1)`);
}

/** The same-site path for a rewritten link, or null for an outside link. */
export function internalPath(href: string): string | null {
  if (!href.startsWith(`${LINK_ORIGIN}/`)) return null;
  return href.slice(LINK_ORIGIN.length);
}

/** Text split into plain parts and `{{PLACEHOLDER}}` parts, so the page can mark what is missing. */
export function splitPlaceholders(text: string): { text: string; placeholder: boolean }[] {
  const out: { text: string; placeholder: boolean }[] = [];
  let last = 0;
  for (const m of text.matchAll(PLACEHOLDER)) {
    const index = m.index ?? 0;
    if (index > last) out.push({ text: text.slice(last, index), placeholder: false });
    out.push({ text: m[0], placeholder: true });
    last = index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), placeholder: false });
  return out;
}

/** A stable, unique id for each heading, for the contents list. */
export function headingIds(titles: string[]): string[] {
  const seen = new Map<string, number>();
  return titles.map((title) => {
    const base = title.toLowerCase().replace(/\{\{|\}\}/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'section';
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    const id = n ? `${base}-${n + 1}` : base;
    return /^[a-z]/.test(id) ? id : `section-${id}`;
  });
}

// ---- Status page -------------------------------------------------------------------------------

export type ComponentId = 'web' | 'api' | 'auth';
export type ComponentState = 'checking' | 'operational' | 'degraded' | 'unreachable' | 'not_configured';
export type OverallState = 'checking' | 'operational' | 'degraded' | 'outage';

export interface ComponentCheck {
  id: ComponentId;
  state: ComponentState;
  /** Round trip in milliseconds, when a response came back. */
  latencyMs?: number;
  /** The HTTP status of an answer that was not what the check expects. */
  httpStatus?: number;
}

export const STATUS_TIMEOUT_MS = 8000;
export const STATUS_REFRESH_MS = 60_000;

/** GET /health answers {"status": "ok", "environment": ...} when the API process is serving. */
export function apiHealthy(body: unknown): boolean {
  return typeof body === 'object' && body !== null && (body as { status?: unknown }).status === 'ok';
}

/** Keycloak's discovery document names the issuer the web app was built with. */
export function discoveryHealthy(body: unknown, issuer: string): boolean {
  if (typeof body !== 'object' || body === null) return false;
  const value = (body as { issuer?: unknown }).issuer;
  return typeof value === 'string' && value.replace(/\/+$/, '') === issuer.replace(/\/+$/, '');
}

export interface CheckDeps {
  fetch: (url: string, init: { signal: AbortSignal; cache: RequestCache; headers: Record<string, string> }) => Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;
  now: () => number;
  timeoutMs?: number;
}

/**
 * One check: a GET with a timeout. No answer (network error, CORS refusal, timeout) is
 * `unreachable`; an answer that is not 2xx, or a body the check does not accept, is `degraded`.
 */
export async function checkEndpoint(id: ComponentId, url: string, healthy: (body: unknown) => boolean, deps: CheckDeps): Promise<ComponentCheck> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), deps.timeoutMs ?? STATUS_TIMEOUT_MS);
  const started = deps.now();
  try {
    const response = await deps.fetch(url, { signal: controller.signal, cache: 'no-store', headers: { accept: 'application/json' } });
    const latencyMs = Math.max(0, Math.round(deps.now() - started));
    if (!response.ok) return { id, state: 'degraded', latencyMs, httpStatus: response.status };
    const body = await response.json().catch(() => null);
    return healthy(body) ? { id, state: 'operational', latencyMs } : { id, state: 'degraded', latencyMs, httpStatus: response.status };
  } catch {
    return { id, state: 'unreachable' };
  } finally {
    clearTimeout(timer);
  }
}

/** The headline: still checking, all fine, the API cannot be used (outage), or something is off. */
export function overallState(checks: ComponentCheck[]): OverallState {
  if (checks.some((c) => c.state === 'checking')) return 'checking';
  const api = checks.find((c) => c.id === 'api');
  if (api && api.state !== 'operational') return 'outage';
  return checks.every((c) => c.state === 'operational' || c.state === 'not_configured') ? 'operational' : 'degraded';
}

/** The URLs the status page checks, from the build's configuration. */
export function statusTargets(apiBaseUrl: string, oidcIssuer: string | undefined): { api: string; auth: string | null; issuer: string | null } {
  const issuer = oidcIssuer?.trim().replace(/\/+$/, '') || null;
  return { api: `${apiBaseUrl.replace(/\/+$/, '')}/health`, auth: issuer ? `${issuer}/.well-known/openid-configuration` : null, issuer };
}
