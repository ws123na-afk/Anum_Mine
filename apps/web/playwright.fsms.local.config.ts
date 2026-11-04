import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', workers: 1, reporter: 'line',
  use: { baseURL: 'http://127.0.0.1:4291', trace: 'off', screenshot: 'only-on-failure', launchOptions: { executablePath: '/opt/pw-browsers/chromium' } },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], launchOptions: { executablePath: '/opt/pw-browsers/chromium' } } },
    { name: 'mobile', grepInvert: /@gate/, use: { ...devices['Pixel 7'], launchOptions: { executablePath: '/opt/pw-browsers/chromium' } } },
  ],
  webServer: { command: 'pnpm exec vite preview --host 127.0.0.1 --port 4291 --strictPort', url: 'http://127.0.0.1:4291', reuseExistingServer: false, timeout: 60_000 },
});
