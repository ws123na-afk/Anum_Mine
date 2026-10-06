# ANUM Desktop

The desktop app is a Tauri v2 shell around the shared `@anum/web` client. The backend remains the source of truth for agents, policy, approvals, and audit records.

## Development

Prerequisites: Node.js, pnpm, Rust, and the platform-specific Tauri dependencies.

```sh
pnpm --filter @anum/desktop dev
```

The desktop package starts the existing web Vite server and loads it at `http://localhost:5173`. Production builds compile `@anum/web` first and bundle `apps/web/dist`.

## Sign-in

Build the web client with `VITE_ANUM_OIDC_ISSUER=<issuer>` and `VITE_ANUM_OIDC_CLIENT_ID=anum-desktop` to sign in through Keycloak; without them the shell uses the local development session. The login opens in the system browser and returns through a one-shot loopback listener on `127.0.0.1` (`src-tauri/src/oidc_loopback.rs`, RFC 8252). See [docs/desktop.md](../../docs/desktop.md#sign-in).

## Release builds

```sh
pnpm --filter @anum/desktop build:release
```

writes `src-tauri/tauri.release.conf.json` (gitignored) from the environment: production issuer and API origins for the CSP, the updater public key and endpoint, and the Windows certificate thumbprint. Private keys are read by the Tauri CLI from CI secrets and are never committed. See [docs/desktop.md](../../docs/desktop.md#release-builds). Tagged releases are built by `.github/workflows/release-clients.yml`, which also writes the updater manifest with `scripts/updater-manifest.mjs` ([docs/desktop.md](../../docs/desktop.md#release-pipeline)).

## Native boundary

The default capability grants only window controls, notifications, user-driven open/save dialogs, opening external links, and one registered global shortcut. The app's own commands are `desktop_context` and the three sign-in commands (`oidc_loopback_listen`, `oidc_loopback_authorize`, `oidc_open_browser`), which only open HTTPS (or local HTTP) identity-provider URLs. Arbitrary filesystem and shell access are intentionally absent. Release builds with an updater key and endpoint also load `tauri-plugin-updater` from Rust (startup check and a tray "Check for updates" item, `src-tauri/src/updater.rs`); the webview gets no updater permission. See [docs/desktop.md](../../docs/desktop.md#updates). Files selected through a dialog are sent to the web client as paths; backend upload and policy enforcement remain application responsibilities.
