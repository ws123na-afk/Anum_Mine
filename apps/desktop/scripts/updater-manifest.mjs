// Collects a release build's installers and Tauri updater artifacts, and writes the updater
// manifest (latest.json) that ANUM_TAURI_UPDATER_ENDPOINT serves. See docs/desktop.md, "Updates".
//
// One build machine:
//   node scripts/updater-manifest.mjs collect --bundle-dir src-tauri/target/release/bundle \
//     --platform windows-x86_64 --version 0.1.0 --base-url https://host/releases/v0.1.0 --out out
// copies the installers to `out` under release-unique names and, when the updater artifacts were
// signed (a `.sig` next to them), writes `out/latest-<platform>.json` with that platform's entries.
//
// After every platform:
//   node scripts/updater-manifest.mjs merge --version 0.1.0 --notes "..." --out latest.json a.json b.json
//
// Only public values are written: the `.sig` files are minisign signatures made with
// TAURI_SIGNING_PRIVATE_KEY, which never leaves CI secrets.
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Installer kinds by bundle directory. `updater` marks the artifact the updater installs, and
// `installer` the key suffix Tauri looks up first ({os}-{arch}-{installer}, then {os}-{arch}).
const KINDS = [
  { dir: 'nsis', suffix: '-setup.exe', installer: 'nsis', updater: true, primary: true },
  { dir: 'msi', suffix: '.msi', installer: 'msi', updater: true },
  { dir: 'macos', suffix: '.app.tar.gz', installer: 'app', updater: true, primary: true },
  { dir: 'dmg', suffix: '.dmg' },
  { dir: 'appimage', suffix: '.AppImage', installer: 'appimage', updater: true, primary: true },
  { dir: 'deb', suffix: '.deb', installer: 'deb', updater: true },
  { dir: 'rpm', suffix: '.rpm', installer: 'rpm', updater: true },
];

const PLATFORM = /^(windows|darwin|linux)-(x86_64|aarch64|i686|armv7)$/;

/** Release file name: unique per platform, so every platform can share one release. */
export function releaseName(file, version, platform) {
  const name = basename(file);
  if (name.includes(version)) return name;
  // macOS bundles are named after the product only (ANUM.app.tar.gz, ANUM.dmg).
  const dot = name.indexOf('.');
  return `${name.slice(0, dot)}_${version}_${platform}${name.slice(dot)}`;
}

/**
 * Plans what to publish from a bundle directory listing.
 * `files` maps a bundle subdirectory to its file names. Returns { copies, platforms }: the files to
 * copy ({ from, to }) and the manifest entries ({ [key]: { url, sigFile } }).
 */
export function plan({ files, platform, version, baseUrl }) {
  if (!PLATFORM.test(platform)) throw new Error(`Unknown updater platform: ${platform}`);
  if (!/^\d+\.\d+\.\d+([-+].+)?$/.test(version)) throw new Error(`Not a semantic version: ${version}`);
  const base = baseUrl.replace(/\/+$/, '');
  if (!base.startsWith('https://')) throw new Error(`The artifact base URL must use HTTPS: ${baseUrl}`);
  const copies = [];
  const platforms = {};
  for (const kind of KINDS) {
    const names = files[kind.dir] ?? [];
    for (const name of names.filter((entry) => entry.endsWith(kind.suffix))) {
      const from = join(kind.dir, name);
      const to = releaseName(name, version, platform);
      copies.push({ from, to });
      if (!kind.updater || !names.includes(`${name}.sig`)) continue;
      copies.push({ from: `${from}.sig`, to: `${to}.sig` });
      const entry = { url: `${base}/${encodeURIComponent(to)}`, sigFile: `${to}.sig` };
      platforms[`${platform}-${kind.installer}`] = entry;
      if (kind.primary) platforms[platform] = entry;
    }
  }
  return { copies, platforms };
}

/** Combines per-platform fragments into one manifest; a platform listed twice is an error. */
export function merge({ version, notes, pubDate, fragments }) {
  const platforms = {};
  for (const fragment of fragments) {
    if (fragment.version !== version) throw new Error(`Fragment for ${fragment.version} in a ${version} manifest.`);
    for (const [key, entry] of Object.entries(fragment.platforms)) {
      if (platforms[key]) throw new Error(`Platform ${key} appears in more than one fragment.`);
      if (!entry.signature?.trim() || !entry.url) throw new Error(`Platform ${key} has no signature or URL.`);
      platforms[key] = { signature: entry.signature, url: entry.url };
    }
  }
  if (!Object.keys(platforms).length) throw new Error('No signed updater artifacts: nothing to put in the manifest.');
  return { version, notes, pub_date: pubDate, platforms };
}

function options(argv) {
  const values = { files: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg.startsWith('--')) values[arg.slice(2)] = argv[(index += 1)];
    else values.files.push(arg);
  }
  return values;
}

function required(values, ...names) {
  for (const name of names) if (!values[name]) throw new Error(`--${name} is required.`);
}

function collect(values) {
  required(values, 'bundle-dir', 'platform', 'version', 'base-url', 'out');
  const files = {};
  for (const kind of KINDS) {
    const dir = join(values['bundle-dir'], kind.dir);
    if (existsSync(dir)) files[kind.dir] = readdirSync(dir);
  }
  const { copies, platforms } = plan({ files, platform: values.platform, version: values.version, baseUrl: values['base-url'] });
  if (!copies.length) throw new Error(`No installers found under ${values['bundle-dir']}.`);
  mkdirSync(values.out, { recursive: true });
  for (const { from, to } of copies) copyFileSync(join(values['bundle-dir'], from), join(values.out, to));
  console.log(`Copied ${copies.length} files to ${values.out}`);
  if (!Object.keys(platforms).length) {
    console.warn('warning: no signed updater artifacts (.sig) found; no manifest fragment written.');
    return;
  }
  const entries = Object.fromEntries(
    Object.entries(platforms).map(([key, { url, sigFile }]) => [key, { url, signature: readFileSync(join(values.out, sigFile), 'utf8').trim() }]),
  );
  const fragment = join(values.out, `latest-${values.platform}.json`);
  writeFileSync(fragment, `${JSON.stringify({ version: values.version, platforms: entries }, null, 2)}\n`);
  console.log(`Wrote ${fragment}`);
}

function mergeFiles(values) {
  required(values, 'version', 'out');
  const fragments = values.files.map((file) => JSON.parse(readFileSync(file, 'utf8')));
  const manifest = merge({ version: values.version, notes: values.notes ?? '', pubDate: new Date().toISOString(), fragments });
  writeFileSync(values.out, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`Wrote ${values.out} (${Object.keys(manifest.platforms).join(', ')})`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [command, ...rest] = process.argv.slice(2);
  const values = options(rest);
  if (command === 'collect') collect(values);
  else if (command === 'merge') mergeFiles(values);
  else {
    console.error('usage: updater-manifest.mjs collect|merge [options]');
    process.exit(2);
  }
}
