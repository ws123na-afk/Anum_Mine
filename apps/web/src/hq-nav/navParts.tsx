/** Small shared pieces of the navigation layer: the thumbnail box, keyboard focus movement and icons by room kind. */
import { Briefcase, Building2, Coffee, DoorOpen, HardDrive, Map as MapIcon, Users, Wrench } from 'lucide-react';
import { useEffect, useState, type KeyboardEvent, type ReactElement } from 'react';
import { getThumb, subscribeThumbs } from './thumbs.ts';

export const ROOM_ICONS: Record<string, ReactElement> = {
  reception: <DoorOpen aria-hidden="true" />, meeting: <Users aria-hidden="true" />, office: <Briefcase aria-hidden="true" />,
  workshop: <Wrench aria-hidden="true" />, warehouse: <HardDrive aria-hidden="true" />, lounge: <Coffee aria-hidden="true" />,
};
export const CAMPUS_ICON = <MapIcon aria-hidden="true" />;
export const BUILDING_ICON = <Building2 aria-hidden="true" />;

/** Re-renders when a new thumbnail was captured. */
export function useThumb(key: string | null): string | null {
  const [, bump] = useState(0);
  useEffect(() => subscribeThumbs(() => bump((n) => n + 1)), []);
  return key ? getThumb(key) : null;
}

/** Arrow keys, Home and End move focus between the buttons of a list (every button stays reachable with Tab). */
export function arrowFocus(event: KeyboardEvent<HTMLElement>, orientation: 'vertical' | 'horizontal'): void {
  const rtl = getComputedStyle(event.currentTarget).direction === 'rtl';
  const prev = orientation === 'vertical' ? 'ArrowUp' : rtl ? 'ArrowRight' : 'ArrowLeft';
  const next = orientation === 'vertical' ? 'ArrowDown' : rtl ? 'ArrowLeft' : 'ArrowRight';
  if (![prev, next, 'Home', 'End'].includes(event.key)) return;
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-nav-item]:not(:disabled)')];
  if (!items.length) return;
  const at = items.indexOf(document.activeElement as HTMLButtonElement);
  let to = at;
  if (event.key === prev) to = Math.max(0, at - 1);
  else if (event.key === next) to = Math.min(items.length - 1, at + 1);
  else if (event.key === 'Home') to = 0;
  else to = items.length - 1;
  event.preventDefault();
  items[to]?.focus();
}
