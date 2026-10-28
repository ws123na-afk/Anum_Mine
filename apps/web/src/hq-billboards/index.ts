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
export { CLOSE_M, closeBoards } from './billboardSites.ts';
import { boardFace, type BoardFace } from './boardFace.ts';
import { getLocale, t } from '../lib/i18n.ts';

export { startBoardFeed, type BoardFeed, type BoardLogo } from './billboardTexture.ts';
import { faceTexts } from './boardFace.ts';

/** What is on the boards right now, one line per board (the stage's `data-board-texts`, for QA and screen readers' curiosity: never an input). */
export const boardLines = (faces: readonly BoardFace[]): string => faces.map((f) => faceTexts(f).join(' | ')).join('\n');
export type { BoardFace } from './boardFace.ts';
/** How many boards draw the owner's brand header and logo right now (the stage's `data-board-brands` and `data-board-logos`, for QA only). */
export const boardStats = (faces: readonly BoardFace[]): string => `${faces.filter((f) => f.header).length}|${faces.filter((f) => f.logo).length}`;

export type BillboardLayer = Readonly<{ sites: readonly BoardSite[]; mesh: BillboardMesh; picks: readonly PickTarget[] }>;

/** The boards of a campus scene, in placement order. */
export function billboardLayer(scene: CampusScene, placements: readonly Placement[]): BillboardLayer {
  const buildings = scene.buildings.map((b) => ({ workspaceId: b.workspaceId, box: b.box, door: b.door }));
  const sites = boardSites({ placements, buildings, monument: scene.monument, extentRadius: scene.extent.radius, canalRadius: scene.canalRadius, plaza: scene.plaza });
  return { sites, mesh: boardMesh(sites), picks: sites.map((s) => ({ id: s.workspaceId, box: s.box })) };
}

export type BoardFaceArgs = Readonly<{
  layer: BillboardLayer;
  buildings: ReadonlyMap<string, CampusBuilding>;
  plans?: ReadonlyMap<string, PlanInput>;
  flow?: BillboardFlow | null;
  currentWorkspaceId: string;
  /** The presence stream is connected right now (`usePresenceLive` link "live"); the Live tick shows only then. */
  live: boolean;
  fontsReady?: boolean;
  now: number;
  /** The buildings whose board shows the close view (focused, or the camera is near its board); see `closeBoards`. */
  detailIds?: ReadonlySet<string>;
  /** The tier's board cell: a 256 px cell keeps the key line only (the focused building's card carries the details). */
  cell?: 512 | 256;
  /** True when the owner's logo of a building is decoded (the campus sign loader); without it the board keeps its plain header. */
  logoReady?: (buildingId: string, sha: string) => boolean;
}>;

/** One face per board, in site order, from the same model the card uses (so the same whitelisted fields). A site whose building is gone says Not connected. */
export function boardFaces(args: BoardFaceArgs): BoardFace[] {
  const locale = getLocale();
  return args.layer.sites.map((site) => {
    const building = args.buildings.get(site.workspaceId);
    if (!building) return boardFace({ id: site.workspaceId, name: '', kindText: null, kind: 'business', summary: '', status: 'unavailable', accessible: false, brand: null, logo: null, icons: [], rows: [], openLabel: '', unavailableText: '' }, { live: false, rtl: locale === 'ar', fontsReady: args.fontsReady, tr: (k, v) => t(k, v) });
    const model = billboardModel({ building, plan: args.plans?.get(site.workspaceId) ?? null, flow: args.flow ?? null, currentWorkspaceId: args.currentWorkspaceId, now: args.now });
    const detail = args.cell !== 256 && args.detailIds?.has(site.workspaceId) === true;
    return boardFace(model, { live: args.live, rtl: locale === 'ar', fontsReady: args.fontsReady, tr: (k, v) => t(k, v), detail, logoReady: args.logoReady });
  });
}

export type BillboardLabelArgs = Readonly<{
  layer: BillboardLayer;
  buildings: ReadonlyMap<string, CampusBuilding>;
  plans?: ReadonlyMap<string, PlanInput>;
  flow?: BillboardFlow | null;
  selectedId: string | null;
  hoverId: string | null;
  /** The camera is close enough for unfocused cards to show; otherwise only the focused building has a card in the page. */
  near: boolean;
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
    if (!focused && !args.near) continue;
    const model = billboardModel({ building, plan: args.plans?.get(site.workspaceId) ?? null, flow: args.flow ?? null, currentWorkspaceId: args.flow?.snapshot?.workspace_id ?? '', now: args.now });
    // A focused card is always full and is placed first (priority -1), so the other cards move round it and it keeps clear of the panels and controls.
    // An unfocused card has priority 1.9: the building cards (priority 1) are placed first and stay above it, so it never takes a click meant for one.
    // On a stage under 700 px wide its body is folded by the style sheet (the detail panel already covers the building) and a focus unfolds it.
    const tiers: readonly Density[] = focused ? ['full'] : ['compact', 'dot'];
    out.push({
      id: `billboard:${site.workspaceId}`, anchor: site.anchor, priority: focused ? -1 : 1.9, tiers, ...(focused ? { fine: true } : {}), ...(focused ? {} : { maxDistance: NEAR }),
      node: createElement(BillboardCard, {
        model, selected: site.workspaceId === args.selectedId, focused, onActivate: () => args.onActivate(site.workspaceId), onOpen: () => args.onOpen(site.workspaceId),
        onHover: (on: boolean) => args.onHover(on ? site.workspaceId : null),
      }),
    });
  }
  return out;
}
