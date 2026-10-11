import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { dsnOrigin } from './src/lib/errorReporting.ts';

export default defineConfig(({ mode }) => {
  // A malformed VITE_SENTRY_DSN fails the build instead of shipping a reporter that cannot send
  // (docs/observability.md#error-reporting). Empty means error reporting is off.
  const dsn = loadEnv(mode, process.cwd(), 'VITE_').VITE_SENTRY_DSN ?? process.env.VITE_SENTRY_DSN;
  if (dsn?.trim()) dsnOrigin(dsn);
  return {
    plugins: [react()],
    server: {
      port: 5173,
    },
  };
});
