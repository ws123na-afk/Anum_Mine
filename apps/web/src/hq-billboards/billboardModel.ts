/**
 * What a building billboard says (docs/ANUM_MINE_HQ_BILLBOARDS.md). Pure functions, no DOM.
 *
 * Data truth: every string comes from exactly two places, a fixed message of the catalogue (a row heading, a per-kind sentence, "Not
 * connected") or a real record field named in `BILLBOARD_FIELDS`. Nothing is estimated, no person, task title, customer, meeting or
 * prompt text is ever read, and a value that is unavailable says "Not connected", never a zero that implies data. `test/hqBillboard.test.ts`
 * reads the model through a recording proxy and fails when it touches a property outside `BILLBOARD_READS`.
 */
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { CampusBuilding, CampusBuildingKind, CampusTotals, StatusKey } from '../lib/campusModel.ts';
import { STATUS_ORDER, buildingStatus, workspaceOf } from '../lib/campusModel.ts';
import type { PlanInput } from '../lib/hqPlanFrame.ts';
import { departmentText } from '../lib/hqLabelModel.ts';
import { roomIconKey, type IconKey } from '../lib/hqIcons.ts';
import { sceneName, roomName } from '../lib/registryNames.ts';
import { getLocale, t, type MessageKey, type Locale, type Vars } from '../lib/i18n.ts';

export type Translate = (key: MessageKey, vars?: Vars) => string;

/**
 * The fixed sentence and the kind name of every building kind. A typed map, not a key built from the kind, so a new kind without its
 * messages is a build error instead of a raw key on the card (a Career building once printed "hqBillboard.purpose.career").
 */
export const PURPOSE_KEYS: Readonly<Record<CampusBuildingKind, MessageKey>> = {
  executive: 'hqBillboard.purpose.executive', business: 'hqBillboard.purpose.business', department: 'hqBillboard.purpose.department',
  personal: 'hqBillboard.purpose.personal', career: 'hqBillboard.purpose.career',
};
export const KIND_KEYS: Readonly<Record<CampusBuildingKind, MessageKey>> = {
  executive: 'hq.kind.executive', business: 'hq.kind.business', department: 'hq.kind.department', personal: 'hq.kind.personal', career: 'hq.kind.career',
};

/** A value older than this is called stale (the same 90 seconds the registry projection gives presence evidence). */
export const BILLBOARD_STALE_MS = 90_000;
/** Room names listed on a billboard before "+N more". */
export const ROOM_NAME_LIMIT = 5;
/** Departments listed on a billboard before "+N more". */
export const DEPARTMENT_LIMIT = 4;
/** Icons drawn on a 3D board at most. */
export const ICON_LIMIT = 6;

/** Every field a billboard can show, with the record it comes from. The docs table is this list (a test keeps them equal). */
export const BILLBOARD_FIELDS = [
  { id: 'name', source: 'CampusBuilding.name and nameAr (office registry building name, name_ar)' },
  { id: 'kind', source: 'CampusBuilding.kind, only when kindKnown is not false (the owner\'s building type)' },
  { id: 'purpose', source: 'a fixed sentence per building kind (hqBillboard.purpose.*), never a claim about activity' },
  { id: 'floors', source: 'CampusBuilding.floors (office registry floors of the building)' },
  { id: 'roomCount', source: 'CampusBuilding.rooms, else the rooms of the building\'s floor plan' },
  { id: 'departments', source: 'room.teamDepartment of the building\'s registry rooms (only departments the catalogue names)' },
  { id: 'roomNames', source: 'room.label and labelAr of the building\'s registry rooms' },
  { id: 'presence', source: 'CampusBuilding.totals (campus endpoint or the presence index), agents by state' },
  { id: 'presenceTime', source: 'CampusBuilding.presence_verified_at' },
  { id: 'work', source: 'OfficeFlowSnapshot task items located in the building (location.building_id, stage, waiting), tasks source connected' },
  { id: 'workTime', source: 'OfficeFlowSnapshot.as_of, and the flow read status' },
  { id: 'brand', source: 'CampusBuilding.sign.color (the owner\'s brand colour of the building\'s sign), the header band and accent rule of the 3D board when it passes the contrast check' },
  { id: 'logo', source: 'CampusBuilding.sign.logoSha256 and sign.buildingId: the owner\'s uploaded logo, fetched from the authenticated logo route and drawn in the header of the 3D board' },
  { id: 'icons', source: 'room.teamDepartment of the building\'s registry rooms, else room.kind: drawn as icons of the one HQ icon set (lib/hqIcons.ts)' },
  { id: 'streamLink', source: 'the presence stream link (usePresenceLive link "live"), drawn only as the Live tick on the 3D board' },
  { id: 'open', source: 'fixed button text (the accessible name adds CampusBuilding.name)' },
] as const;
export type BillboardFieldId = (typeof BILLBOARD_FIELDS)[number]['id'];

