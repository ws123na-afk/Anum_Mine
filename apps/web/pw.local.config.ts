import base from './playwright.config';
const launch = { launchOptions: { executablePath: '/opt/pw-browsers/chromium' } };
export default { ...base, projects: base.projects!.map(p => ({ ...p, use: { ...p.use, ...launch } })),
  webServer: (base.webServer as any[]).map(w => ({ ...w, reuseExistingServer: false })) };
