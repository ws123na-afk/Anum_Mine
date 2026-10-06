// Run with `node --test scripts/updater-manifest.test.mjs` from apps/desktop (part of `pnpm check:desktop`).
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { test } from 'node:test';
import { merge, plan, releaseName } from './updater-manifest.mjs';

const base = 'https://github.com/anum/anum/releases/download/v0.2.0';

test('versioned installer names are kept; macOS bundles get version and platform', () => {
  assert.equal(releaseName('ANUM_0.2.0_x64-setup.exe', '0.2.0', 'windows-x86_64'), 'ANUM_0.2.0_x64-setup.exe');
  assert.equal(releaseName('ANUM.app.tar.gz', '0.2.0', 'darwin-aarch64'), 'ANUM_0.2.0_darwin-aarch64.app.tar.gz');
  assert.equal(releaseName('ANUM.dmg', '0.2.0', 'darwin-aarch64'), 'ANUM_0.2.0_darwin-aarch64.dmg');
});

test('signed Windows installers become nsis (default) and msi entries', () => {
  const { copies, platforms } = plan({
    files: {
      nsis: ['ANUM_0.2.0_x64-setup.exe', 'ANUM_0.2.0_x64-setup.exe.sig'],
      msi: ['ANUM_0.2.0_x64_en-US.msi', 'ANUM_0.2.0_x64_en-US.msi.sig'],
    },
    platform: 'windows-x86_64',
    version: '0.2.0',
    baseUrl: `${base}/`,
  });
  assert.deepEqual(Object.keys(platforms).sort(), ['windows-x86_64', 'windows-x86_64-msi', 'windows-x86_64-nsis']);
  assert.equal(platforms['windows-x86_64'].url, `${base}/ANUM_0.2.0_x64-setup.exe`);
  assert.equal(platforms['windows-x86_64-msi'].sigFile, 'ANUM_0.2.0_x64_en-US.msi.sig');
  assert.deepEqual(copies[0], { from: join('nsis', 'ANUM_0.2.0_x64-setup.exe'), to: 'ANUM_0.2.0_x64-setup.exe' });
  assert.equal(copies.length, 4);
});

test('macOS uses the signed app archive; the dmg is copied but not an update', () => {
  const { copies, platforms } = plan({
    files: { macos: ['ANUM.app', 'ANUM.app.tar.gz', 'ANUM.app.tar.gz.sig'], dmg: ['ANUM_0.2.0_aarch64.dmg'] },
    platform: 'darwin-aarch64',
    version: '0.2.0',
    baseUrl: base,
  });
  assert.deepEqual(Object.keys(platforms).sort(), ['darwin-aarch64', 'darwin-aarch64-app']);
  assert.equal(platforms['darwin-aarch64'].url, `${base}/ANUM_0.2.0_darwin-aarch64.app.tar.gz`);
  assert.deepEqual(copies.map((copy) => copy.to), [
    'ANUM_0.2.0_darwin-aarch64.app.tar.gz',
    'ANUM_0.2.0_darwin-aarch64.app.tar.gz.sig',
    'ANUM_0.2.0_aarch64.dmg',
  ]);
});

test('unsigned builds copy installers and produce no manifest entries', () => {
  const { copies, platforms } = plan({
    files: { nsis: ['ANUM_0.2.0_x64-setup.exe'] },
    platform: 'windows-x86_64',
    version: '0.2.0',
    baseUrl: base,
  });
  assert.equal(copies.length, 1);
  assert.deepEqual(platforms, {});
});

test('bad platform, version or plain-HTTP base URL are refused', () => {
  assert.throws(() => plan({ files: {}, platform: 'windows-x64', version: '0.2.0', baseUrl: base }), /platform/);
  assert.throws(() => plan({ files: {}, platform: 'windows-x86_64', version: 'v0.2.0', baseUrl: base }), /semantic/);
  assert.throws(() => plan({ files: {}, platform: 'windows-x86_64', version: '0.2.0', baseUrl: 'http://x.example' }), /HTTPS/);
});

test('fragments merge into one manifest', () => {
  const manifest = merge({
    version: '0.2.0',
    notes: 'Notes',
    pubDate: '2026-10-06T00:00:00.000Z',
    fragments: [
      { version: '0.2.0', platforms: { 'windows-x86_64': { url: `${base}/a.exe`, signature: 'sig-a' } } },
      { version: '0.2.0', platforms: { 'darwin-aarch64': { url: `${base}/b.app.tar.gz`, signature: 'sig-b' } } },
    ],
  });
  assert.deepEqual(manifest, {
    version: '0.2.0',
    notes: 'Notes',
    pub_date: '2026-10-06T00:00:00.000Z',
    platforms: {
      'windows-x86_64': { url: `${base}/a.exe`, signature: 'sig-a' },
      'darwin-aarch64': { url: `${base}/b.app.tar.gz`, signature: 'sig-b' },
    },
  });
});

test('merge refuses mismatched versions, duplicates, unsigned entries and empty manifests', () => {
  const entry = { url: `${base}/a.exe`, signature: 'sig' };
  const args = { version: '0.2.0', notes: '', pubDate: '' };
  assert.throws(() => merge({ ...args, fragments: [{ version: '0.1.0', platforms: { 'windows-x86_64': entry } }] }), /0\.1\.0/);
  assert.throws(
    () => merge({ ...args, fragments: [{ version: '0.2.0', platforms: { 'windows-x86_64': entry } }, { version: '0.2.0', platforms: { 'windows-x86_64': entry } }] }),
    /more than one/,
  );
  assert.throws(() => merge({ ...args, fragments: [{ version: '0.2.0', platforms: { 'windows-x86_64': { url: entry.url, signature: ' ' } } }] }), /signature/);
  assert.throws(() => merge({ ...args, fragments: [] }), /No signed/);
});
