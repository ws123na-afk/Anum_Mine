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
| `ANUM_DESKTOP_VERSION` | Release version (`1.2.3`), overriding `tauri.conf.json`; the release pipeline sets it from the tag. The updater compares it with the manifest's version. |

Private material never reaches the repository or the overlay: the Tauri CLI reads the updater private key from `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and macOS signing and notarization from `APPLE_SIGNING_IDENTITY`, `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID`, all as CI secrets. Missing values produce warnings, not failures, so an unsigned build still works locally. `scripts/release-config.test.mjs` runs in `pnpm check:desktop`.

## Updates

`tauri-plugin-updater` (pinned to 2.12.0) is registered at startup only when the build carries `plugins.updater` with a public key and an endpoint, that is, a release build made with `ANUM_TAURI_UPDATER_PUBKEY` and `ANUM_TAURI_UPDATER_ENDPOINT` (`src-tauri/src/updater.rs`). Otherwise the plugin is not loaded, nothing contacts an update server, and the tray menu has no update item. When configured:

- The app checks once at startup and the tray menu gains "Check for updates". A found update is offered in a dialog ("Install and restart" or "Later"). The startup check stays silent when there is nothing new or the server is unreachable; the tray check reports either.
- The updater downloads the platform's artifact named in the manifest, verifies its minisign signature against the public key compiled into the build, installs it and restarts. An unsigned or wrongly signed artifact is refused.
- Checks run in the Rust shell; the webview gets no updater permission, so the capability manifest is unchanged.

`scripts/updater-manifest.mjs` writes the manifest (`latest.json`): `collect` copies a build's installers under release-unique names and turns each signed updater artifact (the NSIS and MSI installers on Windows, the `.app.tar.gz` on macOS) into a platform entry with its URL and signature, and `merge` combines the platforms. Its tests (`scripts/updater-manifest.test.mjs`) run in `pnpm check:desktop`. Updates only move forward: a client never installs a version lower than its own.

## Release Pipeline

`.github/workflows/release-clients.yml` builds the desktop installers on `windows-latest` (x86_64) and `macos-latest` (Apple silicon; Intel Macs are not built yet) with `pnpm --filter @anum/desktop build:release`, on every `v*` tag and on manual runs. The same workflow builds the mobile apps ([Flutter mobile](mobile.md#release-pipeline)). Each signing step runs only when its secrets exist; without them the build still runs unsigned and the job passes with a notice, so the workflow never fails for lack of the owner's credentials.

Repository variables (Settings, Secrets and variables, Actions, Variables):

| Variable | Value |
| --- | --- |
| `ANUM_PRODUCTION_API_URL` | Production API origin (HTTPS), compiled into the web bundle and the CSP. Shared with the mobile build. |
| `ANUM_PRODUCTION_OIDC_ISSUER` | Production Keycloak issuer (HTTPS). Shared with the mobile build. |
| `ANUM_TAURI_UPDATER_PUBKEY` | Contents of the `.pub` file from `pnpm --filter @anum/desktop exec tauri signer generate -w anum-updater.key` (run outside the repository). |
| `ANUM_TAURI_UPDATER_ENDPOINT` | Manifest URL. For GitHub releases of a public repository: `https://github.com/<owner>/<repo>/releases/latest/download/latest.json`. |
| `ANUM_TAURI_UPDATER_ARTIFACT_BASE_URL` | Optional. Where clients download the installers from; defaults to the tag's GitHub release download URL. Set it when the repository is private and the files are mirrored to public storage. |
| `ANUM_WINDOWS_CERTIFICATE_THUMBPRINT` | Thumbprint of the Authenticode certificate in the PFX secret below. `ANUM_WINDOWS_TIMESTAMP_URL` optionally replaces the timestamp server. |

Repository secrets:

| Secret | Value |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | The private key file from `tauri signer generate` and its password. Without them no updater artifacts or manifest are produced, even when the public key variable is set. Losing the key means installed clients can never be updated again, so keep an offline backup. |
| `ANUM_WINDOWS_CERTIFICATE_PFX_BASE64`, `ANUM_WINDOWS_CERTIFICATE_PASSWORD` | The Authenticode certificate as a base64 PFX (`base64 -w0 cert.pfx`) and its password, imported into the runner's user certificate store for the build. A certificate held in a cloud HSM needs a custom `signCommand` instead. |
| `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY` | Developer ID Application certificate as a base64 `.p12`, its password, and the identity name (`Developer ID Application: <Name> (<TEAM>)`). |
| `APPLE_TEAM_ID`, `APP_STORE_CONNECT_API_KEY_ID`, `APP_STORE_CONNECT_API_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_P8` | Shared with iOS ([Flutter mobile](mobile.md#release-pipeline)); here the API key notarizes the macOS app. macOS signing needs both the certificate and the key. |

Each desktop job uploads its installers, their SHA-256 files and (when signed for updates) a manifest fragment as the workflow artifact `anum-desktop-<platform>`. On a tag, the `publish` job merges the fragments into `latest.json` and attaches everything to a draft GitHub release named after the tag. It publishes nothing (artifacts only) when the production API and issuer variables are missing, so a release never carries a build that talks to local services.

Cutting a release:

1. On `main`, set `version` in `apps/desktop/src-tauri/tauri.conf.json`, `apps/desktop/src-tauri/Cargo.toml` and `apps/mobile/pubspec.yaml` to the new `X.Y.Z` (the tag overrides the build; the files keep manual runs consistent).
2. Tag that commit `vX.Y.Z` and push the tag. Tags must be plain `vX.Y.Z`: MSI installers and the stores reject other shapes, and the workflow refuses them.
3. Read the run summary (checksums, and notices about anything left unsigned), install the draft release's installers on a test machine, then publish the draft. Publishing is what makes `releases/latest/download/latest.json` point at the new version, so installed clients only see it from then on.

Rolling back: the updater never downgrades, so a bad desktop release is fixed forward. Unpublish or delete the bad GitHub release at once (the `latest` URL falls back to the previous published release, which stops further installs and updates to the bad version), then tag a new patch version with the fix or from the last good commit. Users who already installed the bad version move to the patch through the normal update prompt; installing the previous installer by hand also works.

The pipeline has not run yet: it needs the variables and secrets above, and the signed and macOS paths cannot be exercised from a development container.

## Release Gate

A signed installer still requires a Windows code-signing certificate, Apple signing and notarization credentials and the updater key pair in the repository settings ([Release pipeline](#release-pipeline)), then a first signed release installed and updated on Windows and macOS. Local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache remain future capabilities.
