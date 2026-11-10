import { defineConfig, devices } from '@playwright/test';

// CI builds the three bundles once ("Browser end-to-end (build)" in .github/workflows/ci.yml) and
// every shard downloads them: with ANUM_E2E_PREBUILT set the servers below only serve dist,
// dist-oidc and dist-sentry. Without it (a local run) each server builds its own bundle first.
// The build commands and variables here and in that job must stay the same.
const prebuilt = Boolean(process.env.ANUM_E2E_PREBUILT);
const served = (build: string, preview: string): string => (prebuilt ? preview : `${build} && ${preview}`);

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // GitHub's Linux runners have four cores; three workers per shard (twelve shards in ci.yml).
  workers: process.env.CI ? 3 : undefined,
  // CI runs the suite in shards (.github/workflows/ci.yml, `--shard`): each shard writes a blob
  // report, and the "Browser end-to-end" job merges them into one HTML report.
  reporter: process.env.CI ? [['github'], ['blob']] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], ...(process.platform === 'win32' ? { channel: 'msedge' as const } : {}) } },
    // The @gate sweeps (ui-gate, journey) set their sizes themselves: desktop runs them once and mobile leaves them out.
    { name: 'mobile', grepInvert: /@gate/, use: { ...devices['Pixel 7'], ...(process.platform === 'win32' ? { channel: 'msedge' as const } : {}) } },
  ],
  webServer: [
    {
      command: served('pnpm build', 'pnpm exec vite preview --host 127.0.0.1 --port 4173'),
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // The same app built with OIDC sign-in enabled, for e2e/oidc-sign-in.spec.ts. The issuer is a
      // placeholder host that the spec intercepts; nothing resolves or contacts it.
      command: served('pnpm exec vite build --outDir dist-oidc --emptyOutDir', 'pnpm exec vite preview --outDir dist-oidc --host 127.0.0.1 --port 4174'),
      url: 'http://127.0.0.1:4174',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_ANUM_OIDC_ISSUER: 'http://oidc.anum.test/realms/anum', VITE_ANUM_OIDC_CLIENT_ID: 'anum-web' },
    },
    {
      // The same app built with browser error reporting on, for e2e/error-reporting.spec.ts. The DSN
      // names a placeholder host that the spec intercepts; nothing resolves or contacts it.
      command: served('pnpm exec vite build --outDir dist-sentry --emptyOutDir', 'pnpm exec vite preview --outDir dist-sentry --host 127.0.0.1 --port 4175'),
      url: 'http://127.0.0.1:4175',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_SENTRY_DSN: 'https://examplekey@errors.anum.test/7', VITE_SENTRY_ENVIRONMENT: 'e2e' },
    },
  ],
});
