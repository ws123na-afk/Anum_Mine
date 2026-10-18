// Run with `node --test scripts/release-config.test.mjs` from apps/desktop (part of `pnpm check:desktop`).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { BASE_CONFIG, DEFAULT_RELEASE_ENV, TWILIO_CSP, checkWebBundle, extendCsp, releaseConfig, releaseEnv } from './release-config.mjs';

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

test('calls through ANUM get only the Twilio hosts the web client needs', () => {
  const { config } = releaseConfig({}, base);
  const directives = Object.fromEntries(config.app.security.csp.split(';').map((part) => part.trim().split(/\s+/)).map(([name, ...sources]) => [name, sources]));
  for (const source of ['wss://*.twilio.com', 'https://eventgw.twilio.com']) assert.ok(directives['connect-src'].includes(source), source);
  assert.deepEqual(directives['media-src'], ["'self'", 'blob:', 'https://sdk.twilio.com']);
  assert.deepEqual(directives['script-src'], ["'self'"], 'no Twilio script is loaded: the SDK is bundled');
  const twilio = config.app.security.csp.match(/[^\s;]*twilio[^\s;]*/g);
  assert.deepEqual(twilio.sort(), [...TWILIO_CSP['connect-src'], ...TWILIO_CSP['media-src']].sort(), 'no other Twilio host');
  assert.doesNotMatch(config.app.security.csp, /https:\/\/\*\.twilio\.com|api\.twilio\.com/, 'the REST API is reached by the server, never the app');
});

test('the macOS bundle asks for the microphone with a usage text and the audio-input entitlement', () => {
  const dir = new URL('../src-tauri/', import.meta.url);
  assert.equal(base.bundle.macOS.entitlements, './Entitlements.plist');
  const entitlements = readFileSync(new URL('Entitlements.plist', dir), 'utf8');
  assert.match(entitlements, /<key>com\.apple\.security\.device\.audio-input<\/key>\s*<true\/>/);
  assert.doesNotMatch(entitlements, /camera|app-sandbox/, 'only the microphone');
  const info = readFileSync(new URL('Info.plist', dir), 'utf8');
  assert.match(info, /<key>NSMicrophoneUsageDescription<\/key>\s*<string>[^<]{20,}<\/string>/);
  assert.doesNotMatch(info, /[\u2013\u2014]/);
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

test('an empty environment builds for the production deployment, with warnings for the unsigned parts', () => {
  const { config, env, warnings } = releaseConfig({}, base);
  assert.deepEqual(env, { VITE_ANUM_API_URL: 'https://api.anum360os.com', VITE_ANUM_OIDC_ISSUER: 'https://auth.anum360os.com/realms/anum', VITE_ANUM_OIDC_CLIENT_ID: 'anum-desktop' });
  const connect = config.app.security.csp.split(';').map((part) => part.trim()).find((part) => part.startsWith('connect-src'));
  for (const source of ['https://auth.anum360os.com', 'https://api.anum360os.com', 'wss://api.anum360os.com']) assert.ok(connect.split(' ').includes(source), source);
  assert.equal(config.plugins, undefined);
  assert.equal(config.bundle, undefined);
  assert.equal(warnings.length, 2);
});

test('empty CI variables fall back to the defaults instead of the local development session', () => {
  const { env } = releaseEnv({ VITE_ANUM_API_URL: '', VITE_ANUM_OIDC_ISSUER: '  ', VITE_ANUM_OIDC_CLIENT_ID: '' });
  assert.deepEqual(env, { ...DEFAULT_RELEASE_ENV });
});

test('overrides are normalised without trailing slashes', () => {
  const { env, warnings } = releaseEnv({ VITE_ANUM_API_URL: 'https://api.anum.example/', VITE_ANUM_OIDC_ISSUER: 'https://id.anum.example/realms/anum/' });
  assert.equal(env.VITE_ANUM_API_URL, 'https://api.anum.example');
  assert.equal(env.VITE_ANUM_OIDC_ISSUER, 'https://id.anum.example/realms/anum');
  assert.deepEqual(warnings, []);
});

test('a release never points at local services or a non-Keycloak issuer', () => {
  assert.throws(() => releaseEnv({ VITE_ANUM_API_URL: 'http://localhost:8000' }), /HTTPS/);
  assert.throws(() => releaseEnv({ VITE_ANUM_API_URL: 'https://127.0.0.1:8000' }), /local host/);
  assert.throws(() => releaseEnv({ VITE_ANUM_OIDC_ISSUER: 'https://localhost:8080/realms/anum' }), /local host/);
  assert.throws(() => releaseEnv({ VITE_ANUM_OIDC_ISSUER: 'https://auth.anum.example/' }), /realms/);
  assert.throws(() => releaseEnv({ VITE_ANUM_API_URL: 'https://api.anum.example/?x=1' }), /query/);
});

test('an old DuckDNS address is reported', () => {
  const { warnings } = releaseEnv({ VITE_ANUM_OIDC_ISSUER: 'https://anum-auth.duckdns.org/realms/anum' });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /DuckDNS/);
});

test('the built bundle must carry the issuer, client id and API', () => {
  const { env } = releaseEnv({});
  assert.equal(checkWebBundle([`const a="${env.VITE_ANUM_OIDC_ISSUER}",b="${env.VITE_ANUM_OIDC_CLIENT_ID}";`, `fetch("${env.VITE_ANUM_API_URL}/api/v1")`], env), null);
  assert.match(checkWebBundle(['fetch("http://localhost:8000/api/v1/auth/local/session")'], env), /VITE_ANUM_OIDC_ISSUER, VITE_ANUM_OIDC_CLIENT_ID, VITE_ANUM_API_URL/);
});

test('extendCsp appends without duplicates', () => {
  assert.equal(extendCsp("default-src 'self'; connect-src 'self'", 'connect-src', ['https://a', "'self'"]), "default-src 'self'; connect-src 'self' https://a");
  assert.equal(extendCsp("default-src 'self'", 'connect-src', ['https://a']), "default-src 'self'; connect-src https://a");
});

test('the release version comes from ANUM_DESKTOP_VERSION and must be semantic', () => {
  assert.equal(releaseConfig({}, base).config.version, undefined);
  assert.equal(releaseConfig({ ANUM_DESKTOP_VERSION: '1.2.3' }, base).config.version, '1.2.3');
  assert.equal(releaseConfig({ ANUM_DESKTOP_VERSION: '1.2.3-beta.1' }, base).config.version, '1.2.3-beta.1');
  assert.throws(() => releaseConfig({ ANUM_DESKTOP_VERSION: 'v1.2.3' }, base), /semantic/);
});
