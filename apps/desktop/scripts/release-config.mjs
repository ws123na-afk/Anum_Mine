// Writes src-tauri/tauri.release.conf.json, a Tauri config overlay for release builds, from the
// environment. Nothing secret is written: the updater *public* key, endpoint, CSP origins and the
// Windows certificate thumbprint are public values. Private material stays in CI secrets and is
// read by the Tauri CLI itself (TAURI_SIGNING_PRIVATE_KEY, TAURI_SIGNING_PRIVATE_KEY_PASSWORD,
// APPLE_* for notarization). See docs/desktop.md, "Release Builds".
//
//   node scripts/release-config.mjs && tauri build --config src-tauri/tauri.release.conf.json
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const BASE_CONFIG = join(here, '..', 'src-tauri', 'tauri.conf.json');
export const RELEASE_CONFIG = join(here, '..', 'src-tauri', 'tauri.release.conf.json');

function origin(value, name) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} is not a URL: ${value}`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'wss:') throw new Error(`${name} must use HTTPS in a release build: ${value}`);
  return url.origin;
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
 * Builds the overlay. Returns { config, warnings }; throws on values that would produce a broken
 * or insecure release (plain-HTTP origins, an updater key without an endpoint).
 */
export function releaseConfig(env, base) {
  const config = {};
  const warnings = [];

  const connect = [];
  for (const name of ['VITE_ANUM_OIDC_ISSUER', 'VITE_ANUM_API_URL']) {
    if (env[name]) connect.push(origin(env[name], name));
  }
  if (env.VITE_ANUM_API_URL) connect.push(origin(env.VITE_ANUM_API_URL, 'VITE_ANUM_API_URL').replace(/^https:/, 'wss:'));
  if (connect.length) {
    config.app = { security: { csp: extendCsp(base.app.security.csp, 'connect-src', connect) } };
  } else {
    warnings.push('VITE_ANUM_OIDC_ISSUER and VITE_ANUM_API_URL are unset: the CSP only allows local services.');
  }

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
  return { config, warnings };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const base = JSON.parse(readFileSync(BASE_CONFIG, 'utf8'));
  const { config, warnings } = releaseConfig(process.env, base);
  for (const warning of warnings) console.warn(`warning: ${warning}`);
  writeFileSync(RELEASE_CONFIG, `${JSON.stringify(config, null, 2)}\n`);
  console.log(`Wrote ${RELEASE_CONFIG}`);
}
