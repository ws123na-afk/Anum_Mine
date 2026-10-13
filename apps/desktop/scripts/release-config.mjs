// Writes src-tauri/tauri.release.conf.json, a Tauri config overlay for release builds, from the
// environment, and with `--build` runs the release build with it. Nothing secret is written: the
// API and identity-provider addresses, the updater *public* key, endpoint, CSP origins and the
// Windows certificate thumbprint are public values. Private material stays in CI secrets and is
// read by the Tauri CLI itself (TAURI_SIGNING_PRIVATE_KEY, TAURI_SIGNING_PRIVATE_KEY_PASSWORD,
// APPLE_* for notarization). See docs/desktop.md, "Release Builds".
//
//   node scripts/release-config.mjs --build      (pnpm --filter @anum/desktop build:release)
//
// A release build always signs in through Keycloak: the production API and issuer default to the
// owner's deployment (DEFAULT_RELEASE_ENV) and can be replaced with VITE_ANUM_API_URL and
// VITE_ANUM_OIDC_ISSUER. Plain HTTP, local hosts and a missing value stop the build, and the
// built web bundle is checked for the issuer, so a release never falls back to the local
// development session.
import { spawnSync } from 'node:child_process';
import { appendFileSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const APP_DIR = join(here, '..');
export const BASE_CONFIG = join(APP_DIR, 'src-tauri', 'tauri.conf.json');
export const RELEASE_CONFIG = join(APP_DIR, 'src-tauri', 'tauri.release.conf.json');
export const WEB_DIST = join(APP_DIR, '..', 'web', 'dist');

/** The owner's production deployment (docs/deploy-oracle.md). Public addresses, not secrets. */
export const DEFAULT_RELEASE_ENV = Object.freeze({
  VITE_ANUM_API_URL: 'https://api.anum360os.com',
  VITE_ANUM_OIDC_ISSUER: 'https://auth.anum360os.com/realms/anum',
  VITE_ANUM_OIDC_CLIENT_ID: 'anum-desktop',
});

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '0.0.0.0']);

function parse(value, name) {
  try {
    return new URL(value);
  } catch {
    throw new Error(`${name} is not a URL: ${value}`);
  }
}

function origin(value, name) {
  const url = parse(value, name);
  if (url.protocol !== 'https:' && url.protocol !== 'wss:') throw new Error(`${name} must use HTTPS in a release build: ${value}`);
  return url.origin;
}

/** A production address: HTTPS, not a local host, no query or fragment. Returned without trailing slashes. */
function productionUrl(value, name) {
  const url = parse(value, name);
  if (url.protocol !== 'https:') throw new Error(`${name} must use HTTPS in a release build: ${value}`);
  if (LOCAL_HOSTS.has(url.hostname) || url.hostname.endsWith('.localhost')) throw new Error(`${name} points at a local host; a release build must use the production address: ${value}`);
  if (url.search || url.hash) throw new Error(`${name} must not carry a query or fragment: ${value}`);
  return `${url.origin}${url.pathname}`.replace(/\/+$/, '');
}

/**
 * The web-build variables of a release: the API base URL, the Keycloak issuer and the desktop
 * client id, from the environment or DEFAULT_RELEASE_ENV (an empty value counts as unset).
 * Returns { env, warnings }; throws on a value that would make a broken or local release.
 */
export function releaseEnv(env) {
  const warnings = [];
  const pick = (name) => {
    const value = typeof env[name] === 'string' ? env[name].trim() : '';
    return value || DEFAULT_RELEASE_ENV[name];
  };
  const api = productionUrl(pick('VITE_ANUM_API_URL'), 'VITE_ANUM_API_URL');
  const issuer = productionUrl(pick('VITE_ANUM_OIDC_ISSUER'), 'VITE_ANUM_OIDC_ISSUER');
  if (!/\/realms\/[^/]+$/.test(new URL(issuer).pathname)) throw new Error(`VITE_ANUM_OIDC_ISSUER must be a Keycloak realm issuer (https://<auth host>/realms/<realm>): ${issuer}`);
  const clientId = pick('VITE_ANUM_OIDC_CLIENT_ID');
  if (!/^[A-Za-z0-9._-]+$/.test(clientId)) throw new Error(`VITE_ANUM_OIDC_CLIENT_ID is not a client id: ${clientId}`);
  if (clientId !== DEFAULT_RELEASE_ENV.VITE_ANUM_OIDC_CLIENT_ID) warnings.push(`VITE_ANUM_OIDC_CLIENT_ID is ${clientId}, not anum-desktop: that Keycloak client must allow the loopback redirect http://127.0.0.1/*.`);
  for (const [name, value] of [['VITE_ANUM_API_URL', api], ['VITE_ANUM_OIDC_ISSUER', issuer]]) {
    if (new URL(value).hostname.endsWith('.duckdns.org')) warnings.push(`${name} still points at a DuckDNS host (${value}); the production addresses moved to anum360os.com.`);
  }
  return { env: { VITE_ANUM_API_URL: api, VITE_ANUM_OIDC_ISSUER: issuer, VITE_ANUM_OIDC_CLIENT_ID: clientId }, warnings };
}

/** Appends sources to one CSP directive, keeping the others as they are. */
export function extendCsp(csp, directive, sources) {
  const parts = csp.split(';').map((part) => part.trim()).filter(Boolean);
  const index = parts.findIndex((part) => part.split(/\s+/)[0] === directive);
  if (index < 0) return [...parts, `${directive} ${sources.join(' ')}`].join('; ');
  const existing = parts[index].split(/\s+/);
  parts[index] = [...existing, ...sources.filter((source) => !existing.includes(source))].join(' ');
  return parts.join('; ');
}

