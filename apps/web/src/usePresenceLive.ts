import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from './lib/api.ts';
import type { PresenceItemLike } from './lib/hqPresence.ts';
import type { StatusKey } from './lib/campusModel.ts';
import type { PresenceHub, PresenceHubView, PresenceLink } from './lib/presenceStream.ts';

// The stream code is its own chunk: it loads when the headquarters opens, and the shell never carries it.
type Browser = typeof import('./lib/presenceBrowser.ts');

export type LivePresence = Readonly<{
  /** The items to draw (staleness applied), or null until the first answer; null for good when the server has no presence API. */
  items: PresenceItemLike[] | null;
  asOf: string | null;
  link: PresenceLink;
  /** Re-read now while the page is polling (used when the shell reports an event); a live stream needs nothing. */
  refresh: () => void;
}>;

/**
 * The headquarters' presence: one shared stream for the page (see lib/presenceStream.ts). Items are aged on a 20 s
 * tick so a record that stops being confirmed turns stale without any new frame.
 */
export function usePresenceLive(enabled = true): LivePresence {
  const [view, setView] = useState<PresenceHubView | null>(null);
  const [hub, setHub] = useState<PresenceHub | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [browser, setBrowser] = useState<Browser | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let teardown: (() => void) | null = null;
    void import('./lib/presenceBrowser.ts').then((mod) => {
      if (cancelled) return;
      const scope = api.currentScope();
      const lease = mod.acquirePresenceHub(`${scope.tenantId}/${scope.workspaceId}`, mod.createBrowserPresenceHub);
      setBrowser(mod);
      setHub(lease.hub);
      setView(lease.hub.getView());
      const off = lease.hub.subscribe(setView);
      const wake = () => { if (document.visibilityState === 'visible') lease.hub.kick(); };
      document.addEventListener('visibilitychange', wake);
      window.addEventListener('online', wake);
      const tick = window.setInterval(() => { if (document.visibilityState === 'visible') setNow(Date.now()); }, 20_000);
      teardown = () => {
        off();
        document.removeEventListener('visibilitychange', wake);
        window.removeEventListener('online', wake);
        window.clearInterval(tick);
        lease.release();
      };
    });
    return () => { cancelled = true; teardown?.(); };
  }, [enabled]);
  const items = useMemo(() => {
    if (!view || !hub || !browser) return null;
    // Read the hub's own clocks: a repeated snapshot refreshes them without a new view.
    const aged = browser.presenceWithStaleness(hub.getView(), now);
    return aged ? aged.map((i) => ({ ...i, state: i.state as StatusKey })) as PresenceItemLike[] : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.version, now, hub, browser]);
  // Only a read: reconnecting is the hub's own job (backoff), never a reaction to the shell's events.
  const refresh = useCallback(() => { hub?.pollNow(); }, [hub]);
  return { items, asOf: view?.state?.asOf ?? null, link: view?.link ?? 'connecting', refresh };
}
