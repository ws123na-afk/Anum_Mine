#!/usr/bin/env node
/**
 * Owner-run graphics check for the ANUM MINE headquarters (docs/ANUM_MINE_HQ_GPU_CHECK.md).
 *
 * Opens the headquarters in a HEADED Chromium started with GPU flags, runs the in-app Graphics check for every quality tier
 * (first on the campus, then inside a building so a floor change is timed too), saves the JSON reports and screenshots of the campus
 * by day, at dusk and at night and of a building cut-away, and prints a summary. It never asks for or reads a password, and it reads
 * nothing from the repository except Playwright itself.
 *
 *   node scripts/hq-gpu-check.mjs --url https://app.anum360os.com --login
 *
 * Options:  --url <address>      the app (default http://127.0.0.1:4173, a local `vite preview`)
 *           --login              open the page, let YOU sign in by hand, then press Enter here to continue
 *           --length <2|5|10>    seconds timed per tier (default 5)
 *           --out <folder>       where reports and screenshots go (default ./hq-gpu-check-<date time>)
 *           --channel <name>     chrome (default), msedge, or chromium for Playwright's own build
 *           --executable <path>  a browser binary to use instead of a channel
 *           --headless           no window (Chrome's new headless mode still uses the GPU)
 *           --allow-software     carry on, and exit 0, even when only a software rasteriser is found
 * Environment: ANUM_HQ_URL (same as --url) and ANUM_HQ_SESSION_TOKEN (an access token placed in the page's session storage; treat it
 *           like a password and prefer --login).
 * Exit codes: 0 done on a GPU, 2 only software GL was found (results are NOT about your graphics card), 3 the check could not run.
 */
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const GPU_ARGS = ['--use-angle=default', '--enable-gpu-rasterization', '--ignore-gpu-blocklist'];
const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic|mesa offscreen|lavapipe/i;

export function parseArgs(argv, env = {}) {
  const o = { url: env.ANUM_HQ_URL || 'http://127.0.0.1:4173', login: false, length: 5, out: '', channel: 'chrome', executable: '', headless: false, allowSoftware: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => { const v = argv[++i]; if (v === undefined) throw new Error(`${a} needs a value`); return v; };
    if (a === '--url') o.url = next();
    else if (a === '--login') o.login = true;
    else if (a === '--length') o.length = Number(next());
    else if (a === '--out') o.out = next();
    else if (a === '--channel') o.channel = next();
    else if (a === '--executable') o.executable = next();
    else if (a === '--headless') o.headless = true;
    else if (a === '--allow-software') o.allowSoftware = true;
    else if (a === '--help' || a === '-h') o.help = true;
    else throw new Error(`Unknown option ${a}. Use --help.`);
  }
  if (![2, 5, 10].includes(o.length)) throw new Error('--length must be 2, 5 or 10');
  let parsed;
  try { parsed = new URL(o.url); } catch { throw new Error('--url must be a full address such as https://app.anum360os.com'); }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') throw new Error('--url must start with https:// or http://');
  o.origin = parsed.origin;
  return o;
}

const stamp = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

/** WebGL facts straight from a scratch canvas, to decide up front whether this browser has a GPU. */
async function glFacts(page) {
  return page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return { webgl2: false, renderer: '', vendor: '' };
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    return { webgl2: true, renderer: String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER)), vendor: String(gl.getParameter(info ? info.UNMASKED_VENDOR_WEBGL : gl.VENDOR)), masked: !info };
  });
}

async function openPanel(page) {
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: 'Office tools' }).click();
  await hq.getByRole('button', { name: 'Graphics check' }).click();
  await page.getByTestId('hq-gpu-check').waitFor({ state: 'visible' });
}

async function benchAndSave(page, o, label, log) {
  await openPanel(page);
  const panel = page.getByTestId('hq-gpu-check');
  await panel.getByTestId('hq-gpu-length').selectOption(String(o.length));
  await panel.getByTestId('hq-gpu-bench').click();
  log(`Timing every tier (${label}), about ${Math.round(4 * (o.length + 1))} seconds. Leave the window alone.`);
  await panel.getByTestId('hq-gpu-progress').filter({ hasText: /Done|Stopped|could not finish/ }).waitFor({ timeout: 8 * 60_000 });
  const verdict = (await panel.getByTestId('hq-gpu-verdict').innerText()).replace(/\s+/g, ' ').trim();
  await page.screenshot({ path: path.join(o.out, `panel-${label}.png`) });
  const [download] = await Promise.all([page.waitForEvent('download'), panel.getByTestId('hq-gpu-download').click()]);
  const file = path.join(o.out, `report-${label}.json`);
  await download.saveAs(file);
  await page.keyboard.press('Escape');
  await panel.waitFor({ state: 'detached' });
  return { file, verdict };
}

async function lighting(page, name) {
  await page.locator('.hqLive').getByRole('group', { name: 'Lighting' }).getByRole('button', { name }).click();
  await page.waitForTimeout(1500);
}

