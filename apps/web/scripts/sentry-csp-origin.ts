// Prints the origin the browser sends error reports to (VITE_SENTRY_DSN), for the CSP's connect-src,
// or nothing when error reporting is off. Exits 1 (without echoing the DSN) when it is malformed.
// Used by apps/web/Dockerfile: node --experimental-strip-types apps/web/scripts/sentry-csp-origin.ts
import { DsnError, dsnOrigin } from '../src/lib/errorReporting.ts';

const dsn = (process.env.VITE_SENTRY_DSN ?? '').trim();
try {
  process.stdout.write(dsn ? `${dsnOrigin(dsn)}\n` : '\n');
} catch (error) {
  process.stderr.write(`${error instanceof DsnError ? error.message : 'VITE_SENTRY_DSN could not be read'}\n`);
  process.exit(1);
}
