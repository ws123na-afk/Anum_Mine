/**
 * Entry of the billboard chunk, loaded by `CampusView` only when a campus has buildings (docs/ANUM_MINE_HQ_BILLBOARDS.md):
 * the board sites and merged mesh, the pick boxes, the night halos and the label cards. Nothing here reads anything but the campus
 * model, the registry plans and the live flow read, through `billboardModel`.
 */
import { createElement } from 'react';
import type { CampusBuilding } from '../lib/campusModel.ts';
import type { CampusScene } from '../lib/campusScene.ts';
import type { Placement } from '../lib/campusModel.ts';
import type { PlanInput } from '../lib/hqPlanFrame.ts';
import type { Density } from '../lib/hqLabels.ts';
import type { PickTarget } from '../lib/hqMath.ts';
import type { StageLabel } from '../HqStage';
import { BillboardCard } from './BillboardCard.tsx';
import { billboardModel, type BillboardFlow } from './billboardModel.ts';
import { boardMesh, boardSites, type BillboardMesh, type BoardSite } from './billboardSites.ts';

export type BillboardLayer = Readonly<{ sites: readonly BoardSite[]; mesh: BillboardMesh; picks: readonly PickTarget[] }>;

/** The boards of a campus scene, in placement order. */
export function billboardLayer(scene: CampusScene, placements: readonly Placement[]): BillboardLayer {
  const buildings = scene.buildings.map((b) => ({ workspaceId: b.workspaceId, box: b.box, door: b.door, signIndex: b.signIndex }));
  const sites = boardSites({ placements, buildings, monument: scene.monument, extentRadius: scene.extent.radius, canalRadius: scene.canalRadius, signCount: scene.signCount });
  return { sites, mesh: boardMesh(sites, buildings, scene.signCount), picks: sites.map((s) => ({ id: s.workspaceId, box: s.box })) };
}

export type BillboardLabelArgs = Readonly<{
  layer: BillboardLayer;
  buildings: ReadonlyMap<string, CampusBuilding>;
  plans?: ReadonlyMap<string, PlanInput>;
  flow?: BillboardFlow | null;
  selectedId: string | null;
  hoverId: string | null;
  now: number;
  onActivate: (id: string) => void;
  onOpen: (id: string) => void;
  onHover: (id: string | null) => void;
}>;

/** How far (in scene metres along the view) an unfocused card still shows. */
const NEAR = 85;

/** One card per board: full when its building is selected or hovered, compact when the camera is near, never covering the controls. */
export function billboardLabels(args: BillboardLabelArgs): StageLabel[] {
  const out: StageLabel[] = [];
  for (const site of args.layer.sites) {
    const building = args.buildings.get(site.workspaceId);
    if (!building) continue;
    const focused = site.workspaceId === args.selectedId || site.workspaceId === args.hoverId;
    const model = billboardModel({ building, plan: args.plans?.get(site.workspaceId) ?? null, flow: args.flow ?? null, currentWorkspaceId: args.flow?.snapshot?.workspace_id ?? '', now: args.now });
    // A focused card is full when a free spot clear of other cards, panels and controls exists, else compact (a focus still expands it). The others give up detail first.
    const tiers: readonly Density[] = focused ? ['full', 'compact'] : ['compact', 'dot'];
    out.push({
      id: `billboard:${site.workspaceId}`, anchor: site.anchor, priority: focused ? -1 : 1, tiers, ...(focused ? { fine: true } : {}), ...(focused ? {} : { maxDistance: NEAR }),
      node: createElement(BillboardCard, {
        model, selected: site.workspaceId === args.selectedId, onActivate: () => args.onActivate(site.workspaceId), onOpen: () => args.onOpen(site.workspaceId),
        onHover: (on: boolean) => args.onHover(on ? site.workspaceId : null),
      }),
    });
  }
  return out;
}
