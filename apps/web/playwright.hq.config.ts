// Local runner for the headquarters (HQ) specs in a sandbox: uses the installed Chromium, an own port,
// and WebGL through SwiftShader (software GL). CI keeps using playwright.config.ts.
// Usage: pnpm build && pnpm exec vite preview --host 127.0.0.1 --port 4205 &  then
//   pnpm exec playwright test -c playwright.hq.config.ts e2e/hq-*.spec.ts
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.HQ_PORT ?? 4205);
export default defineConfig({
  testDir: './e2e',
  testMatch: /hq-.*\.spec\.ts|office-workstations\.spec\.ts|hq\/.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'off',
    launchOptions: {
      executablePath: process.env.HQ_CHROMIUM ?? '/opt/pw-browsers/chromium',
      args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
    },
  },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `pnpm build && pnpm exec vite preview --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