/**
 * Builds the overlay. Returns { config, env, warnings }, where env holds the web-build variables
 * (releaseEnv); throws on values that would produce a broken or insecure release (plain-HTTP or
 * local origins, an updater key without an endpoint).
 */
export function releaseConfig(env, base) {
  const config = {};
  const { env: webEnv, warnings } = releaseEnv(env);

  // The release tag's version (v1.2.3 -> 1.2.3); the updater compares it with the manifest's.
  const version = env.ANUM_DESKTOP_VERSION?.trim();
  if (version) {
    if (!/^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$/.test(version)) throw new Error(`ANUM_DESKTOP_VERSION is not a semantic version: ${version}`);
    config.version = version;
  }

  // The token request goes to the issuer and every API call (and the event stream) to the API.
  const connect = [
    origin(webEnv.VITE_ANUM_OIDC_ISSUER, 'VITE_ANUM_OIDC_ISSUER'),
    origin(webEnv.VITE_ANUM_API_URL, 'VITE_ANUM_API_URL'),
    origin(webEnv.VITE_ANUM_API_URL, 'VITE_ANUM_API_URL').replace(/^https:/, 'wss:'),
  ];
  config.app = { security: { csp: extendCsp(base.app.security.csp, 'connect-src', connect) } };

  const pubkey = env.ANUM_TAURI_UPDATER_PUBKEY?.trim();
  if (pubkey) {
    const endpoint = env.ANUM_TAURI_UPDATER_ENDPOINT?.trim();
    if (!endpoint) throw new Error('ANUM_TAURI_UPDATER_ENDPOINT is required with ANUM_TAURI_UPDATER_PUBKEY.');
    origin(endpoint, 'ANUM_TAURI_UPDATER_ENDPOINT');
    if (!env.TAURI_SIGNING_PRIVATE_KEY) warnings.push('TAURI_SIGNING_PRIVATE_KEY is unset: tauri build cannot sign the updater artifacts.');
    config.bundle = { createUpdaterArtifacts: true };
    config.plugins = { updater: { pubkey, endpoints: [endpoint] } };
  } else {
    warnings.push('ANUM_TAURI_UPDATER_PUBKEY is unset: no updater artifacts are produced.');
  }

  const thumbprint = env.ANUM_WINDOWS_CERTIFICATE_THUMBPRINT?.trim();
  if (thumbprint) {
    config.bundle = {
      ...config.bundle,
      windows: {
        certificateThumbprint: thumbprint,
        digestAlgorithm: 'sha256',
        timestampUrl: env.ANUM_WINDOWS_TIMESTAMP_URL?.trim() || 'http://timestamp.digicert.com',
      },
    };
  } else {
    warnings.push('ANUM_WINDOWS_CERTIFICATE_THUMBPRINT is unset: Windows installers are not Authenticode-signed.');
  }
  return { config, env: webEnv, warnings };
}

/**
 * Checks that the built web bundle signs in through Keycloak: some JavaScript file in `dist`
 * must contain the issuer and the client id. Returns an error message, or null.
 */
export function checkWebBundle(files, env) {
  const missing = ['VITE_ANUM_OIDC_ISSUER', 'VITE_ANUM_OIDC_CLIENT_ID', 'VITE_ANUM_API_URL'].filter((name) => !files.some((text) => text.includes(env[name])));
  if (!missing.length) return null;
  return `The web bundle does not contain ${missing.join(', ')}; the desktop app would not sign in to production. Rebuild without a stale apps/web/dist.`;
}

function readBundle(dir) {
  const files = [];
  const walk = (path) => {
    for (const entry of readdirSync(path, { withFileTypes: true })) {
      const full = join(path, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.js')) files.push(readFileSync(full, 'utf8'));
    }
  };
  walk(dir);
  return files;
}

function main(argv) {
  const ci = process.env.GITHUB_ACTIONS === 'true';
  const base = JSON.parse(readFileSync(BASE_CONFIG, 'utf8'));
  const { config, env, warnings } = releaseConfig(process.env, base);
  for (const warning of warnings) console.warn(ci ? `::warning title=Desktop release::${warning}` : `warning: ${warning}`);
  writeFileSync(RELEASE_CONFIG, `${JSON.stringify(config, null, 2)}\n`);
  console.log(`Wrote ${RELEASE_CONFIG}`);
  const summary = [`API: ${env.VITE_ANUM_API_URL}`, `Sign-in: ${env.VITE_ANUM_OIDC_ISSUER} (client ${env.VITE_ANUM_OIDC_CLIENT_ID})`];
  for (const line of summary) console.log(line);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${summary.map((line) => `- Desktop ${line}`).join('\n')}\n`);
  if (!argv.includes('--build')) return 0;

  // The Tauri CLI runs beforeBuildCommand (the web build) with this environment.
  const cli = join(dirname(createRequire(import.meta.url).resolve('@tauri-apps/cli/package.json')), 'tauri.js');
  const result = spawnSync(process.execPath, [cli, 'build', '--config', RELEASE_CONFIG], { cwd: APP_DIR, stdio: 'inherit', env: { ...process.env, ...env } });
  if (result.status !== 0) return result.status ?? 1;
  const problem = checkWebBundle(readBundle(WEB_DIST), env);
  if (problem) {
    console.error(ci ? `::error title=Desktop release::${problem}` : `error: ${problem}`);
    return 1;
  }
  console.log('The web bundle signs in through Keycloak with the values above.');
  return 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(`error: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