/** The properties the model may read, by path (`[]` is an array element). Anything else read from an input is a data truth failure. */
export const BILLBOARD_READS: readonly string[] = [
  'building.name', 'building.nameAr', 'building.kind', 'building.kindKnown', 'building.floors', 'building.rooms', 'building.accessible',
  'building.totals', 'building.presence_verified_at', 'building.sign', 'building.sign.color', 'building.sign.logoSha256', 'building.sign.buildingId', 'building.registryBuildingId', 'building.workspace_id', 'building.realWorkspaceId',
  'building.totals.agents', 'building.totals.working', 'building.totals.queued', 'building.totals.meeting', 'building.totals.waiting_approval',
  'building.totals.blocked', 'building.totals.idle', 'building.totals.paused', 'building.totals.unknown', 'building.totals.off_shift',
  'plan.floors', 'plan.floors[]', 'plan.floors[].rooms', 'plan.floors[].rooms[]', 'plan.floors[].rooms[].label', 'plan.floors[].rooms[].labelAr',
  'plan.floors[].rooms[].teamDepartment', 'plan.floors[].rooms[].kind',
  'flow.status', 'flow.snapshot', 'flow.snapshot.sources', 'flow.snapshot.sources.tasks', 'flow.snapshot.items', 'flow.snapshot.items[]',
  'flow.snapshot.items[].kind', 'flow.snapshot.items[].stage', 'flow.snapshot.items[].waiting', 'flow.snapshot.items[].location',
  'flow.snapshot.items[].location.building_id', 'flow.snapshot.truncated', 'flow.snapshot.as_of',
];

export type BillboardFlow = Readonly<{ status: 'loading' | 'ready' | 'error' | 'denied'; snapshot: OfficeFlowSnapshot | null }>;

export type BillboardInput = Readonly<{
  building: CampusBuilding;
  /** The real floor plans of the registry building (rooms, departments), when the building has them. */
  plan?: Pick<PlanInput, 'floors'> | null;
  /** The live flow read of the open workspace. */
  flow?: BillboardFlow | null;
  /** The workspace that is open: the flow belongs to it only. */
  currentWorkspaceId: string;
  /** Milliseconds since the epoch, for the stale rule. */
  now: number;
  locale?: Locale;
  tr?: Translate;
}>;

export type BillboardPart = Readonly<{ key: string; label: string; value: string }>;
export type BillboardRow = Readonly<{
  field: BillboardFieldId;
  /** Row heading (a fixed message), or null for a row that has none. */
  label: string | null;
  /** The value text: a record field, a fixed sentence, or "Not connected". */
  value: string;
  /** False when the value is the "Not connected" (or "No access") text. */
  connected: boolean;
  stale?: boolean;
  parts?: readonly BillboardPart[];
}>;

export type BillboardModel = Readonly<{
  id: string;
  name: string;
  /** The building type in words, or null while the owner has not set one. */
  kindText: string | null;
  kind: CampusBuilding['kind'];
  /** The one line shown at the compact density. */
  summary: string;
  status: ReturnType<typeof buildingStatus>;
  accessible: boolean;
  /** The owner's brand colour (#rrggbb) of the building's sign, or null. */
  brand: string | null;
  /** The owner's uploaded logo of the building's sign (its building id and SHA-256), or null. Only the board draws it, once decoded. */
  logo: Readonly<{ buildingId: string; sha: string }> | null;
  /** One icon per department of the building's registry rooms (a room without a known department gives its kind), at most `ICON_LIMIT`. */
  icons: readonly IconKey[];
  rows: readonly BillboardRow[];
  openLabel: string;
  unavailableText: string;
}>;

/** True when `value` is a usable ISO time. */
const isTime = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));

const timeText = (iso: string, locale: Locale): string => new Date(iso).toLocaleTimeString(locale === 'ar' ? 'ar-u-nu-latn' : undefined);

/** Presence counts as `parts` (working always, any other state only when it has agents). */
export function presenceParts(totals: CampusTotals, tr: Translate): BillboardPart[] {
  const label = (key: StatusKey): string => tr(`hq.status.${key}` as MessageKey);
  return STATUS_ORDER.filter((key) => key === 'working' || (totals[key] ?? 0) > 0).map((key) => ({ key, label: label(key), value: String(totals[key] ?? 0) }));
}

type Work = Readonly<{ state: 'ok'; open: number; waiting: number; blocked: number; atLeast: boolean; asOf: string | null; stale: boolean }> | Readonly<{ state: 'not_connected' | 'no_access' }>;

