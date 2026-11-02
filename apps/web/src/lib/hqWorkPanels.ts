/** Work opened inside the headquarters (docs/ANUM_MINE_ASSIGN_WORK.md, "Work inside the office"). A task, an approval, the approvals of a task,
 * a meeting and an agent's work log open as drawers over the scene, never as a page change. Callers ask with
 * `openHqPanel`; it returns false when no headquarters is mounted, and the caller then goes to the module page as before.
 * Pure and DOM free apart from the event, so test/hqWorkPanels.test.ts runs it directly. */
import { assignHostAvailable, openTaskPanel } from './hqAssign.ts';

export const HQ_PANEL_EVENT = 'anum:hq-work-panel';
/** Panels the headquarters itself owns (the live flow dock and the team roster), asked for by the assistant dock. */
export const HQ_SCENE_PANEL_EVENT = 'anum:hq-scene-panel';
export type HqScenePanel = 'flow' | 'team' | 'meetings';

export type HqWorkPanel =
  | Readonly<{ kind: 'task'; id: string }>
  | Readonly<{ kind: 'run'; id: string }>
  | Readonly<{ kind: 'approval'; id: string }>
  /** Pending approvals, of one task when `taskId` is set, else the whole queue. */
  | Readonly<{ kind: 'approvals'; taskId?: string | null }>
  /** A live meeting by id, or the list of meetings to pick from. */
  | Readonly<{ kind: 'meeting'; id?: string | null; roomId?: string | null }>
  | Readonly<{ kind: 'agent'; id: string; name?: string | null }>;

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/;
const ok = (id: string | null | undefined): boolean => typeof id === 'string' && ID.test(id);

/** True when every id in the request is a plain record id (nothing else ever reaches a drawer or a URL). */
export function validPanel(panel: HqWorkPanel): boolean {
  switch (panel.kind) {
    case 'task': case 'run': case 'approval': case 'agent': return ok(panel.id);
    case 'approvals': return panel.taskId == null || ok(panel.taskId);
    case 'meeting': return (panel.id == null || ok(panel.id)) && (panel.roomId == null || ok(panel.roomId));
  }
}

export function openHqPanel(panel: HqWorkPanel, target: Pick<Window, 'dispatchEvent'> = globalThis as unknown as Window): boolean {
  if (!assignHostAvailable() || !validPanel(panel)) return false;
  if (panel.kind === 'task') return openTaskPanel(panel.id, target);
  target.dispatchEvent(new CustomEvent<HqWorkPanel>(HQ_PANEL_EVENT, { detail: panel }));
  return true;
}

export function openHqScenePanel(which: HqScenePanel, target: Pick<Window, 'dispatchEvent'> = globalThis as unknown as Window): boolean {
  if (!assignHostAvailable()) return false;
  target.dispatchEvent(new CustomEvent<HqScenePanel>(HQ_SCENE_PANEL_EVENT, { detail: which }));
  return true;
}

/** The module page a drawer's "Open full page" link goes to. */
export function fullPageHash(panel: HqWorkPanel): string {
  switch (panel.kind) {
    case 'task': case 'run': return '#tasks';
    case 'approval': case 'approvals': return '#approvals';
    case 'meeting': return panel.id ? `#office/meeting/${encodeURIComponent(panel.id)}` : '#meetings';
    case 'agent': return `#agents/team/agent/${encodeURIComponent(panel.id)}`;
  }
}

/** A record named by a history source, a meeting link or a briefing line: the drawer that shows it, if any. */
export function panelForRecord(kind: string, id: string): HqWorkPanel | null {
  if (!ok(id)) return null;
  switch (kind) {
    case 'task': return { kind: 'task', id };
    case 'run': return { kind: 'run', id };
    case 'approval': return { kind: 'approval', id };
    case 'meeting': return { kind: 'meeting', id };
    case 'agent': return { kind: 'agent', id };
    default: return null;
  }
}

/** What a module view asked for by the assistant dock opens inside the headquarters instead. */
export function panelForView(view: string): HqWorkPanel | HqScenePanel | null {
  switch (view) {
    case 'approvals': return { kind: 'approvals' };
    case 'meetings': return { kind: 'meeting' };
    case 'tasks': return 'flow';
    case 'agents': case 'team': return 'team';
    default: return null;
  }
}

/** Opens a module view inside the headquarters when one is mounted; false means the caller navigates as before. */
export function openViewInHq(view: string, target: Pick<Window, 'dispatchEvent'> = globalThis as unknown as Window): boolean {
  const panel = panelForView(view);
  if (!panel) return false;
  return typeof panel === 'string' ? openHqScenePanel(panel, target) : openHqPanel(panel, target);
}

/** onClick for a link that keeps its href (new tab, copy link) but opens the drawer in place when it can. */
export function inHqClick(panel: HqWorkPanel | null) {
  return (event: { preventDefault(): void; metaKey?: boolean; ctrlKey?: boolean; shiftKey?: boolean; button?: number }) => {
    if (!panel || event.metaKey || event.ctrlKey || event.shiftKey || (event.button ?? 0) !== 0) return;
    if (openHqPanel(panel)) event.preventDefault();
  };
}
