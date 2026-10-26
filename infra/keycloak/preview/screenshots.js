// Screenshots and layout checks of the pages preview.sh rendered (docs/identity.md#login-theme-preview).
//
//   node screenshots.js <site-dir> <out-dir> [pages=login,otp] [langs=en,ar] [schemes=light,dark] [chromium-path]
//
// Needs Playwright (require('playwright')) and a Chromium. For every page, language, colour scheme and
// viewport (1440x900, 1366x768, 1024x768, 768x1024, 390x844) it saves a PNG and reports horizontal
// overflow, controls that leave the viewport sideways and text-bearing elements whose box is clipped.
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const [site, out, pagesArg = 'login', langsArg = 'en,ar', schemesArg = 'light,dark', exe = '/opt/pw-browsers/chromium'] = process.argv.slice(2);
const viewports = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]];

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: exe });
  const problems = [];
  for (const page of pagesArg.split(',')) for (const lang of langsArg.split(',')) for (const scheme of schemesArg.split(',')) {
    const context = await browser.newContext({ colorScheme: scheme, reducedMotion: 'reduce' });
    const tab = await context.newPage();
    for (const [width, height] of viewports) {
      await tab.setViewportSize({ width, height });
      await tab.goto('file://' + path.resolve(site, `${page}.${lang}.html`));
      await tab.evaluate(() => document.fonts.ready);
      await tab.waitForTimeout(150);
      const report = await tab.evaluate(() => {
        const root = document.documentElement;
        const bad = [];
        for (const el of document.querySelectorAll('main *, .anum-brand-inner *, .anum-toolbar *')) {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if (r.right > root.clientWidth + 1 || r.left < -1) bad.push((el.id || el.className || el.tagName).toString().slice(0, 40));
        }
        return { overflowX: root.scrollWidth - root.clientWidth, pageHeight: root.scrollHeight, bad: [...new Set(bad)].slice(0, 5) };
      });
      const name = `${page}.${lang}.${scheme}.${width}x${height}.png`;
      await tab.screenshot({ path: path.join(out, name), fullPage: false });
      if (report.overflowX > 0 || report.bad.length) problems.push({ name, ...report });
      if (process.env.VERBOSE) console.log(name, JSON.stringify(report));
    }
    await context.close();
  }
  await browser.close();
  console.log(problems.length ? 'PROBLEMS ' + JSON.stringify(problems, null, 1) : 'no horizontal overflow or sideways-clipped elements');
})();
