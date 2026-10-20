// Keeps the emergency-stop banner in step with the organization's switch (docs/agent-controls.md):
// another owner can turn it on or off at any time, so the state is read again on a short poll while
// the page is visible and whenever the window regains focus or becomes visible. Pure and injectable,
// so `pnpm --filter @anum/web test:unit` covers it without a browser.

export const STOP_POLL_MS = 30_000;

export interface WatchTarget {
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

export interface WatchOptions {
  intervalMs?: number;
  /** The window (focus) and document (visibilitychange) events; null where there is none. */
  windowTarget?: WatchTarget | null;
  documentTarget?: (WatchTarget & { readonly hidden?: boolean }) | null;
  setTimer?: (run: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

/**
 * Read the stop now and then on every tick and focus. `read` resolves to whether the stop is on;
 * a failure keeps the last known state. Returns a function that stops watching.
 */
export function watchAgentStop(read: () => Promise<boolean>, onChange: (engaged: boolean) => void, options: WatchOptions = {}): () => void {
  const interval = options.intervalMs ?? STOP_POLL_MS;
  const windowTarget = options.windowTarget === undefined ? (typeof window === 'undefined' ? null : window) : options.windowTarget;
  const documentTarget = options.documentTarget === undefined ? (typeof document === 'undefined' ? null : document) : options.documentTarget;
  const setTimer = options.setTimer ?? ((run: () => void, ms: number) => setInterval(run, ms));
  const clearTimer = options.clearTimer ?? ((handle: unknown) => clearInterval(handle as ReturnType<typeof setInterval>));
  let stopped = false;
  let last: boolean | null = null;
  const refresh = () => {
    if (stopped || documentTarget?.hidden) return;
    void read().then((engaged) => {
      if (stopped || engaged === last) return;
      last = engaged;
      onChange(engaged);
    }).catch(() => undefined);
  };
  const handle = setTimer(refresh, interval);
  windowTarget?.addEventListener('focus', refresh);
  documentTarget?.addEventListener('visibilitychange', refresh);
  refresh();
  return () => {
    stopped = true;
    clearTimer(handle);
    windowTarget?.removeEventListener('focus', refresh);
    documentTarget?.removeEventListener('visibilitychange', refresh);
  };
}
