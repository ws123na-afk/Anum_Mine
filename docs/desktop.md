# Desktop

The ANUM desktop app should use Tauri to package the web experience with controlled native capabilities. Desktop should add value through local context, shortcuts, files, notifications, and OS integration, not by forking the product.

## Role

Desktop should share the React+TypeScript UI where practical. Native code should be limited to capabilities that require OS access: filesystem pickers, secure local storage, notifications, tray controls, hotkeys, local capture, and later local tool execution.

## Security Model

Tauri permissions must be narrow. Local filesystem access should be user-selected and scoped. Desktop tools should still route through ANUM policy and audit logging. Local credentials should use platform secure storage when available.

## Runtime Relationship

The desktop app is a client, not a separate agent brain. It can provide local signals and native actions to the backend runtime through approved tool interfaces. Offline behavior should be limited until conflict handling and encrypted local storage are designed.

## Now

Define Tauri as the desktop direction and keep the web app reusable.

## Later

Add desktop notifications, global task launcher, local file context, screen-aware assistance with explicit consent, local-only tools, offline drafts, and encrypted local cache.