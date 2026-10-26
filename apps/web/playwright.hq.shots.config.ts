// Local screenshot runner for e2e/tools/*.shots.ts (not part of CI): same browser flags as playwright.hq.config.ts.
// Software GL (SwiftShader) flags; CI keeps using playwright.config.ts.
// Usage: vite preview --port 4311 &  then  HQ_PORT=4311 LOOK_DIR=<dir> LOOK_Q=4 pnpm exec playwright test -c playwright.hq.shots.config.ts --project=desktop
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.HQ_PORT ?? 4205);
export default defineConfig({
  testDir: './e2e',
  testMatch: /\.shots\.ts$/,
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
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: 'pnpm exec vite preview --host 127.0.0.1 --port ' + port,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
