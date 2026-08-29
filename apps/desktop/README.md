# ANUM Desktop

The desktop app is a Tauri v2 shell around the shared `@anum/web` client. The backend remains the source of truth for agents, policy, approvals, and audit records.

## Development

Prerequisites: Node.js, pnpm, Rust, and the platform-specific Tauri dependencies.

```sh
pnpm --filter @anum/desktop dev
```

The desktop package starts the existing web Vite server and loads it at `http://localhost:5173`. Production builds compile `@anum/web` first and bundle `apps/web/dist`.

## Native boundary

The default capability grants only window controls, notifications, user-driven open/save dialogs, opening external links, and one registered global shortcut. Arbitrary filesystem and shell access are intentionally absent. Files selected through a dialog are sent to the web client as paths; backend upload and policy enforcement remain application responsibilities.
