/**
 * Which module panels a building shows (docs/ANUM_MINE_HQ_BUILDING_MODULES.md). A building shows the panels of what it is for:
 *  1. a building made by the starter campus is known by the stable id its record carries (`campus-02-commerce`): ANUM HQ and
 *     the corporate buildings show the ANUM OS panel (tasks and agents), ANUM 360 Commerce its orders and stock, Tyre Pulse
 *     its fleet and workshop numbers,
 *  2. any other building is read from the departments stored on its rooms (tyre engineering shows the tyre panel, sales and
 *     support the commerce panel),
 *  3. a building with neither keeps the two original panels, so nothing that was there before goes missing.
 * The panel numbers come from existing endpoints for the open workspace; a panel whose source is not connected says
 * "Not connected". The id never decides what is drawn from a name, and nothing here reads data. Pure: no effects, no DOM.
 */

export type ModulePanelId = 'team' | 'os' | 'commerce' | 'tyre';
export type BuildingModules = Readonly<{
  panels: readonly ModulePanelId[];
  /** Where the choice came from. */
  source: 'starter' | 'departments' | 'default';
  /** The starter campus key (`hq`, `commerce`, `tyre-pulse`...) or null. */
  key: string | null;
}>;

type BuildingLike = Readonly<{
  id: string;
  floors: ReadonlyArray<Readonly<{ rooms: ReadonlyArray<Readonly<{ teamDepartment?: string | null }>> }>>;
}>;

const STARTER_ID = /^campus-\d{2}-([a-z0-9-]{1,60})$/;
const TEAM_OS: readonly ModulePanelId[] = ['team', 'os'];

/** Panels per starter building. Keys are the stable keys of `office_starter_campus.py` (`CAMPUS`); a test keeps the two in step. */
export const STARTER_PANELS: Readonly<Record<string, readonly ModulePanelId[]>> = {
  hq: TEAM_OS,
  commerce: ['team', 'commerce'],
  'tyre-pulse': ['team', 'tyre'],
  'corporate-services': TEAM_OS,
  'knowledge-ai': TEAM_OS,
  'new-ventures': TEAM_OS,
  personal: TEAM_OS,
  career: TEAM_OS,
};

/** The starter campus key of a registry building id, or null for any other id. */
export function starterKey(id: string): string | null {
  const m = STARTER_ID.exec(id);
  return m ? m[1] : null;
}

/**
 * The building kind a room's module is decided with. Only a workspace's own building can be an "executive" building whose rooms
 * open the executive desk; a building of the office registry (the starter campus HQ is drawn as the executive tower) is not
 * classified by an owner, so its rooms are decided by their department, kind and name only.
 */
export function moduleBuildingKind(b: Readonly<{ kind: string; registryBuildingId?: string | null }> | null | undefined): string | null {
  if (!b) return null;
  return b.registryBuildingId ? null : b.kind;
}

export function buildingModules(building: BuildingLike): BuildingModules {
  const key = starterKey(building.id);
  const fixed = key ? STARTER_PANELS[key] : undefined;
  if (key && fixed) return { panels: fixed, source: 'starter', key };
  const departments = new Set<string>();
  for (const floor of building.floors) for (const room of floor.rooms) if (room.teamDepartment) departments.add(room.teamDepartment);
  const panels: ModulePanelId[] = ['team'];
  if (departments.has('sales') || departments.has('support')) panels.push('commerce');
  if (departments.has('tyre_engineering')) panels.push('tyre');
  if (panels.length > 1) return { panels, source: 'departments', key: null };
  return { panels: ['team', 'commerce', 'tyre'], source: 'default', key: null };
}