/**
 * Open work of one building from the live flow read: task items that a record placed in one of its rooms and that are not done.
 * Only the open workspace has a flow, so any other building is "not connected"; so is a flow that did not answer or whose tasks source is down.
 */
export function workOf(building: CampusBuilding, flow: BillboardFlow | null | undefined, currentWorkspaceId: string): Work {
  if (workspaceOf(building) !== currentWorkspaceId) return { state: 'not_connected' };
  if (!flow) return { state: 'not_connected' };
  if (flow.status === 'denied') return { state: 'no_access' };
  const snap = flow.snapshot;
  if (!snap) return { state: 'not_connected' };
  if (snap.sources.tasks === 'no_access') return { state: 'no_access' };
  if (snap.sources.tasks !== 'connected') return { state: 'not_connected' };
  const mine = building.registryBuildingId;
  let open = 0, waiting = 0, blocked = 0;
  for (const item of snap.items) {
    if (item.kind !== 'task' || item.stage === 'done' || !item.location) continue;
    if (mine !== undefined && item.location.building_id !== mine) continue;
    open += 1;
    if (item.waiting) waiting += 1;
    if (item.stage === 'blocked') blocked += 1;
  }
  // A failed read that kept the last answer is shown, but called stale (the flow dock does the same).
  return { state: 'ok', open, waiting, blocked, atLeast: snap.truncated === true, asOf: isTime(snap.as_of) ? snap.as_of : null, stale: flow.status === 'error' };
}

type RoomFacts = { label: string; labelAr: string | null; department: string | null; icon: IconKey };
function roomsOf(plan: BillboardInput['plan']): RoomFacts[] {
  const out: RoomFacts[] = [];
  for (const floor of plan?.floors ?? []) {
    for (const room of floor.rooms) {
      const department = room.teamDepartment ?? null;
      out.push({ label: room.label, labelAr: room.labelAr ?? null, department, icon: roomIconKey({ kind: room.kind, teamDepartment: department }) });
    }
  }
  return out;
}

const unique = (list: readonly string[]): string[] => [...new Set(list)];

