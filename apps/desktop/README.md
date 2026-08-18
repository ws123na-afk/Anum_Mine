# ANUM Desktop

A Tauri shell around `apps/web` (see [docs/desktop.md](../../docs/desktop.md)). This is Phase 3's "Now" scope: establish Tauri as the desktop direction and keep the web app itself unforked and reusable. Native code here is intentionally small — one command (`pick_local_file`) proving the pattern described in docs/desktop.md's security model ("Local filesystem access should be user-selected and scoped... Desktop tools should still route through ANUM policy and audit logging"), not a parallel implementation of the app.

## What's here

- `src-tauri/` — the Rust shell. `src/main.rs` registers exactly one native command, `pick_local_file`, which opens a native file-picker dialog and returns the single path the user chose — it never reads file contents or grants broader filesystem access. `capabilities/default.json` grants only `core:default` and `dialog:allow-open`, nothing else.
- `tauri.conf.json` — points the webview at `apps/web`'s existing build (`frontendDist: ../../web/dist`) in production, or the Vite dev server (`devUrl: http://localhost:5173`) during `tauri dev`. `apps/web` itself has zero Tauri-specific code; it stays a normal, independently-deployable web app.

## Verified in this environment

- `cargo check` and `cargo build` both succeed for real, against real `libwebkit2gtk-4.1-dev`/`libgtk-3-dev`/`libayatana-appindicator3-dev` system libraries (installed via apt; see the repo's setup notes if these aren't present elsewhere).
- The compiled binary was launched under Xvfb (`xvfb-run ./target/debug/anum-desktop`) and stayed running, loading the built web app into a real GTK/WebKit window, without crashing.
- **Not verified here**: an actual bundled installer/package (`tauri build`'s `.deb`/`.AppImage`/etc. targets) and macOS/Windows builds — this sandbox is Linux-only and this pass focused on proving the Rust shell itself compiles and runs correctly.

## Local development

```bash
# from the repo root
pnpm install
pnpm --filter @anum/desktop dev    # runs `tauri dev`: starts the web app's Vite dev server and opens it in a native window
pnpm --filter @anum/desktop build  # runs `tauri build`: builds apps/web, then bundles a native installer for your platform
```

Rust/Cargo and platform-specific system dependencies (see [Tauri's prerequisites docs](https://v2.tauri.app/start/prerequisites/) for your OS) are required locally; `pnpm install` alone does not install them.

## Frontend integration

`pick_local_file` isn't called from `apps/web` yet — there's no file-attach UI in the web app to call it from (the backend's `POST /api/v1/files` endpoint exists as of this same work, but the frontend view for it doesn't). When that UI is built, it should feature-detect Tauri (e.g. `'__TAURI__' in window`) and, only when running inside this shell, offer the native picker via `@tauri-apps/api`'s `invoke('pick_local_file')` as an alternative to the browser's own `<input type="file">` — never as a replacement for it, since the web app must keep working standalone in a browser.
