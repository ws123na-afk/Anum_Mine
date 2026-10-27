/**
 * What the 3D board of a building says (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board"). Pure: it reads only the finished `BillboardModel`
 * (so only the fields of `BILLBOARD_FIELDS`) plus one flag, whether the presence stream is really connected. Nothing here reads a record:
 * a number is a count the model already carries, a sentence is a fixed message, an unreadable feed is "Not connected", an old one "Stale".
 */
import type { BillboardModel, BillboardPart, BillboardRow, Translate } from './billboardModel.ts';

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
  /** The Arabic face finished loading: a canvas does not wait for a web font, so it changes the key and the board is drawn again. */
  fonts: boolean;
}>;

const row = (model: BillboardModel, field: BillboardRow['field']): BillboardRow | undefined => model.rows.find((r) => r.field === field);
const part = (parts: readonly BillboardPart[] | undefined, key: string): number => Number(parts?.find((p) => p.key === key)?.value ?? 0);

export function boardFace(model: BillboardModel, opts: Readonly<{ live: boolean; rtl: boolean; fontsReady?: boolean; tr: Translate }>): BoardFace {
  const { tr } = opts;
  const presence = row(model, 'presence');
  const time = row(model, 'presenceTime');
  const work = row(model, 'work');
  let headline: string, tone: BoardTone, states: string | null = null;
  if (!presence || !presence.connected) { headline = tr('hqBillboard.notConnected'); tone = 'off'; }
  else if (!presence.parts) { headline = tr('hq.status.empty'); tone = time?.stale ? 'stale' : 'ok'; }
  else {
    const working = part(presence.parts, 'working'), meeting = part(presence.parts, 'meeting');
    headline = [tr('hq.building.working', { count: working }), ...(meeting > 0 ? [tr('hq.building.inMeeting', { count: meeting })] : [])].join(', ');
    tone = time?.stale ? 'stale' : 'ok';
    const rest = presence.parts.filter((p) => p.key !== 'working' && p.key !== 'meeting');
    states = [presence.value, ...rest.map((p) => `${p.label} ${p.value}`)].join(', ');
  }
  let workText: string | null = null;
  if (work && !work.connected) workText = `${work.label}: ${work.value}`;
  else if (work?.parts) {
    const shown = work.parts.filter((p) => p.key === 'open' || Number(p.value.replace('+', '')) > 0);
    workText = shown.map((p) => `${p.label} ${p.value}`).join(', ');
  }
  const kindSet = model.kindText !== null;
  const purpose = kindSet ? (row(model, 'purpose')?.value ?? null) : null;
  return {
    id: model.id, name: model.name, rtl: opts.rtl, purpose, headline, tone, states, work: workText, workConnected: work?.connected === true,
    foot: tone === 'off' ? '' : (time?.value ?? ''), live: opts.live && tone === 'ok', liveLabel: tr('hq.live.live'), status: model.status, fonts: opts.fontsReady === true,
  };
}

/** The text a face prints, line by line (the unit test checks every one against the model's allowed texts). */
export function faceTexts(face: BoardFace): string[] {
  return [face.name, ...(face.purpose ? [face.purpose] : []), face.headline, ...(face.states ? [face.states] : []), ...(face.work ? [face.work] : []), ...(face.foot ? [face.foot] : []), ...(face.live ? [face.liveLabel] : [])];
}

/** Two faces that print the same thing in the same way have the same key: the painter draws a board again only when its key moved. */
export const faceKey = (face: BoardFace): string => JSON.stringify(face);