/** The model of one billboard. */
export function billboardModel(input: BillboardInput): BillboardModel {
  const locale = input.locale ?? getLocale();
  const tr: Translate = input.tr ?? ((key, vars) => t(key, vars));
  const b = input.building;
  const name = sceneName(b, locale);
  const kindText = b.kindKnown === false ? null : tr(KIND_KEYS[b.kind] ?? 'hq.kind.unset');
  const rows: BillboardRow[] = [];
  const notConnected = (field: BillboardFieldId, label: string | null, noAccess = false): BillboardRow => (
    { field, label, value: tr(noAccess ? 'hqBillboard.noAccess' : 'hqBillboard.notConnected'), connected: false }
  );

  // What it does: a fixed sentence for the building's kind (never a claim about activity).
  rows.push({ field: 'purpose', label: tr('hqBillboard.label.about'), value: tr(b.kindKnown === false ? 'hqBillboard.purpose.unset' : (PURPOSE_KEYS[b.kind] ?? 'hqBillboard.purpose.unset')), connected: true });

  // Size: floors and rooms of the registry.
  const rooms = roomsOf(input.plan);
  const roomCount = typeof b.rooms === 'number' ? b.rooms : input.plan ? rooms.length : null;
  const size: string[] = [];
  if (typeof b.floors === 'number') size.push(tr('hq.building.floors', { count: b.floors }));
  if (roomCount !== null) size.push(tr('hqNav.card.rooms', { count: roomCount }));
  rows.push(size.length ? { field: typeof b.floors === 'number' ? 'floors' : 'roomCount', label: tr('hqBillboard.label.size'), value: size.join(' · '), connected: true } : notConnected('floors', tr('hqBillboard.label.size')));

  // Departments and room names come from the registry rooms of this building only.
  if (input.plan) {
    const departments = unique(rooms.map((r) => departmentText(r.department, tr)).filter((d): d is string => d !== null));
    if (departments.length) {
      const shown = departments.slice(0, DEPARTMENT_LIMIT);
      const more = departments.length - shown.length;
      rows.push({ field: 'departments', label: tr('hqBillboard.label.departments'), value: [...shown, ...(more > 0 ? [tr('hqBillboard.more', { count: more })] : [])].join(', '), connected: true });
    }
    const names = unique(rooms.map((r) => roomName({ label: r.label, labelAr: r.labelAr }, locale)));
    if (names.length) {
      const shown = names.slice(0, ROOM_NAME_LIMIT);
      const more = names.length - shown.length;
      rows.push({ field: 'roomNames', label: tr('hqBillboard.label.rooms'), value: [...shown, ...(more > 0 ? [tr('hqBillboard.more', { count: more })] : [])].join(', '), connected: true });
    } else {
      rows.push({ field: 'roomNames', label: tr('hqBillboard.label.rooms'), value: tr('hqBillboard.noRooms'), connected: true });
    }
  } else {
    rows.push(notConnected('roomNames', tr('hqBillboard.label.rooms')));
  }

  // Presence: the counts the campus panel shows, with the same verified time and a stale mark.
  const totals = b.accessible ? b.totals : null;
  let summary: string;
  if (!totals) {
    summary = tr('hqBillboard.notConnected');
    rows.push(notConnected('presence', tr('hqBillboard.label.presence')));
  } else if (totals.agents === 0) {
    summary = tr('hq.status.empty');
    rows.push({ field: 'presence', label: tr('hqBillboard.label.presence'), value: tr('hq.status.empty'), connected: true });
  } else {
    summary = [tr('hq.building.agents', { count: totals.agents }), tr('hq.status.working') + ' ' + totals.working].join(', ');
    rows.push({ field: 'presence', label: tr('hqBillboard.label.presence'), value: tr('hq.building.agents', { count: totals.agents }), connected: true, parts: presenceParts(totals, tr) });
  }
  if (totals) {
    const at = b.presence_verified_at;
    if (!isTime(at)) rows.push({ field: 'presenceTime', label: null, value: tr('hqBillboard.noTime'), connected: true, stale: true });
    else {
      const stale = input.now - Date.parse(at) > BILLBOARD_STALE_MS;
      rows.push({ field: 'presenceTime', label: null, value: tr(stale ? 'hqBillboard.stale' : 'hqBillboard.verified', { time: timeText(at, locale) }), connected: true, stale });
    }
  }

  // Open work from the live flow.
  const work = workOf(b, input.flow, input.currentWorkspaceId);
  if (work.state !== 'ok') rows.push(notConnected('work', tr('hqBillboard.label.work'), work.state === 'no_access'));
  else {
    const n = (v: number) => (work.atLeast ? `${v}+` : String(v));
    rows.push({
      field: 'work', label: tr('hqBillboard.label.work'), value: n(work.open), connected: true, stale: work.stale,
      parts: [
        { key: 'open', label: tr('hqBillboard.work.open'), value: n(work.open) },
        { key: 'waiting', label: tr('hqBillboard.work.waiting'), value: n(work.waiting) },
        { key: 'blocked', label: tr('hqBillboard.work.blocked'), value: n(work.blocked) },
      ],
    });
    if (work.asOf) rows.push({ field: 'workTime', label: null, value: tr(work.stale ? 'hqBillboard.stale' : 'hqBillboard.verified', { time: timeText(work.asOf, locale) }), connected: true, stale: work.stale });
  }

  const sign = b.sign;
  const brand = /^#[0-9a-f]{6}$/i.test((sign?.color ?? '').trim()) ? sign!.color!.trim().toLowerCase() : null;
  const sha = sign?.logoSha256 ?? null;
  const logo = sign && typeof sha === 'string' && /^[0-9a-f]{64}$/.test(sha) && typeof sign.buildingId === 'string' && sign.buildingId ? { buildingId: sign.buildingId, sha } : null;
  // Department icons first (in the order the rooms come), then the kinds of rooms that have no known department.
  const keys = unique(rooms.map((r) => r.icon));
  const icons = [...keys.filter((k) => k.startsWith('dept:')), ...keys.filter((k) => k.startsWith('kind:'))].slice(0, ICON_LIMIT) as IconKey[];
  return {
    id: b.workspace_id, name, kindText, kind: b.kind, summary, status: buildingStatus(b), accessible: b.accessible, brand, logo, icons, rows,
    openLabel: tr('hqBillboard.open'), unavailableText: tr('hqBillboard.unavailable'),
  };
}

/** Every string a billboard prints, with the field it comes from (the test and the docs read this). */
export function billboardTexts(model: BillboardModel): Array<{ field: BillboardFieldId | 'name' | 'kind' | 'open'; text: string }> {
  const out: Array<{ field: BillboardFieldId; text: string }> = [{ field: 'name', text: model.name }];
  if (model.kindText) out.push({ field: 'kind', text: model.kindText });
  for (const row of model.rows) {
    if (row.label) out.push({ field: row.field, text: row.label });
    out.push({ field: row.field, text: row.value });
    for (const part of row.parts ?? []) out.push({ field: row.field, text: `${part.label} ${part.value}` });
  }
  out.push({ field: 'open', text: model.openLabel });
  return out;
}
