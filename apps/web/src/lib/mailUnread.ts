// The Email Center's inbox unread count for the sidebar (docs/email.md#email-center). The mail page
// publishes it each time it loads or refreshes its folders; the sidebar reads it. Nothing else polls
// the mail server, so the badge shows the last count the page saw in this tab.

type Listener = () => void;
let unread = 0;
const listeners = new Set<Listener>();

export function publishMailUnread(count: number): void {
  const next = Math.max(0, Math.floor(Number.isFinite(count) ? count : 0));
  if (next === unread) return;
  unread = next;
  for (const listener of listeners) listener();
}

export function mailUnread(): number { return unread; }

export function subscribeMailUnread(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * While the Email page is closed, the counts the server's background sync stored (GET /mail/unread,
 * docs/email.md#background-sync) keep the badge current: read now, then every two minutes while the
 * tab is visible (doubling to ten after failures), and stop when the Email Center is off here or the
 * server does not check in the background. Returns the stop function.
 */
export function pollMailUnread(
  read: () => Promise<{ enabled: boolean; sync_running: boolean; total: number }>,
  timers: { set: (fn: () => void, ms: number) => unknown; clear: (id: unknown) => void } = { set: (fn, ms) => setTimeout(fn, ms), clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>) },
  visible: () => boolean = () => typeof document === 'undefined' || document.visibilityState !== 'hidden',
): () => void {
  let stopped = false;
  let failures = 0;
  let timer: unknown = null;
  const schedule = () => { if (!stopped) timer = timers.set(tick, Math.min(120_000 * 2 ** failures, 600_000)); };
  const tick = () => {
    if (stopped) return;
    if (!visible()) { schedule(); return; }
    read().then((unread) => {
      if (stopped) return;
      failures = 0;
      if (!unread.enabled || !unread.sync_running) { stopped = true; return; }
      publishMailUnread(unread.total);
      schedule();
    }).catch(() => { failures = Math.min(failures + 1, 3); schedule(); });
  };
  tick();
  return () => { stopped = true; if (timer !== null) timers.clear(timer); };
}
