/**
 * Registry buildings on the campus. A workspace's office registry can hold several buildings (the starter campus creates
 * eight). The campus draws each as its own building, with its own card, floors and people; a workspace with one
 * registry building (or none) stays the single building it always was. Other workspaces are never expanded: their
 * registry is not read across workspaces. Pure: the unit runner covers it.
 *
 * Data truth: a building appears only for a real registry building record; its people are the agents assigned to its
 * desks (and private-agent desks), with the state of their presence record; with no presence the card says
 * "Presence unavailable" and shows no count.
 */
import { departmentsOf, initialsOf } from '../hq-nav/navModel.ts';
import { EMPTY_TOTALS, campusTotals, type Campus, type CampusBuilding, type CampusBuildingKind, type CampusTotals, type StatusKey } from './campusModel.ts';
import { starterKey } from './hqBuildingModules.ts';
import type { SceneBuilding } from './headquartersProjection.ts';
import { presenceForDesk, type HqPresenceIndex } from './hqPresence.ts';

/**
 * How a starter campus building is drawn. The starter campus gives every building a stable id (`campus-07-personal`), so the
 * look follows that id and never the words of a name: ANUM HQ stands as the executive tower, the personal office as the
 * villa, Tyre Pulse as the saw-tooth workshop, ANUM 360 Commerce as the glazed shop pavilion. Any other registry building
 * keeps the plain business look. `icon` is a local drawing hint (see `campusBuildings.ts`), never stored.
 */
const STARTER_LOOK: Readonly<Record<string, Readonly<{ kind: CampusBuildingKind; icon: string | null }>>> = {
  hq: { kind: 'executive', icon: null },
  personal: { kind: 'personal', icon: null },
  'tyre-pulse': { kind: 'department', icon: 'workshop' },
  commerce: { kind: 'business', icon: 'store' },
};
export function registryLook(buildingId: string): Readonly<{ kind: CampusBuildingKind; icon: string | null }> {
  const key = starterKey(buildingId);
  return (key ? STARTER_LOOK[key] : undefined) ?? { kind: 'business', icon: null };
}

export const registryKey = (workspaceId: string, buildingId: string): string => `${workspaceId}~${buildingId}`;

/** Totals and people of one registry building from its desks and the presence index. Null totals when presence is unavailable. */
export function registryBuildingFacts(building: SceneBuilding, presence: HqPresenceIndex | null): { totals: CampusTotals | null; people: string[]; departments: string[] } {
  const seen = new Set<string>();
  const statuses: StatusKey[] = [];
  const names: string[] = [];
  const roles: Array<string | null> = [];
  for (const floor of building.floors) {
    for (const desk of floor.desks) {
      const record = presenceForDesk(presence, desk);
      if (!desk.agentId && !record) continue;
      const id = desk.agentId ?? record?.agentId ?? desk.id;
      if (seen.has(id)) continue;
      seen.add(id);
      statuses.push(record ? record.status : 'unknown');
      const hidden = record?.reason === 'agent_not_visible' || (record?.agentId.startsWith('desk:') ?? false);
      const name = hidden ? '' : record?.name || desk.agent?.name || '';
      if (name) { names.push(name); roles.push(record?.role ?? null); }
    }
  }
  const totals: CampusTotals | null = presence
    ? statuses.reduce<CampusTotals>((t, s) => ({ ...t, agents: t.agents + 1, [s]: t[s] + 1 }), EMPTY_TOTALS)
    : null;
  return { totals, people: names.slice(0, 4).map(initialsOf), departments: departmentsOf(roles) };
}

/**
 * Replaces the open workspace's single building by one building per registry building when there are two or more.
 * `workspaceName` labels each card with its workspace. Everything else is returned untouched.
 */
export function expandCampus(campus: Campus, workspaceId: string, registry: readonly SceneBuilding[], presence: HqPresenceIndex | null): Campus {
  const home = campus.buildings.find((b) => b.workspace_id === workspaceId);
  if (!home || !home.accessible || home.archived || registry.length < 2) return campus;
  const expanded: CampusBuilding[] = registry.map((b) => {
    const facts = registryBuildingFacts(b, presence);
    const rooms = b.floors.reduce((n, f) => n + f.rooms.length, 0);
    const desks = b.floors.reduce((n, f) => n + f.desks.length, 0);
    return {
      ...home, workspace_id: registryKey(workspaceId, b.id), realWorkspaceId: workspaceId, registryBuildingId: b.id, workspaceName: home.name, name: b.name,
      // The registry holds no building type, so none is ever labelled; the look follows the starter campus id (`registryLook`).
      ...registryLook(b.id), kindKnown: false, role: home.role, profileRevision: null,
      floors: b.floors.length || null, rooms, desks, totals: facts.totals, people: facts.people, departments: facts.departments,
    };
  });
  const buildings = [...campus.buildings.filter((b) => b.workspace_id !== workspaceId), ...expanded];
  return { ...campus, buildings, totals: campusTotals(buildings).totals };
}
