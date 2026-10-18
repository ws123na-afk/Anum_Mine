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
