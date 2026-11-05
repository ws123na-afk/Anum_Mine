/**
 * What the 3D board of a building says (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board"). Pure: it reads only the finished `BillboardModel`
 * (so only the fields of `BILLBOARD_FIELDS`) plus one flag, whether the presence stream is really connected. Nothing here reads a record:
 * a number is a count the model already carries, a sentence is a fixed message, an unreadable feed is "Not connected", an old one "Stale".
 */
import type { BillboardModel, BillboardPart, BillboardRow, Translate } from './billboardModel.ts';
import { contrast } from '../hq-logo-signs/signModel.ts';
import type { IconKey } from '../lib/hqIcons.ts';

/** The two lettering colours a brand header may take: the readable one (the higher WCAG contrast) is used. */
export const HEADER_LIGHT = '#ffffff';
export const HEADER_DARK = '#0b1722';
/** A brand header needs this contrast between the band and its lettering (WCAG AA for text); below it the board keeps the standard header. */
export const HEADER_MIN_CONTRAST = 4.5;

/** The header band of a board in the owner's brand colour, with the lettering colour that reads on it; null when there is no valid, readable colour. */
export function brandHeader(color: string | null): Readonly<{ fill: string; text: string; ratio: number }> | null {
  if (!color || !/^#[0-9a-f]{6}$/.test(color)) return null;
  const light = contrast(color, HEADER_LIGHT), dark = contrast(color, HEADER_DARK);
  const text = light >= dark ? HEADER_LIGHT : HEADER_DARK;
  const ratio = Math.max(light, dark);
  return ratio >= HEADER_MIN_CONTRAST ? { fill: color, text, ratio } : null;
}

export type BoardTone = 'ok' | 'stale' | 'off';

export type BoardFace = Readonly<{
  id: string;
  name: string;
  /** Arabic mode: every line is shaped and aligned right to left. */
  rtl: boolean;
  /** The fixed sentence for the building's kind; null while the owner has not set a type. */
  purpose: string | null;
  /** "5 working, 2 in meetings", "No agents" or "Not connected". */
  headline: string;
  tone: BoardTone;
  /** The other states that have agents ("Idle 1, Paused 3"), or null. */
  states: string | null;
  /** "Open tasks 3, Blocked 1", or "Open work: Not connected". */
  work: string | null;
  workConnected: boolean;
  /** "Verified 14:03:22" or "Stale: last verified 14:03:22"; empty when there is no presence to verify. */
  foot: string;
  /** The Live tick: the presence stream is connected and the numbers are fresh. */
  live: boolean;
  liveLabel: string;
  /** The building's status word for the dot colour (the same `data-status` the cards use). */
  status: string;
  /** The owner's brand colour, drawn as the accent rule only (never behind text); null: the standard teal. */
  accent: string | null;
  /** The Arabic face finished loading: a canvas does not wait for a web font, so it changes the key and the board is drawn again. */
  fonts: boolean;
  /**
   * The close view: the building is focused (selected, hovered) or the camera is near its board, and the tier draws 512 px cells. Only then
   * does the board print its detail lines (what it does, the other states, open work, departments, the verified time); far away it prints the
   * name and the key line large.
   */
  detail: boolean;
  /** The header band in the owner's brand colour and its readable lettering colour, or null for the standard header. */
  header: Readonly<{ fill: string; text: string }> | null;
  /** The key of the owner's decoded logo (`buildingId:sha`), drawn in the header; null without a decoded logo (the plain header). */
  logo: string | null;
  /** Department icons (else room kinds) of the building's registry rooms. */
  icons: readonly IconKey[];
  /** The departments line of the close view ("Sales, Marketing"), or null. */
  departments: string | null;
}>;

const row = (model: BillboardModel, field: BillboardRow['field']): BillboardRow | undefined => model.rows.find((r) => r.field === field);
const part = (parts: readonly BillboardPart[] | undefined, key: string): number => Number(parts?.find((p) => p.key === key)?.value ?? 0);

export type BoardFaceOptions = Readonly<{
  live: boolean; rtl: boolean; fontsReady?: boolean; tr: Translate;
  /** The close view (see `BoardFace.detail`). */
  detail?: boolean;
  /** True when the owner's logo of this building is decoded and ready to draw (the campus sign loader's bitmaps). */
  logoReady?: (buildingId: string, sha: string) => boolean;
}>;

export function boardFace(model: BillboardModel, opts: BoardFaceOptions): BoardFace {
  const { tr } = opts;
  // Arabic lists take the Arabic comma.
  const comma = opts.rtl ? '، ' : ', ';
  const presence = row(model, 'presence');
  const time = row(model, 'presenceTime');
  const work = row(model, 'work');
  let headline: string, tone: BoardTone, states: string | null = null;
  if (!presence || !presence.connected) { headline = tr('hqBillboard.notConnected'); tone = 'off'; }
  else if (!presence.parts) { headline = tr('hq.status.empty'); tone = time?.stale ? 'stale' : 'ok'; }
  else {
    const working = part(presence.parts, 'working'), meeting = part(presence.parts, 'meeting');
    headline = [tr('hq.building.working', { count: working }), ...(meeting > 0 ? [tr('hq.building.inMeeting', { count: meeting })] : [])].join(comma);
    tone = time?.stale ? 'stale' : 'ok';
    const rest = presence.parts.filter((p) => p.key !== 'working' && p.key !== 'meeting');
    states = [presence.value, ...rest.map((p) => `${p.label} ${p.value}`)].join(comma);
  }
  let workText: string | null = null;
  if (work && !work.connected) workText = `${work.label}: ${work.value}`;
  else if (work?.parts) {
    const shown = work.parts.filter((p) => p.key === 'open' || Number(p.value.replace('+', '')) > 0);
    workText = shown.map((p) => `${p.label} ${p.value}`).join(comma);
  }
  const kindSet = model.kindText !== null;
  const purpose = kindSet ? (row(model, 'purpose')?.value ?? null) : null;
  const departments = row(model, 'departments')?.value ?? null;
  const logo = model.logo && opts.logoReady?.(model.logo.buildingId, model.logo.sha) ? `${model.logo.buildingId}:${model.logo.sha}` : null;
  return {
    id: model.id, name: model.name, rtl: opts.rtl, purpose, headline, tone, states, work: workText, workConnected: work?.connected === true,
    foot: tone === 'off' ? '' : (time?.value ?? ''), live: opts.live && tone === 'ok', liveLabel: tr('hq.live.live'), status: model.status, accent: model.brand, fonts: opts.fontsReady === true,
    detail: opts.detail === true, header: brandHeader(model.brand), logo, icons: model.icons ?? [], departments,
  };
}

/** The text a face prints, line by line (the unit test checks every one against the model's allowed texts). Far away: the name, the key line and the Live tick. */
export function faceTexts(face: BoardFace): string[] {
  if (!face.detail) return [face.name, face.headline, ...(face.live ? [face.liveLabel] : [])];
  return [face.name, ...(face.purpose ? [face.purpose] : []), face.headline, ...(face.states ? [face.states] : []), ...(face.work ? [face.work] : []), ...(face.departments ? [face.departments] : []), ...(face.foot ? [face.foot] : []), ...(face.live ? [face.liveLabel] : [])];
}

/** Two faces that print the same thing in the same way have the same key: the painter draws a board again only when its key moved. */
export const faceKey = (face: BoardFace): string => JSON.stringify(face.detail ? face : { ...face, purpose: null, states: null, work: null, workConnected: false, foot: '', departments: null });
