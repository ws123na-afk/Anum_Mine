/**
 * Which module a headquarters room opens (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md, "Room modules").
 *
 * The module follows what the room is FOR: the department stored on the room (`team_department`,
 * set by the builder and the starter campus), never the words in its name. Order of the decision:
 *  1. the stored department (a mapped department opens its module; a department without a module,
 *     such as `custom`, says so honestly),
 *  2. the room kind (meeting, workshop, warehouse, reception, command centre),
 *  3. the building kind (rooms in the executive tower open the executive desk),
 *  4. the room name, as a last resort, and the owner is told to set a department,
 *  5. the old generic kind fallback (office to Tasks, lounge to Calendar).
 * Only routes that exist in the shell are used (`lib/shell.ts` View). Pure: no effects, no DOM.
 */
import { meetingHash } from './liveMeeting.ts';
import type { View } from './shell.ts';

/** What opening the module does. `flow` toggles the live flow dock inside the office; `reception` opens the visitor register (owner decision O4); `none` has no module. */
export type RoomModuleTarget =
  | Readonly<{ type: 'view'; view: View }>
  | Readonly<{ type: 'flow' }>
  /** The specific live meeting held in this room (`#office/meeting/<id>`), not the Meetings list. */
  | Readonly<{ type: 'meeting'; meetingId: string }>
  | Readonly<{ type: 'reception' }>
  | Readonly<{ type: 'none'; why: 'no_department_module' }>;

export type RoomModuleSource = 'department' | 'kind' | 'building' | 'name' | 'generic';

export type RoomModule = Readonly<{
  target: RoomModuleTarget;
  source: RoomModuleSource;
  /** True when no department is stored: the owner should set one in the builder. */
  needsDepartment: boolean;
  /** Message key suffix of the human name of the module (`hqRoomModule.name.<id>`). */
  id: string;
}>;

export type RoomModuleInput = Readonly<{
  /** `team_department` stored on the room. */
  department?: string | null;
  kind: string;
  name: string;
  /** Kind of the campus building the room is in (`executive` for the tower). */
  buildingKind?: string | null;
  /** The live meeting stored for this room (`room_id` on the meeting), if any. */
  liveMeetingId?: string | null;
}>;

type Entry = Readonly<{ id: string; target: RoomModuleTarget }>;
const view = (id: string, v: View): Entry => ({ id, target: { type: 'view', view: v } });

/**
 * Department to module. Every department the contract can store (`OFFICE_TEAM_DEPARTMENTS`) has an entry; a
 * test keeps the two in step. Each opens a view that exists in the shell, or says plainly that no module is
 * connected (`custom`, `procurement`): never a dead button. `executive` is storable (migration 0116), so a room whose
 * stored department is executive opens the executive desk whatever its building kind. `commerce` and `meeting` are not
 * storable departments; they stay so a later contract value or a building kind resolves to the right module.
 *
 *   marketing   -> Marketing (#marketing)
 *   technology  -> Developer mode (#developer; owners use it, others see who can)
 *   research    -> Knowledge Base collections (#knowledge)
 *   legal       -> Documents (#files)
 *   compliance  -> Governance (#governance)
 *   security    -> Audit log (#audit)
 *   hr          -> Members and roles (#members)
 *   procurement -> no module connected
 *   executive   -> Executive desk (#executive)
 */
export const DEPARTMENT_MODULES: Readonly<Record<string, Entry>> = {
  finance: view('finance', 'finance'),
  sales: view('sales', 'whatsapp'),
  support: view('support', 'inbox'),
  operations: { id: 'operations', target: { type: 'flow' } },
  tyre_engineering: view('tyre', 'tyre'),
  marketing: view('marketing', 'marketing'),
  technology: view('developer', 'developer'),
  research: view('knowledge', 'knowledge'),
  legal: view('documents', 'files'),
  security: view('audit', 'audit'),
  hr: view('people', 'members'),
  procurement: { id: 'none', target: { type: 'none', why: 'no_department_module' } },
  custom: { id: 'none', target: { type: 'none', why: 'no_department_module' } },
  executive: view('executive', 'executive'),
  commerce: view('commerce', 'commerce'),
  compliance: view('governance', 'governance'),
  meeting: view('meetings', 'meetings'),
};

