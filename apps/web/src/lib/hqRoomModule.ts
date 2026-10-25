/**
 * Which module a headquarters room opens (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md, "Room modules").
 *
 * The module follows what the room is FOR: the department stored on the room (`team_department`,
 * set by the builder and the starter campus), never the words in its name. Order of the decision:
 *  1. the stored department (a mapped department opens its module; a department without a module,
 *     such as `custom`, says so honestly),
 *  2. the room kind (meeting, workshop, warehouse, reception),
 *  3. the building kind (rooms in the executive tower open the executive desk),
 *  4. the room name, as a last resort, and the owner is told to set a department,
 *  5. the old generic kind fallback (office to Tasks, lounge to Calendar).
 * Only routes that exist in the shell are used (`lib/shell.ts` View). Pure: no effects, no DOM.
 */
import type { View } from './shell.ts';

/** What opening the module does. `flow` toggles the live flow dock inside the office; `none` has no module. */
export type RoomModuleTarget =
  | Readonly<{ type: 'view'; view: View }>
  | Readonly<{ type: 'flow' }>
  | Readonly<{ type: 'none'; why: 'no_department_module' | 'no_visitor_register' }>;

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
}>;

type Entry = Readonly<{ id: string; target: RoomModuleTarget }>;
const view = (id: string, v: View): Entry => ({ id, target: { type: 'view', view: v } });

/**
 * Department to module. The first six departments are the ones the contract can store today
 * (`OFFICE_TEAM_DEPARTMENTS`); the rest are accepted if a later contract adds them, so a stored
 * value never silently lands on a wrong module. `custom` has no module by design.
 */
export const DEPARTMENT_MODULES: Readonly<Record<string, Entry>> = {
  finance: view('finance', 'finance'),
  sales: view('sales', 'whatsapp'),
  support: view('support', 'inbox'),
  operations: { id: 'operations', target: { type: 'flow' } },
  tyre_engineering: view('tyre', 'tyre'),
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
  reception: { id: 'none', target: { type: 'none', why: 'no_visitor_register' } },
};

const GENERIC_KIND: Readonly<Record<string, Entry>> = {
  office: view('tasks', 'tasks'),
  lounge: view('calendar', 'calendar'),
};

/** Last resort: words in the room name (English and Arabic). Only used with no department and a generic kind. */
export const NAME_PATTERNS: ReadonlyArray<Readonly<{ re: RegExp; entry: Entry }>> = [
  { re: /financ|account|treasur|مال|محاسب/i, entry: DEPARTMENT_MODULES.finance },
  { re: /sales|crm|مبيع/i, entry: DEPARTMENT_MODULES.sales },
  { re: /support|customer|care|help ?desk|دعم|عملاء/i, entry: DEPARTMENT_MODULES.support },
  { re: /tyre|tire|إطار|اطار/i, entry: DEPARTMENT_MODULES.tyre_engineering },
  { re: /operation|command|dispatch|عمليات/i, entry: DEPARTMENT_MODULES.operations },
  { re: /executive|ceo|board|تنفيذ/i, entry: DEPARTMENT_MODULES.executive },
];

function normaliseDepartment(value: string | null | undefined): string | null {
  const v = (value ?? '').trim().toLowerCase();
  return v === '' ? null : v;
}

export function resolveRoomModule(input: RoomModuleInput): RoomModule {
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
  return target.type === 'view' ? `#${target.view}` : null;
}
