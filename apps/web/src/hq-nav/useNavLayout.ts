/** Which navigation panels sit beside the scene at this width, and the person's own collapse choices (kept per browser). */
import { useCallback, useEffect, useState } from 'react';

export type RailMode = 'full' | 'slim' | 'drawer' | 'hidden';
export type SideMode = 'open' | 'hidden' | 'drawer';
export type NavLayout = Readonly<{ rail: RailMode; side: SideMode; compact: boolean; width: number }>;

const RAIL_KEY = 'anum.hq.nav.rail';
const SIDE_KEY = 'anum.hq.nav.side';
const STRIP_KEY = 'anum.hq.nav.strip';
const FOCUS_KEY = 'anum.hq.nav.focus';

export type Pref = 'open' | 'closed' | null;
const read = (key: string): Pref => { try { const v = window.localStorage.getItem(key); return v === 'open' || v === 'closed' ? v : null; } catch { return null; } };
const writeRail = (value: RailPref) => { try { if (value) window.localStorage.setItem(RAIL_KEY, value); else window.localStorage.removeItem(RAIL_KEY); } catch { /* remembered for this page only */ } };
const write = (key: string, value: Pref) => { try { if (value) window.localStorage.setItem(key, value); else window.localStorage.removeItem(key); } catch { /* remembered for this page only */ } };

/** The floor list beside the scene: not shown (null or closed, it opens as a drawer from the Floors button), docked whole, or docked slim. */
export type RailPref = 'open' | 'slim' | 'closed' | null;
const readRail = (): RailPref => { try { const v = window.localStorage.getItem(RAIL_KEY); return v === 'open' || v === 'slim' || v === 'closed' ? v : null; } catch { return null; } };

/** From this width the side column is docked beside the scene by default; narrower, it is a drawer so the scene keeps the width. */
export const SIDE_DOCK_MIN = 1600;

/**
 * Pure: the layout for a viewport width and the person's choices (docs/ANUM_MINE_HQ_PAGE_LAYOUT.md). The in-scene floor selector is the
 * one floor control, so the floor list with thumbnails is docked only when the person asks for it (the Floors button), from 900 px.
 */
export function layoutFor(width: number, railPref: RailPref, sidePref: Pref, focus = false): NavLayout {
  let rail: RailMode = width < 900 || railPref === null || railPref === 'closed' ? 'drawer' : railPref === 'slim' ? 'slim' : 'full';
  // Focus mode gives the whole width to the scene: no rail and no side column (drawers stay reachable from the toolbar).
  if (focus && rail !== 'drawer') rail = 'hidden';
  const side: SideMode = width < 1280 ? 'drawer' : focus || sidePref === 'closed' ? 'hidden' : sidePref === 'open' || width >= SIDE_DOCK_MIN ? 'open' : 'drawer';
  return { rail, side, compact: width < 760, width };
}

export function useNavLayout() {
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth));
  const [railPref, setRailPref] = useState<RailPref>(() => readRail());
  const [sidePref, setSidePref] = useState<Pref>(() => read(SIDE_KEY));
  const [stripPref, setStripPref] = useState<Pref>(() => read(STRIP_KEY));
  const [focus, setFocus] = useState<boolean>(() => read(FOCUS_KEY) === 'closed');
  useEffect(() => {
    let raf = 0;
    const onResize = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setWidth(window.innerWidth)); };
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); cancelAnimationFrame(raf); };
  }, []);
  const layout = layoutFor(width, railPref, sidePref, focus);
  // The docked rail's own collapse button: whole and slim.
  const toggleRail = useCallback(() => { const next: RailPref = layout.rail === 'full' ? 'slim' : 'open'; setRailPref(next); writeRail(next); }, [layout.rail]);
  // The Floors button: dock the floor list beside the scene, or put it away again (it then opens as a drawer).
  const toggleRailDock = useCallback(() => { const next: RailPref = layout.rail === 'full' || layout.rail === 'slim' ? 'closed' : 'open'; setRailPref(next); writeRail(next); }, [layout.rail]);
  const toggleSide = useCallback(() => { const next: Pref = layout.side === 'open' ? 'closed' : 'open'; setSidePref(next); write(SIDE_KEY, next); }, [layout.side]);
  const toggleStrip = useCallback(() => { const next: Pref = stripPref === 'closed' ? 'open' : 'closed'; setStripPref(next); write(STRIP_KEY, next); }, [stripPref]);
  const toggleFocus = useCallback(() => { const next = !focus; setFocus(next); write(FOCUS_KEY, next ? 'closed' : 'open'); }, [focus]);
  return { layout, focus, stripOpen: stripPref !== 'closed', toggleRail, toggleRailDock, toggleSide, toggleStrip, toggleFocus };
}
