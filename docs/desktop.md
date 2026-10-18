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

The webview's capability manifest (`src-tauri/capabilities/default.json`) grants only what the web code calls: core, the file dialogs and notifications. Opening links and the launcher shortcut run in Rust (`oidc_open_browser`, which accepts only `https` or a local `http` identity provider, and the shortcut handler), so the webview has no `opener` or `global-shortcut` permission and cannot open arbitrary URLs or schemes through the plugin (full skills audit, October 2026).

Because the shell renders the web build (`frontendDist: ../../web/dist`), every web screen is the desktop screen. "Sources used" on a task's run ([Memory](memory.md#at-run-time)) therefore works on desktop exactly as on the web, with no desktop code: the panel lists each memory and file the run's prompt used by name and kind (a memory's first line, a file's name and type, resolved in one `GET /api/v1/retrieval/sources` request), with its id, passage number, match score and whether it was shortened; it explains a retrieval that found nothing, was skipped or was unavailable, and is absent for a run without a `retrieval` step. Clicking a memory opens the Memory view scrolled to it and marked "Used by the run you came from"; clicking a file opens its detail in the Files view; a source that no longer exists shows its id and is not a link. The name lookup has loading, error-with-retry and permission-denied states ([Memory](memory.md#at-run-time)). The desktop window opens at 1440x900 and shrinks to 960x640; `apps/web/e2e/sources-used.spec.ts` renders the panel, and the Memory view it opens, at both sizes and at the responsive gate's other sizes and checks they fit without horizontal overflow. The repository has no separate desktop UI tests: the web end-to-end suite is the desktop UI suite. This matches the Flutter panel ([Flutter mobile](mobile.md#implemented)).

## Language and Accessibility

The shell has no language setting of its own: it renders the web build, so it runs in English or Arabic, right to left in Arabic, exactly as the web does ([Web localization](web-localization.md)). The first start follows the operating system's language as the webview reports it in `navigator.languages`; Settings > Language saves a choice in the webview's storage, which belongs to the installed app, so it persists across restarts and updates and is separate from any browser. The web accessibility gate (axe-core in `apps/web/e2e/accessibility.spec.ts`, both languages) is the desktop's gate too. Native surfaces the shell adds itself (tray menu, notifications, the "you can close this tab" sign-in page) are English; translating them needs the shell to read the saved choice and is open.

## Sign-In

The shell signs in through Keycloak with the shared web code when the web build is made with `VITE_ANUM_OIDC_ISSUER` and `VITE_ANUM_OIDC_CLIENT_ID=anum-desktop` (authorization code + PKCE, tokens in memory, refresh token in the webview's `sessionStorage`). The login runs in the user's default browser and returns through an RFC 8252 loopback redirect, so the shell never sees the password and the user's existing browser session, password manager and security keys work:

1. Sign-in calls the shell command `oidc_loopback_listen`, which binds `127.0.0.1` on an ephemeral port (loopback interface only) and returns the port.
2. The web client (`apps/web/src/lib/auth.ts`) starts the request with redirect URI `http://127.0.0.1:<port>/callback`, a fresh state, nonce and PKCE verifier, and passes the authorization URL to `oidc_loopback_authorize`.
3. The shell opens the URL in the system browser (HTTPS only, or HTTP to a local identity provider), waits for exactly one `GET /callback?...`, answers it with a static "you can close this tab" page, focuses the ANUM window and hands the callback URL back to the web client.
4. The web client checks state, expiry and nonce and redeems the code with the same loopback redirect URI, as in the browser flow.

The listener gives up after ten minutes, and pressing Sign in again replaces a pending attempt. Other requests to the port (for example a favicon) get a 404 and do not end the wait; a forged callback from another local process fails the state check. Sign-out forgets the tokens in the shell and opens the end-session URL (with `id_token_hint`, without a post-logout redirect) in the system browser, where the Keycloak session lives. The commands are in `apps/desktop/src-tauri/src/oidc_loopback.rs`, without extra plugins; the webview calls them through `window.__TAURI_INTERNALS__` (`apps/web/src/lib/desktop.ts`), so the web build gains no Tauri dependency and a normal browser keeps the redirect flow.

The `anum-desktop` realm client already allows `http://127.0.0.1/*`, and Keycloak matches a loopback redirect URI registered without a port on any port, as RFC 8252 section 7.3 requires. The round trip has not yet been run against a live Keycloak from a packaged build; do that (Windows and macOS, including cancel, retry and sign-out) before the desktop beta channel opens. The CSP's `connect-src` must include the issuer origin for the token request; release builds add it (below). The client's redirect URIs and web origins name no server host, so moving the API or Keycloak to a new address needs a new desktop build but no change to the `anum-desktop` client ([Moving to a new address](#moving-to-a-new-address)). See [Identity and sign-in](identity.md#web-and-desktop).

## Release Builds

`pnpm --filter @anum/desktop build:release` runs `scripts/release-config.mjs --build`, which writes the gitignored overlay `src-tauri/tauri.release.conf.json` from the environment, runs `tauri build --config` with it (the Tauri CLI builds the web client first, with the sign-in variables below) and then checks the built web bundle. The committed `tauri.conf.json` holds no keys, and the overlay holds only public values.

A release build always signs in through Keycloak; it never falls back to the local development session. The production API and issuer default to the owner's deployment, an unset or empty variable takes the default, and the build stops on a plain-HTTP or local (`localhost`, `127.0.0.1`) address, an issuer that is not `https://<host>/realms/<realm>`, or a web bundle that does not contain the issuer, client id and API address. The resolved values are printed (and written to the run summary in CI). A plain `pnpm build:desktop` keeps the development behaviour: the local API and session unless `VITE_ANUM_OIDC_ISSUER` is set.

| Variable | Effect |
| --- | --- |
| `VITE_ANUM_API_URL` | API base URL, default `https://api.anum360os.com`. Trailing slashes are removed. Its HTTPS origin and `wss://` origin are added to the CSP `connect-src`. |
| `VITE_ANUM_OIDC_ISSUER` | Keycloak issuer, default `https://auth.anum360os.com/realms/anum`. Its origin is added to the CSP `connect-src`. |
| `VITE_ANUM_OIDC_CLIENT_ID` | Keycloak client, default `anum-desktop`. |
| `ANUM_TAURI_UPDATER_PUBKEY` | Updater public key (the `.pub` file from `tauri signer generate`). Turns on `createUpdaterArtifacts` and sets `plugins.updater.pubkey`. |
| `ANUM_TAURI_UPDATER_ENDPOINT` | Required with the public key: the HTTPS update manifest URL (may use `{{target}}` and `{{current_version}}`). |
| `ANUM_WINDOWS_CERTIFICATE_THUMBPRINT` | Authenticode certificate thumbprint in the build machine's certificate store; signs with SHA-256 and timestamps through `ANUM_WINDOWS_TIMESTAMP_URL` (DigiCert's by default). |
| `ANUM_DESKTOP_VERSION` | Release version (`1.2.3`), overriding `tauri.conf.json`; the release pipeline sets it from the tag. The updater compares it with the manifest's version. |

Private material never reaches the repository or the overlay: the Tauri CLI reads the updater private key from `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and macOS signing and notarization from `APPLE_SIGNING_IDENTITY`, `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID`, all as CI secrets. Missing values produce warnings, not failures, so an unsigned build still works locally. `scripts/release-config.test.mjs` runs in `pnpm check:desktop`.

## Updates

`tauri-plugin-updater` (pinned to 2.12.0, built without its `zip` feature: it reads only Tauri v2 updater artifacts, see [Security](security.md#scanning-in-ci)) is registered at startup only when the build carries `plugins.updater` with a public key and an endpoint, that is, a release build made with `ANUM_TAURI_UPDATER_PUBKEY` and `ANUM_TAURI_UPDATER_ENDPOINT` (`src-tauri/src/updater.rs`). Otherwise the plugin is not loaded, nothing contacts an update server, and the tray menu has no update item. When configured:

- The app checks once at startup and the tray menu gains "Check for updates". A found update is offered in a dialog ("Install and restart" or "Later"). The startup check stays silent when there is nothing new or the server is unreachable; the tray check reports either.
- The updater downloads the platform's artifact named in the manifest, verifies its minisign signature against the public key compiled into the build, installs it and restarts. An unsigned or wrongly signed artifact is refused.
- Checks run in the Rust shell; the webview gets no updater permission, so the capability manifest is unchanged.

`scripts/updater-manifest.mjs` writes the manifest (`latest.json`): `collect` copies a build's installers under release-unique names and turns each signed updater artifact (the NSIS and MSI installers on Windows, the `.app.tar.gz` on macOS) into a platform entry with its URL and signature, and `merge` combines the platforms. Its tests (`scripts/updater-manifest.test.mjs`) run in `pnpm check:desktop`. Updates only move forward: a client never installs a version lower than its own.

## Release Pipeline

`.github/workflows/release-clients.yml` builds the desktop installers on `windows-latest` (x86_64) and `macos-latest` (Apple silicon; Intel Macs are not built yet) with `pnpm --filter @anum/desktop build:release`. The same workflow builds the mobile apps ([Flutter mobile](mobile.md#release-pipeline)). Desktop releases are deferred by the owner, so the desktop jobs and the `publish` job are skipped (not failed) unless the repository variable `ANUM_RELEASE_DESKTOP` is `true` (then every `v*` tag builds them) or a manual run ticks the "desktop" input; a mobile release never waits on or fails for the desktop half. Each signing step runs only when its secrets exist; without them the build still runs unsigned and the job passes with a notice, so the desktop jobs never fail for lack of the owner's credentials (the mobile jobs fail when a run that uploads lacks one, [Release pipeline](mobile.md#release-pipeline)).

Repository variables (Settings, Secrets and variables, Actions, Variables):

| Variable | Value |
| --- | --- |
| `ANUM_RELEASE_DESKTOP` | `true` turns the desktop jobs on for tags. Unset while desktop releases are deferred. |
| `ANUM_PRODUCTION_API_URL` | Production API origin (HTTPS), compiled into the web bundle and the CSP: `https://api.anum360os.com/`. Shared with the mobile build. Unset, the desktop build uses `https://api.anum360os.com`. |
| `ANUM_PRODUCTION_OIDC_ISSUER` | Production Keycloak issuer (HTTPS): `https://auth.anum360os.com/realms/anum`. Shared with the mobile build. Unset, the desktop build uses that value. |
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

Each desktop job uploads its installers, their SHA-256 files and (when signed for updates) a manifest fragment as the workflow artifact `anum-desktop-<platform>`. On a tag, the `publish` job merges the fragments into `latest.json` and attaches everything to a draft GitHub release named after the tag. Every installer is built for the production API and Keycloak (the defaults above, or the two variables), so a release never carries a build that talks to local services. The CI job "Tauri desktop" (`ci.yml`) builds its unsigned Windows installer (`anum-desktop-windows`) the same way.

Cutting a release:

1. On `main`, set `version` in `apps/desktop/src-tauri/tauri.conf.json`, `apps/desktop/src-tauri/Cargo.toml` and `apps/mobile/pubspec.yaml` to the new `X.Y.Z` (the tag overrides the build; the files keep manual runs consistent).
2. Tag that commit `vX.Y.Z` and push the tag. Tags must be plain `vX.Y.Z`: MSI installers and the stores reject other shapes, and the workflow refuses them.
3. Read the run summary (checksums, and notices about anything left unsigned), install the draft release's installers on a test machine, then publish the draft. Publishing is what makes `releases/latest/download/latest.json` point at the new version, so installed clients only see it from then on.

Rolling back: the updater never downgrades, so a bad desktop release is fixed forward. Unpublish or delete the bad GitHub release at once (the `latest` URL falls back to the previous published release, which stops further installs and updates to the bad version), then tag a new patch version with the fix or from the last good commit. Users who already installed the bad version move to the patch through the normal update prompt; installing the previous installer by hand also works.

The pipeline has not run yet: it needs the variables and secrets above, and the signed and macOS paths cannot be exercised from a development container. It stays off until the owner turns desktop releases on with `ANUM_RELEASE_DESKTOP`.

## Moving to a New Address

The desktop app has the API and Keycloak addresses compiled in, so a move (as from the DuckDNS hosts to `app.anum360os.com`, `api.anum360os.com` and `auth.anum360os.com`) needs a new build; an installed copy keeps calling the old addresses. A build that showed the development sign-in, or that cannot reach the server, came from a build without these values (for example the CI artifact before this change) and has to be replaced.

1. GitHub, Settings, Secrets and variables, Actions, Variables: set `ANUM_PRODUCTION_API_URL` to `https://api.anum360os.com/` and `ANUM_PRODUCTION_OIDC_ISSUER` to `https://auth.anum360os.com/realms/anum` (both shared with the phone apps). Delete neither: an empty value also gives these defaults, but the phone apps need them.
2. Keycloak: nothing to change for the desktop. In the admin console (realm `anum`, Clients, `anum-desktop`) check that **Valid redirect URIs** contains `http://127.0.0.1/*` (the sign-in returns to `http://127.0.0.1:<port>/callback`), **Valid post logout redirect URIs** contains `tauri://localhost/*` and `http://tauri.localhost/*`, and **Web origins** contains `tauri://localhost` (macOS) and `http://tauri.localhost` (Windows); the token request is sent from those origins. Add any that is missing, then Save. The realm file already holds them.
3. Actions, "Release clients", Run workflow on `main`, tick **desktop** (leave **upload** unticked unless a phone release is also wanted). A `vX.Y.Z` tag builds them too when `ANUM_RELEASE_DESKTOP` is `true`.
4. Read the run summary: it lists `Desktop API: https://api.anum360os.com` and `Desktop Sign-in: https://auth.anum360os.com/realms/anum (client anum-desktop)` for each platform. Download the artifacts `anum-desktop-windows-x86_64` and `anum-desktop-darwin-aarch64` from the run page.
5. Install on Windows: uninstall the old ANUM (Settings, Apps), then run the `.msi` (or the setup `.exe`). Without the code-signing certificate Windows SmartScreen warns; choose More info, Run anyway. On a Mac (Apple silicon): open the `.dmg` and drag ANUM to Applications, replacing the old copy. Without Apple signing macOS blocks the first start; open it with Control-click, Open, or allow it in System Settings, Privacy and Security.
6. Start ANUM and choose Sign in: the default browser opens `auth.anum360os.com`, and after sign-in the browser tab says it can be closed and the app shows your workspace. Sessions from the old address are not carried over (the token issuer changed).

## Release Gate

A signed installer still requires a Windows code-signing certificate, Apple signing and notarization credentials and the updater key pair in the repository settings ([Release pipeline](#release-pipeline)), then a first signed release installed and updated on Windows and macOS. Local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache remain future capabilities.
