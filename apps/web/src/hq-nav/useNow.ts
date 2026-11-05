import { useEffect, useState } from 'react';

/** The wall clock, refreshed while the page is visible, so a panel can age its own data ("Stale") without any new frame. */
export function useNow(everyMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') setNow(Date.now()); };
    const id = window.setInterval(tick, everyMs);
    document.addEventListener('visibilitychange', tick);
    return () => { window.clearInterval(id); document.removeEventListener('visibilitychange', tick); };
  }, [everyMs]);
  return now;
}
