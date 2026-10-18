// Loaded only when the bundle was built with VITE_SENTRY_DSN (see startErrorReporting in main.tsx):
// a build without it contains no Sentry code at all. Errors only: no tracing, no replay, no
// sessions, no user feedback. Every event and breadcrumb is scrubbed (lib/errorReporting.ts).
import * as Sentry from '@sentry/browser';
import { scrubBreadcrumb, scrubEvent } from './errorReporting';

export function start(dsn: string, environment: string, tenantId: () => string | null): void {
  Sentry.init({
    dsn,
    environment,
    // SDK 11 replaced `sendDefaultPii` with per-category data collection: collect none of it.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
    },
    // Only the integrations that report errors and the scrubbed network/navigation breadcrumbs.
    // SDK 11 records console breadcrumbs in its own `Console` integration, so it is dropped too.
    integrations: (defaults) => [
      ...defaults.filter((integration) => !['BrowserSession', 'Breadcrumbs', 'Console'].includes(integration.name)),
      // Console text and clicked element names can hold what someone typed: off.
      Sentry.breadcrumbsIntegration({ dom: false, fetch: true, xhr: true, history: true, sentry: false }),
    ],
    maxBreadcrumbs: 30,
    beforeBreadcrumb: (crumb) => scrubBreadcrumb(crumb as unknown as Record<string, unknown>) as unknown as Sentry.Breadcrumb | null,
    beforeSend: (event) => scrubEvent(event as unknown as Record<string, unknown>, safeTenant(tenantId)) as unknown as Sentry.ErrorEvent,
  });
}

function safeTenant(tenantId: () => string | null): string | null {
  try { return tenantId(); } catch { return null; }
}
