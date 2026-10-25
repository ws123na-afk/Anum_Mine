/** Which navigation panels sit beside the scene at this width, and the person's own collapse choices (kept per browser). */
import { useCallback, useEffect, useState } from 'react';

export type RailMode = 'full' | 'slim' | 'drawer';
export type SideMode = 'open' | 'hidden' | 'drawer';
export type NavLayout = Readonly<{ rail: RailMode; side: SideMode; compact: boolean; width: number }>;

const RAIL_KEY = 'anum.hq.nav.rail';
const SIDE_KEY = 'anum.hq.nav.side';
const STRIP_KEY = 'anum.hq.nav.strip';

export type Pref = 'open' | 'closed' | null;
const read = (key: string): Pref => { try { const v = window.localStorage.getItem(key); return v === 'open' || v === 'closed' ? v : null; } catch { return null; } };
const write = (key: string, value: Pref) => { try { if (value) window.localStorage.setItem(key, value); else window.localStorage.removeItem(key); } catch { /* remembered for this page only */ } };

/** Pure: the layout for a viewport width and the person's choices. */
export function layoutFor(width: number, railPref: Pref, sidePref: Pref): NavLayout {
  let rail: RailMode = width >= 1440 ? 'full' : width >= 900 ? 'slim' : 'drawer';
  if (rail !== 'drawer') { if (railPref === 'closed') rail = 'slim'; else if (railPref === 'open') rail = 'full'; }
  const room = width >= 1280;
  const side: SideMode = !room ? 'drawer' : sidePref === 'closed' ? 'hidden' : 'open';
  return { rail, side, compact: width < 760, width };
}

export function useNavLayout() {
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth));
  const [railPref, setRailPref] = useState<Pref>(() => read(RAIL_KEY));
  const [sidePref, setSidePref] = useState<Pref>(() => read(SIDE_KEY));
  const [stripPref, setStripPref] = useState<Pref>(() => read(STRIP_KEY));
  useEffect(() => {
    let raf = 0;
    const onResize = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setWidth(window.innerWidth)); };
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); cancelAnimationFrame(raf); };
  }, []);
  const layout = layoutFor(width, railPref, sidePref);
  const toggleRail = useCallback(() => { const next: Pref = layout.rail === 'full' ? 'closed' : 'open'; setRailPref(next); write(RAIL_KEY, next); }, [layout.rail]);
  const toggleSide = useCallback(() => { const next: Pref = layout.side === 'open' ? 'closed' : 'open'; setSidePref(next); write(SIDE_KEY, next); }, [layout.side]);
  const toggleStrip = useCallback(() => { const next: Pref = stripPref === 'closed' ? 'open' : 'closed'; setStripPref(next); write(STRIP_KEY, next); }, [stripPref]);
  return { layout, stripOpen: stripPref !== 'closed', toggleRail, toggleSide, toggleStrip };
}