/** The whole check on an already open page. Exported so the repository's own browser test can run it against a mocked office. */
export async function runCheck(page, o, log = console.log) {
  mkdirSync(o.out, { recursive: true });
  const hq = page.locator('.hqLive');
  await hq.getByRole('heading', { level: 1 }).waitFor({ timeout: 90_000 });
  const canvas = hq.locator('canvas.hqCanvas');
  await canvas.waitFor({ state: 'visible', timeout: 90_000 });
  await page.waitForFunction(() => (document.querySelector('canvas.hqCanvas')?.dataset.engine ?? '').includes('three'), null, { timeout: 120_000 });
  await page.waitForTimeout(2500);

  const facts = await glFacts(page);
  const software = !facts.webgl2 || SOFTWARE.test(facts.renderer);
  if (software) {
    log('');
    log('!! ONLY SOFTWARE GRAPHICS WERE FOUND !!');
    log(facts.webgl2 ? `   This browser draws with: ${facts.renderer}` : '   This browser could not start WebGL 2.');
    log('   The numbers below describe software drawing on the processor, NOT your graphics card.');
    log('   Try: --channel chrome (or msedge) on a PC with a graphics card; turn on "Use graphics acceleration when available" in the browser');
    log('   settings and check chrome://gpu says "Hardware accelerated" for WebGL2; update the graphics driver; do not use a remote desktop session.');
    log('');
  } else {
    log(`Graphics found: ${facts.renderer}`);
  }

  const shots = [];
  const shot = async (name) => { const f = path.join(o.out, name); await page.screenshot({ path: f }); shots.push(f); };
  await lighting(page, 'Day'); await shot('campus-day.png');
  await lighting(page, 'Blue hour'); await shot('campus-dusk.png');
  await lighting(page, 'Night'); await shot('campus-night.png');
  await lighting(page, 'Day');

  const campus = await benchAndSave(page, o, 'campus', log);

  let building = null;
  try {
    await hq.locator('.hqLabels button').first().click({ timeout: 15_000 });
    await hq.getByRole('button', { name: /^Open / }).first().click({ timeout: 15_000 });
    await hq.getByRole('button', { name: 'Back to campus' }).waitFor({ timeout: 30_000 });
    await page.waitForTimeout(3000);
    await shot('building-cutaway-day.png');
    await lighting(page, 'Night'); await shot('building-cutaway-night.png'); await lighting(page, 'Day');
    building = await benchAndSave(page, o, 'building', log);
  } catch {
    log('No building could be opened, so the floor change was not timed. The campus report is complete.');
  }

  const read = async (f) => JSON.parse((await import('node:fs/promises')).readFile ? await (await import('node:fs/promises')).readFile(f, 'utf8') : '{}');
  const reports = {};
  for (const [label, r] of [['campus', campus], ['building', building]]) if (r) reports[label] = await read(r.file);
  const summary = { generatedAt: new Date().toISOString(), softwareOnly: software, renderer: facts.renderer, lengthSeconds: o.length, screenshots: shots.map((s) => path.basename(s)), reports: Object.fromEntries(Object.entries(reports).map(([k, v]) => [k, v.verdict])), verdictText: { campus: campus.verdict, building: building ? building.verdict : null } };
  writeFileSync(path.join(o.out, 'summary.json'), JSON.stringify(summary, null, 2));

  log('');
  log('Tier      median  p95     worst   fps   dropped  (frame times in ms)');
  for (const [label, rep] of Object.entries(reports)) {
    log(`-- ${label}`);
    for (const t of rep.tiers) log(`${t.key.padEnd(9)} ${t.frames.medianMs.toFixed(1).padStart(6)} ${t.frames.p95Ms.toFixed(1).padStart(6)} ${t.frames.worstMs.toFixed(1).padStart(7)} ${t.frames.fps.toFixed(0).padStart(5)} ${String(t.frames.droppedFrames).padStart(8)}`);
  }
  log('');
  log(`Verdict (campus): ${campus.verdict}`);
  if (building) log(`Verdict (building): ${building.verdict}`);
  log(`Saved to ${path.resolve(o.out)}`);
  log('Send us summary.json and the report-*.json files. They hold graphics and timing facts only, no names, accounts or addresses.');
  return { software, summary };
}

async function main() {
  let o;
  try { o = parseArgs(process.argv.slice(2), process.env); } catch (e) { console.error(e.message); process.exit(3); }
  if (o.help) { console.log(`See the header of ${fileURLToPath(import.meta.url)} or docs/ANUM_MINE_HQ_GPU_CHECK.md`); return; }
  o.out = o.out || `hq-gpu-check-${stamp()}`;

  let chromium;
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    chromium = createRequire(path.join(here, '..', 'apps', 'web', 'package.json'))('@playwright/test').chromium;
  } catch {
    console.error('Playwright is not installed. From the repository folder run:  pnpm install   then try again.');
    process.exit(3);
  }
  const launch = { headless: o.headless, args: GPU_ARGS };
  if (o.allowSoftware) launch.args = [...GPU_ARGS, '--enable-unsafe-swiftshader'];
  if (o.executable) launch.executablePath = o.executable; else if (o.channel !== 'chromium') launch.channel = o.channel;
  let browser;
  try { browser = await chromium.launch(launch); } catch {
    console.error(`Could not start the browser (${o.executable || o.channel}). Install Google Chrome, or pass --channel msedge, or --executable "<path to chrome.exe>".`);
    process.exit(3);
  }
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const token = process.env.ANUM_HQ_SESSION_TOKEN;
  await context.addInitScript((t) => {
    try { localStorage.setItem('anum.locale', 'en'); localStorage.setItem('anum.hq.light', 'day'); localStorage.removeItem('anum.hq.quality'); if (t) sessionStorage.setItem('anum_access_token', t); } catch { /* storage blocked */ }
  }, token || '');
  const page = await context.newPage();
  let code = 0;
  try {
    await page.goto(new URL('/#office-preview', o.origin).href, { waitUntil: 'domcontentloaded' });
    if (o.login) {
      console.log('Sign in in the browser window, open the Office, then come back here and press Enter.');
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      await rl.question('');
      rl.close();
    }
    const { software } = await runCheck(page, o);
    if (software && !o.allowSoftware) code = 2;
  } catch (e) {
    console.error(`The check could not finish: ${e instanceof Error ? e.message.split('\n')[0] : 'unknown error'}`);
    console.error('If the office asked you to sign in, run again with --login.');
    code = 3;
  } finally {
    await browser.close();
  }
  process.exit(code);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
