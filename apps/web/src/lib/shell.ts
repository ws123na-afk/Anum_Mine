// The web shell's model (docs/design/anum360.md#web-command-center): which views exist, how the
// sidebar groups them, hash routing, the command palette's matching and the user menu's name.
// Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser.
import { t, type MessageKey } from './i18n.ts';

export type View =
  | 'home' | 'command' | 'assistant' | 'agents' | 'voice' | 'call' | 'automation' | 'tasks' | 'meetings' | 'integrations' | 'tyre' | 'commerce' | 'files' | 'memory' | 'knowledge' | 'reports' | 'skills'
  | 'favorites' | 'inbox' | 'whatsapp' | 'learning' | 'approvals' | 'activity' | 'autonomy' | 'settings'
  | 'governance' | 'enterprise' | 'members' | 'audit' | 'budgets' | 'policy' | 'developer';

export type NavGroup = 'main' | 'more' | 'admin';

/**
 * The sidebar: the mockup's entries first (Home, ANUM Command, AI Chat, Agents, Voice, Automations,
 * Tasks, Integrations, Documents, Knowledge Base, Reports, ANUM Store), then everything else under
 * More and Admin so every view stays one click away. Calendar from the mockup has no API yet and is
 * not listed.
 *
 * The Knowledge Base entry has two tabs: Memories (`#memory`) and Collections (`#knowledge`,
 * docs/knowledge-base.md). `knowledge` is a tab of `memory` (TABS_OF), not a sidebar entry.
 *
 * Adding a view: add its id to `View`, put it in one group below, give it an icon in Shell.tsx's
 * ICONS (a Record<View>, so a missing icon is a type error), a `nav.<id>` label in both catalogs,
 * and its render line in main.tsx.
 */
export const NAV: readonly { group: NavGroup; views: readonly View[] }[] = [
  { group: 'main', views: ['home', 'command', 'assistant', 'agents', 'voice', 'call', 'automation', 'tasks', 'meetings', 'integrations', 'tyre', 'commerce', 'files', 'memory', 'reports', 'skills'] },
  { group: 'more', views: ['favorites', 'inbox', 'whatsapp', 'learning', 'approvals', 'activity', 'autonomy', 'settings'] },
  { group: 'admin', views: ['governance', 'enterprise', 'members', 'audit', 'budgets', 'policy', 'developer'] },
];

/** Views reached as a tab of a sidebar entry: they open by link and light up their parent entry. */
export const TABS_OF: Readonly<Partial<Record<View, View>>> = { knowledge: 'memory' };
/** The sidebar entry a view belongs to (itself, or the entry it is a tab of). */
export const navParent = (view: View): View => TABS_OF[view] ?? view;
/** Every view a link can open, sidebar entries first. */
export const VIEWS: readonly View[] = [...NAV.flatMap((group) => group.views), ...(Object.keys(TABS_OF) as View[])];
/** Views only an owner sees in the navigation (the view itself explains it if opened by link). */
export const OWNER_ONLY: ReadonlySet<View> = new Set(['developer']);
/** Views that draw their own page header (the Command Center). */
export const OWN_HEADER: ReadonlySet<View> = new Set(['home', 'command']);

export function isView(value: string): value is View {
  return (VIEWS as readonly string[]).includes(value);
}

export const viewLabel = (view: View): string => t(`nav.${view}` as MessageKey);

/** The view a URL hash opens; deep links into Settings and invitations keep working. */
export function viewFromHash(hash: string): View {
  const id = hash.replace(/^#/, '');
  if (id.startsWith('invitation=')) return 'members';
  if (id.startsWith('meetings?')) return 'meetings';
  if (id === 'privacy' || id === 'language' || id === 'notifications') return 'settings';
  // A report or collection opened by link (docs/reports.md, docs/knowledge-base.md).
  if (id.startsWith('reports/')) return 'reports';
  if (id.startsWith('knowledge/')) return 'knowledge';
  return isView(id) ? id : 'home';
}

/** The views a person can open, in sidebar order. */
export function visibleViews(owner: boolean | null): View[] {
  return VIEWS.filter((view) => !(view in TABS_OF) && (!OWNER_ONLY.has(view) || owner === true));
}

// ---------------------------------------------------------------- command palette

export type PaletteKind = 'view' | 'task' | 'file' | 'email';
export interface PaletteItem { kind: PaletteKind; id: string; label: string; detail?: string }

const fold = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();

/** How well `text` matches `query`: 3 exact, 2 prefix, 1 word prefix or substring, 0 none. */
export function matchScore(text: string, query: string): number {
  const q = fold(query);
  if (!q) return 1;
  const value = fold(text);
  if (value === q) return 3;
  if (value.startsWith(q)) return 2;
  return value.includes(q) ? 1 : 0;
}

/** Views whose name matches, best first; an empty query lists them all in sidebar order. */
export function matchViews(query: string, views: readonly View[], label: (view: View) => string = viewLabel): PaletteItem[] {
  return views
    .map((view, index) => ({ view, index, score: matchScore(label(view), query) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((row) => ({ kind: 'view' as const, id: row.view, label: label(row.view) }));
}

/** Cmd+K or Ctrl+K opens the palette (also "/" outside text fields). */
export function isPaletteShortcut(event: { key: string; metaKey: boolean; ctrlKey: boolean; altKey?: boolean }, typing = false): boolean {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k') return true;
  return !typing && event.key === '/' && !event.metaKey && !event.ctrlKey;
}

/** Move the active option with the arrow keys, wrapping at both ends. */
export function moveActive(current: number, count: number, key: string): number {
  if (!count) return -1;
  if (key === 'ArrowDown') return (current + 1) % count;
  if (key === 'ArrowUp') return (current - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return current;
}

// ---------------------------------------------------------------- user menu and notifications

/** A person's display name from their sign-in token claims; null when the token has none. */
export function displayName(claims: Record<string, unknown> | null | undefined): string | null {
  if (!claims) return null;
  for (const key of ['given_name', 'name', 'preferred_username']) {
    const value = claims[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  const email = claims.email;
  if (typeof email === 'string' && email.includes('@')) return email.split('@')[0];
  return null;
}

/** Up to two initials for the avatar (letters of any script). */
export function initials(name: string): string {
  const words = name.replace(/[_.-]+/g, ' ').trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((word) => Array.from(word)[0] ?? '');
  return letters.join('').toUpperCase() || '?';
}

/** The bell's count: pending approvals, automation alerts and "ANUM acted" notices. */
export function notificationCount(input: { pendingApprovals: number; alerts: number; notices: number }): number {
  return input.pendingApprovals + input.alerts + input.notices;
}

/** The count as the badge shows it. */
export const badgeText = (count: number): string => (count > 99 ? '99+' : String(count));
