import base from './playwright.config';
export default { ...base, webServer: undefined, projects: base.projects?.map((p) => ({ ...p, use: { ...p.use, launchOptions: { executablePath: '/opt/pw-browsers/chromium' } } })) };
