// Optional browser error reporting to a Sentry-compatible server (GlitchTip on the Oracle VM),
// docs/observability.md#error-reporting. Off unless the bundle is built with VITE_SENTRY_DSN; then
// errorReportingStart.ts loads @sentry/browser on first use and every event and breadcrumb passes
// through scrubEvent and scrubBreadcrumb below.
//
// The same rules as the API's error_reporting.py: an event keeps the exception type and stack frames
// (file, function, line), the page path, the environment and one tag, anum.tenant_id. It never
// carries exception messages (they can echo what someone typed or an API answer), user identity,
// request bodies, query strings or fragments, headers, cookies, source lines, local variables,
// extra data or other tags. Breadcrumbs keep their category, level and time; fetch and XHR
// breadcrumbs keep method, status and the URL without its query; navigation keeps paths. A
// breadcrumb whose URL carried a token (OIDC code, access token, invitation) is dropped whole.
//
// Pure (no imports), so `node --experimental-strip-types` runs it in the unit tests and in the
// Docker build (scripts/sentry-csp-origin.ts).

export const REDACTED = '[redacted]';
export const TENANT_TAG = 'anum.tenant_id';

const KEPT_CONTEXTS = new Set(['browser', 'os', 'trace']);
const KEPT_BREADCRUMB_DATA = new Set(['method', 'status_code', 'url', 'from', 'to']);
const URL_KEYS = ['url', 'from', 'to'] as const;
const NETWORK_CATEGORIES = new Set(['fetch', 'xhr', 'navigation']);
// Anything that looks like a credential in a URL (query, fragment or path).
const TOKEN_IN_URL = /(access_token|id_token|refresh_token|token|code=|state=|session_state|invitation|password|secret|api[_-]?key|authorization|sentry_key)/i;

export class DsnError extends Error {}

/**
 * The origin events are sent to (`https://errors.example.org`), for the CSP's connect-src.
 * Accepts `https://<public key>@<host>[:port]/<project id>`; plain http only for localhost or
 * 127.0.0.1 (development). Throws DsnError (without echoing the value) otherwise.
 */
export function dsnOrigin(dsn: string): string {
  let url: URL;
  try { url = new URL(dsn.trim()); } catch { throw new DsnError('VITE_SENTRY_DSN must look like https://<public key>@<host>/<project id>'); }
  const project = url.pathname.replace(/\/+$/, '').split('/').pop() ?? '';
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
  if (!url.username || url.password || !/^\d+$/.test(project) || url.search || url.hash || !/^[A-Za-z0-9.-]+$/.test(url.hostname)) {
    throw new DsnError('VITE_SENTRY_DSN must look like https://<public key>@<host>/<project id>');
  }
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new DsnError('VITE_SENTRY_DSN must use https (http only for localhost)');
  }
  return url.origin;
}

/** A URL without its query string, fragment and credentials; unparsable values are redacted. */
export function stripUrl(value: unknown): unknown {
  if (typeof value !== 'string' || !value) return value;
  try {
    const relative = !/^[a-z][a-z0-9+.-]*:/i.test(value);
    const url = new URL(value, 'http://relative.invalid');
    const path = url.pathname;
    return relative ? path : `${url.protocol}//${url.host}${path}`;
  } catch {
    return REDACTED;
  }
}

export function urlCarriesToken(value: unknown): boolean {
  return typeof value === 'string' && TOKEN_IN_URL.test(value);
}

type Json = Record<string, unknown>;
const isRecord = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value);

/** A breadcrumb as the SDK passes it to beforeBreadcrumb; null drops it. */
export function scrubBreadcrumb<T extends Json>(crumb: T): T | null {
  const data = isRecord(crumb.data) ? crumb.data : undefined;
  if (data && URL_KEYS.some((key) => urlCarriesToken(data[key]))) return null;
  const category = typeof crumb.category === 'string' ? crumb.category : undefined;
  const cleaned: Json = {};
  for (const key of ['type', 'category', 'level', 'timestamp'] as const) if (key in crumb) cleaned[key] = crumb[key];
  if (data && category && NETWORK_CATEGORIES.has(category)) {
    const kept: Json = {};
    for (const [key, value] of Object.entries(data)) {
      if (!KEPT_BREADCRUMB_DATA.has(key)) continue;
      kept[key] = (URL_KEYS as readonly string[]).includes(key) ? stripUrl(value) : value;
    }
    if (Object.keys(kept).length) cleaned.data = kept;
  }
  // Messages (console text, clicked element names) are never kept.
  return cleaned as T;
}

function scrubFrames(container: unknown): void {
  if (!isRecord(container) || !isRecord(container.stacktrace) || !Array.isArray(container.stacktrace.frames)) return;
  for (const frame of container.stacktrace.frames) {
    if (!isRecord(frame)) continue;
    for (const key of ['vars', 'context_line', 'pre_context', 'post_context']) delete frame[key];
    if ('abs_path' in frame) frame.abs_path = stripUrl(frame.abs_path);
    if ('filename' in frame) frame.filename = stripUrl(frame.filename);
  }
}

/** An event as the SDK passes it to beforeSend, reduced to what may leave the browser. */
export function scrubEvent<T extends Json>(event: T, tenantId: string | null): T {
  const out = event as Json;
  for (const key of ['user', 'extra', 'fingerprint_extra', 'modules', 'server_name']) delete out[key];
  if (isRecord(out.request)) {
    out.request = out.request.url === undefined ? {} : { url: stripUrl(out.request.url) };
  }
  if (typeof out.transaction === 'string') out.transaction = stripUrl(out.transaction);
  if (isRecord(out.exception) && Array.isArray(out.exception.values)) {
    for (const value of out.exception.values) {
      if (!isRecord(value)) continue;
      if ('value' in value) value.value = REDACTED;
      scrubFrames(value);
    }
  }
  if (Array.isArray(out.threads)) for (const thread of out.threads) scrubFrames(thread);
  if (isRecord(out.contexts)) {
    out.contexts = Object.fromEntries(Object.entries(out.contexts).filter(([name]) => KEPT_CONTEXTS.has(name)));
  }
  if (Array.isArray(out.breadcrumbs)) {
    out.breadcrumbs = out.breadcrumbs.filter(isRecord).map((crumb) => scrubBreadcrumb(crumb)).filter((crumb) => crumb !== null);
  }
  out.tags = tenantId ? { [TENANT_TAG]: tenantId } : {};
  return event;
}
