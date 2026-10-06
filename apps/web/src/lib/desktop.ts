// Bridge to the Tauri desktop shell (apps/desktop). The web build has no @tauri-apps/api
// dependency: Tauri v2 injects `window.__TAURI_INTERNALS__` into its webview, which is what
// `@tauri-apps/api/core` `invoke` calls. In a normal browser this returns null.

interface TauriInternals {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>;
}

export interface DesktopShell {
  /** Binds the RFC 8252 loopback listener and returns its port (apps/desktop/src-tauri/src/oidc_loopback.rs). */
  listenForSignIn(): Promise<number>;
  /** Opens the URL in the system browser and resolves with the loopback callback URL. */
  authorizeInBrowser(authorizationUrl: string): Promise<string>;
  /** Opens an identity-provider URL (for example RP-initiated logout) in the system browser. */
  openInBrowser(url: string): Promise<void>;
}

export function desktopShell(scope: object = globalThis): DesktopShell | null {
  const internals = (scope as { __TAURI_INTERNALS__?: Partial<TauriInternals> }).__TAURI_INTERNALS__;
  if (!internals || typeof internals.invoke !== 'function') return null;
  const invoke = internals.invoke.bind(internals) as TauriInternals['invoke'];
  return {
    listenForSignIn: () => invoke<number>('oidc_loopback_listen'),
    authorizeInBrowser: (authorizationUrl) => invoke<string>('oidc_loopback_authorize', { authorizationUrl }),
    openInBrowser: (url) => invoke<void>('oidc_open_browser', { url }),
  };
}
