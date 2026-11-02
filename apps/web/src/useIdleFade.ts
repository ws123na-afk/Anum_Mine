/**
 * Idle state of a scene for its camera group (docs/ANUM_MINE_HQ_LABELS.md, "Declutter"): the group fades after `ms` without a pointer
 * move, a press, a wheel turn, a key or a focus inside the scene, and comes back at once on any of them. Hover and keyboard focus on the
 * group itself keep it whole through CSS (`.hqCamGroup:is(:hover, :focus-within)`), so it is never faded while being used.
 */
import { useEffect, useState, type RefObject } from 'react';

export const IDLE_MS = 3000;

export function useIdleFade(scope: RefObject<HTMLElement | null>, ms = IDLE_MS): boolean {
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    const el = scope.current;
    if (!el) return undefined;
    let timer = window.setTimeout(() => setIdle(true), ms);
    const wake = () => {
      window.clearTimeout(timer);
      setIdle(false);
      timer = window.setTimeout(() => setIdle(true), ms);
    };
    const events = ['pointermove', 'pointerdown', 'wheel', 'keydown', 'focusin', 'touchstart'] as const;
    for (const name of events) el.addEventListener(name, wake, { passive: true });
    return () => { window.clearTimeout(timer); for (const name of events) el.removeEventListener(name, wake); };
  }, [scope, ms]);
  return idle;
}
