# Desktop

The ANUM desktop app should use Tauri to package the web experience with controlled native capabilities. Desktop should add value through local context, shortcuts, files, notifications, and OS integration, not by forking the product.

## Role

Desktop should share the React+TypeScript UI where practical. Native code should be limited to capabilities that require OS access: filesystem pickers, secure local storage, notifications, tray controls, hotkeys, local capture, and later local tool execution.

## Security Model

Tauri permissions must be narrow. Local filesystem access should be user-selected and scoped. Desktop tools should still route through ANUM policy and audit logging. Local credentials should use platform secure storage when available.

## Runtime Relationship

The desktop app is a client, not a separate agent brain. It can provide local signals and native actions to the backend runtime through approved tool interfaces. Offline behavior should be limited until conflict handling and encrypted local storage are designed.

## Implemented

The Tauri v2 shell reuses the production web build and includes a scoped capability manifest, tray controls, notifications, dialogs, external-link opening, and a global task-launcher shortcut. CI performs a Rust compile check on Windows.

## Sign-In

The shell signs in through Keycloak with the shared web code when the web build is made with `VITE_ANUM_OIDC_ISSUER` and `VITE_ANUM_OIDC_CLIENT_ID=anum-desktop` (authorization code + PKCE, tokens in memory, refresh token in the webview's `sessionStorage`). The login runs in the user's default browser and returns through an RFC 8252 loopback redirect, so the shell never sees the password and the user's existing browser session, password manager and security keys work:

1. Sign-in calls the shell command `oidc_loopback_listen`, which binds `127.0.0.1` on an ephemeral port (loopback interface only) and returns the port.
2. The web client (`apps/web/src/lib/auth.ts`) starts the request with redirect URI `http://127.0.0.1:<port>/callback`, a fresh state, nonce and PKCE verifier, and passes the authorization URL to `oidc_loopback_authorize`.
3. The shell opens the URL in the system browser (HTTPS only, or HTTP to a local identity provider), waits for exactly one `GET /callback?...`, answers it with a static "you can close this tab" page, focuses the ANUM window and hands the callback URL back to the web client.
4. The web client checks state, expiry and nonce and redeems the code with the same loopback redirect URI, as in the browser flow.

The listener gives up after ten minutes, and pressing Sign in again replaces a pending attempt. Other requests to the port (for example a favicon) get a 404 and do not end the wait; a forged callback from another local process fails the state check. Sign-out forgets the tokens in the shell and opens the end-session URL (with `id_token_hint`, without a post-logout redirect) in the system browser, where the Keycloak session lives. The commands are in `apps/desktop/src-tauri/src/oidc_loopback.rs`, without extra plugins; the webview calls them through `window.__TAURI_INTERNALS__` (`apps/web/src/lib/desktop.ts`), so the web build gains no Tauri dependency and a normal browser keeps the redirect flow.

The `anum-desktop` realm client already allows `http://127.0.0.1/*`, and Keycloak matches a loopback redirect URI registered without a port on any port, as RFC 8252 section 7.3 requires. The round trip has not yet been run against a live Keycloak from a packaged build; do that (Windows and macOS, including cancel, retry and sign-out) before the desktop beta channel opens. The CSP's `connect-src` must include the issuer origin for the token request; release builds add it (below). See [Identity and sign-in](identity.md#web-and-desktop).

## Release Builds

`pnpm --filter @anum/desktop build:release` runs `scripts/release-config.mjs`, which writes the gitignored overlay `src-tauri/tauri.release.conf.json` from the environment, then `tauri build --config` with it. The committed `tauri.conf.json` holds no keys, and the overlay holds only public values:

| Variable | Effect |
| --- | --- |
| `VITE_ANUM_OIDC_ISSUER`, `VITE_ANUM_API_URL` | Their HTTPS origins (and `wss://` for the API) are added to the CSP `connect-src`. Plain HTTP is refused. Use the same values for the web build. |
| `ANUM_TAURI_UPDATER_PUBKEY` | Updater public key (the `.pub` file from `tauri signer generate`). Turns on `createUpdaterArtifacts` and sets `plugins.updater.pubkey`. |
| `ANUM_TAURI_UPDATER_ENDPOINT` | Required with the public key: the HTTPS update manifest URL (may use `{{target}}` and `{{current_version}}`). |
| `ANUM_WINDOWS_CERTIFICATE_THUMBPRINT` | Authenticode certificate thumbprint in the build machine's certificate store; signs with SHA-256 and timestamps through `ANUM_WINDOWS_TIMESTAMP_URL` (DigiCert's by default). |

Private material never reaches the repository or the overlay: the Tauri CLI reads the updater private key from `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and macOS signing and notarization from `APPLE_SIGNING_IDENTITY`, `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID`, all as CI secrets. Missing values produce warnings, not failures, so an unsigned build still works locally. `scripts/release-config.test.mjs` runs in `pnpm check:desktop`.

The overlay prepares signed updater artifacts; checking for and installing updates at runtime additionally needs `tauri-plugin-updater` registered in `src-tauri/src/lib.rs` with its capability, which is added together with the first update endpoint.

## Release Gate

A signed installer still requires the MSVC C++ linker toolchain, a Windows code-signing certificate, Apple notarization credentials and the updater key pair. Local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache remain future capabilities.
