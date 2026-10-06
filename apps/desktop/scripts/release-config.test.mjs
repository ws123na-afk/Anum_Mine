// Run with `node --test scripts/release-config.test.mjs` from apps/desktop (part of `pnpm check:desktop`).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { BASE_CONFIG, extendCsp, releaseConfig } from './release-config.mjs';

const base = JSON.parse(readFileSync(BASE_CONFIG, 'utf8'));

test('the committed config holds no updater key or signing identity', () => {
  assert.equal(base.plugins?.updater, undefined);
  assert.equal(base.bundle.createUpdaterArtifacts, undefined);
  assert.equal(base.bundle.windows.certificateThumbprint, undefined);
});

test('production origins are added to connect-src only', () => {
  const { config } = releaseConfig({ VITE_ANUM_OIDC_ISSUER: 'https://id.anum.example/realms/anum', VITE_ANUM_API_URL: 'https://api.anum.example/' }, base);
  const csp = config.app.security.csp;
  const connect = csp.split(';').map((part) => part.trim()).find((part) => part.startsWith('connect-src'));
  assert.match(connect, / https:\/\/id\.anum\.example( |$)/);
  assert.match(connect, / https:\/\/api\.anum\.example( |$)/);
  assert.match(connect, / wss:\/\/api\.anum\.example( |$)/);
  assert.match(csp, /script-src 'self'/);
  assert.doesNotMatch(csp.replace(connect, ''), /anum\.example/);
});

test('plain-HTTP production origins are refused', () => {
  assert.throws(() => releaseConfig({ VITE_ANUM_OIDC_ISSUER: 'http://id.anum.example/realms/anum' }, base), /HTTPS/);
});

test('the updater needs a public key and an HTTPS endpoint', () => {
  const { config } = releaseConfig({ ANUM_TAURI_UPDATER_PUBKEY: 'cHVibGljLWtleQ==', ANUM_TAURI_UPDATER_ENDPOINT: 'https://updates.anum.example/{{target}}/{{current_version}}', TAURI_SIGNING_PRIVATE_KEY: 'set-in-ci' }, base);
  assert.equal(config.bundle.createUpdaterArtifacts, true);
  assert.deepEqual(config.plugins.updater, { pubkey: 'cHVibGljLWtleQ==', endpoints: ['https://updates.anum.example/{{target}}/{{current_version}}'] });
  assert.doesNotMatch(JSON.stringify(config), /set-in-ci/, 'the private key never reaches the overlay');
  assert.throws(() => releaseConfig({ ANUM_TAURI_UPDATER_PUBKEY: 'k' }, base), /ANUM_TAURI_UPDATER_ENDPOINT/);
});

test('Windows signing uses the certificate thumbprint with SHA-256', () => {
  const { config } = releaseConfig({ ANUM_WINDOWS_CERTIFICATE_THUMBPRINT: 'ABCDEF' }, base);
  assert.equal(config.bundle.windows.certificateThumbprint, 'ABCDEF');
  assert.equal(config.bundle.windows.digestAlgorithm, 'sha256');
});

test('an empty environment produces an empty overlay with warnings', () => {
  const { config, warnings } = releaseConfig({}, base);
  assert.deepEqual(config, {});
  assert.equal(warnings.length, 3);
});

test('extendCsp appends without duplicates', () => {
  assert.equal(extendCsp("default-src 'self'; connect-src 'self'", 'connect-src', ['https://a', "'self'"]), "default-src 'self'; connect-src 'self' https://a");
  assert.equal(extendCsp("default-src 'self'", 'connect-src', ['https://a']), "default-src 'self'; connect-src https://a");
});
