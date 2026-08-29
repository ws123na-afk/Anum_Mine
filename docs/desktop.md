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

## Release Gate

A signed installer still requires the MSVC C++ linker toolchain and a Windows code-signing identity. Local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache remain future capabilities.