/** Room kind to module, used when no department is stored. Office and lounge are generic and handled later. */
export const KIND_MODULES: Readonly<Record<string, Entry>> = {
  meeting: view('meetings', 'meetings'),
  workshop: view('tyre', 'tyre'),
  warehouse: view('commerce', 'commerce'),
  /** A reception room opens the real visitor register (`HqReceptionPanel`, docs/ANUM_MINE_HQ_RECEPTION.md). */
  reception: { id: 'reception', target: { type: 'reception' } },
  /** The operations command centre opens the live flow (the same module as the operations department). */
  command: { id: 'operations', target: { type: 'flow' } },
};

const GENERIC_KIND: Readonly<Record<string, Entry>> = {
  office: view('tasks', 'tasks'),
  lounge: view('calendar', 'calendar'),
};

/** Last resort: words in the room name (English and Arabic). Only used with no department and a generic kind. */
export const NAME_PATTERNS: ReadonlyArray<Readonly<{ re: RegExp; entry: Entry }>> = [
  { re: /financ|account|treasur|مال|محاسب/i, entry: DEPARTMENT_MODULES.finance },
  { re: /sales|crm|مبيع/i, entry: DEPARTMENT_MODULES.sales },
  { re: /support|customer|\bcare\b|help ?desk|دعم|عملاء/i, entry: DEPARTMENT_MODULES.support },
  { re: /tyre|tire|إطار|اطار/i, entry: DEPARTMENT_MODULES.tyre_engineering },
  { re: /operation|command|dispatch|عمليات/i, entry: DEPARTMENT_MODULES.operations },
  { re: /executive|ceo|board|تنفيذ/i, entry: DEPARTMENT_MODULES.executive },
];

function normaliseDepartment(value: string | null | undefined): string | null {
  const v = (value ?? '').trim().toLowerCase();
  return v === '' ? null : v;
}

export function resolveRoomModule(input: RoomModuleInput): RoomModule {
  const base = resolveBase(input);
  // A live meeting in the room: the Meetings module link opens that very meeting instead of the list.
  if (input.liveMeetingId && base.target.type === 'view' && base.target.view === 'meetings') {
    return { ...base, target: { type: 'meeting', meetingId: input.liveMeetingId } };
  }
  return base;
}

function resolveBase(input: RoomModuleInput): RoomModule {
  const department = normaliseDepartment(input.department);
  if (department !== null) {
    const entry = DEPARTMENT_MODULES[department];
    // A department this build does not know has no module either: say so, never guess from the name.
    const e = entry ?? { id: 'none', target: { type: 'none', why: 'no_department_module' } as RoomModuleTarget };
    return { target: e.target, source: 'department', needsDepartment: false, id: e.id };
  }
  const byKind = KIND_MODULES[input.kind];
  if (byKind) return { target: byKind.target, source: 'kind', needsDepartment: true, id: byKind.id };
  if (input.buildingKind === 'executive') {
    const e = DEPARTMENT_MODULES.executive;
    return { target: e.target, source: 'building', needsDepartment: true, id: e.id };
  }
  const named = NAME_PATTERNS.find((p) => p.re.test(input.name));
  if (named) return { target: named.entry.target, source: 'name', needsDepartment: true, id: named.entry.id };
  const generic = GENERIC_KIND[input.kind];
  if (generic) return { target: generic.target, source: 'generic', needsDepartment: true, id: generic.id };
  return { target: { type: 'none', why: 'no_department_module' }, source: 'generic', needsDepartment: true, id: 'none' };
}

/** The hash a `view` target links to. */
export function moduleHash(target: RoomModuleTarget): string | null {
  if (target.type === 'meeting') return meetingHash(target.meetingId);
  return target.type === 'view' ? `#${target.view}` : null;
}
