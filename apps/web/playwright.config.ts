import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // In CI the HTML report is written too (never opened); the workflow uploads it with the traces
  // when the job fails.
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], ...(process.platform === 'win32' ? { channel: 'msedge' as const } : {}) } },
    { name: 'mobile', use: { ...devices['Pixel 7'], ...(process.platform === 'win32' ? { channel: 'msedge' as const } : {}) } },
  ],
  webServer: [
    {
      command: 'pnpm build && pnpm exec vite preview --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // The same app built with OIDC sign-in enabled, for e2e/oidc-sign-in.spec.ts. The issuer is a
      // placeholder host that the spec intercepts; nothing resolves or contacts it.
      command: 'pnpm exec vite build --outDir dist-oidc --emptyOutDir && pnpm exec vite preview --outDir dist-oidc --host 127.0.0.1 --port 4174',
      url: 'http://127.0.0.1:4174',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_ANUM_OIDC_ISSUER: 'http://oidc.anum.test/realms/anum', VITE_ANUM_OIDC_CLIENT_ID: 'anum-web' },
    },
    {
      // The same app built with browser error reporting on, for e2e/error-reporting.spec.ts. The DSN
      // names a placeholder host that the spec intercepts; nothing resolves or contacts it.
      command: 'pnpm exec vite build --outDir dist-sentry --emptyOutDir && pnpm exec vite preview --outDir dist-sentry --host 127.0.0.1 --port 4175',
      url: 'http://127.0.0.1:4175',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { VITE_SENTRY_DSN: 'https://examplekey@errors.anum.test/7', VITE_SENTRY_ENVIRONMENT: 'e2e' },
    },
  ],
});
