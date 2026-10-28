/**
 * What the navigation's polite live region says for a camera flight event (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md, Camera).
 * Pure, so the unit runner covers it: a flight that starts says where it goes, one that arrives says what is shown, and one
 * that is cancelled says so once and forgets the place, so no later event of another move repeats the old name.
 */
import type { MessageKey } from '../lib/i18n.ts';
import type { FlightEvent } from './cameraBridge.ts';

export type FlightPlace = Readonly<{ stopId: string; name: string }>;
export type FlightSay = (key: MessageKey, vars?: Record<string, string>) => string;

export type FlightNote = Readonly<{
  /** The text for the live region, or null to leave it as it is. */
  text: string | null;
  /** The place the next event of this flight is told against; null once the flight is over for good. */
  place: FlightPlace | null;
  /** True when the flight really arrived, so the calm view may be photographed. */
  arrived: boolean;
}>;

export function flightNote(event: FlightEvent, place: FlightPlace | null, say: FlightSay): FlightNote {
  if (event.phase === 'missing') return { text: say('hqNav.live.notShown'), place, arrived: false };
  if (event.phase === 'cancelled') {
    // Once, and only for a move that was told: a camera control with no flight under way never reaches here.
    return { text: place ? say('hqNav.live.cancelled', { place: place.name }) : null, place: null, arrived: false };
  }
  if (!place) return { text: null, place: null, arrived: false };
  if (event.phase === 'start') return { text: say('hqNav.live.flying', { place: place.name }), place, arrived: false };
  return { text: say(event.phase === 'instant' ? 'hqNav.live.instant' : 'hqNav.live.arrived', { place: place.name }), place, arrived: true };
}
