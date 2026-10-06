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

The shell signs in through Keycloak with the shared web code when the web build is made with `VITE_ANUM_OIDC_ISSUER` and `VITE_ANUM_OIDC_CLIENT_ID=anum-desktop` (authorization code + PKCE, tokens in memory, refresh token in the webview's `sessionStorage`). The login page loads inside the webview and redirects back to the shell origin. The CSP's `connect-src` allows the local Keycloak; a release build must add its own issuer origin. Moving the login to the system browser with a loopback redirect (RFC 8252) is a follow-up. See [Identity and sign-in](identity.md#web-and-desktop).

## Release Gate

A signed installer still requires the MSVC C++ linker toolchain and a Windows code-signing identity. Local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache remain future capabilities.
