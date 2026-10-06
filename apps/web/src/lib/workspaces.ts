// Which workspace the web client acts in, and the workspaces it can offer to switch to.
// docs/identity.md (Client Sign-In, Workspace switcher). Pure apart from the injected storage,
// so `pnpm --filter @anum/web test:unit` can exercise it without a browser.
//
// The list combines the caller's active memberships from `GET /api/v1/me/workspace-memberships`
// (when it can be loaded) with what the client knows by itself: the selected workspace, the
// token's default workspace, the configured default, and workspaces this browser joined or
// switched to. Any other workspace can be typed in; the API's membership check in the target
// workspace still decides before every switch.

/** Same pattern the API enforces for `x-workspace-id` (`is_valid_scope_id`). */
export const WORKSPACE_ID_PATTERN = /^[A-Za-z0-9_-]{3,80}$/;
const MAX_KNOWN = 20;

export interface WorkspaceMemory {
  /** The workspace the user picked; null means "use the token's or configured default". */
  selected: string | null;
  /** Workspaces this browser joined or switched to, most recent first. */
  known: string[];
}

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const EMPTY_MEMORY: WorkspaceMemory = { selected: null, known: [] };

export const isWorkspaceId = (value: string): boolean => WORKSPACE_ID_PATTERN.test(value);

/** Storage key per tenant and user, so two people sharing a browser never see each other's list. */
export function memoryKey(tenantId: string, userId: string): string {
  return `anum.workspaces.${tenantId || 'unknown'}.${userId || 'unknown'}`;
}

/** Read the remembered workspaces; anything malformed or unreadable reads as empty. */
export function readMemory(storage: KeyValueStorage | null, key: string): WorkspaceMemory {
  try {
    const raw = storage?.getItem(key);
    if (!raw) return EMPTY_MEMORY;
    const parsed = JSON.parse(raw) as Partial<WorkspaceMemory>;
    const selected = typeof parsed.selected === 'string' && isWorkspaceId(parsed.selected) ? parsed.selected : null;
    const known = Array.isArray(parsed.known) ? parsed.known.filter((x): x is string => typeof x === 'string' && isWorkspaceId(x)).slice(0, MAX_KNOWN) : [];
    return { selected, known };
  } catch {
    return EMPTY_MEMORY;
  }
}

/** Persist; a blocked storage (private window, disabled site data) is ignored. */
export function writeMemory(storage: KeyValueStorage | null, key: string, memory: WorkspaceMemory): void {
  try {
    storage?.setItem(key, JSON.stringify(memory));
  } catch {
    // The selection still holds for this page; it is just not remembered.
  }
}

/** Add a workspace to the front of the known list (deduplicated, capped). */
export function remember(memory: WorkspaceMemory, workspaceId: string): WorkspaceMemory {
  if (!isWorkspaceId(workspaceId)) return memory;
  return { ...memory, known: [workspaceId, ...memory.known.filter((x) => x !== workspaceId)].slice(0, MAX_KNOWN) };
}

/** Select a workspace and remember it. */
export function select(memory: WorkspaceMemory, workspaceId: string): WorkspaceMemory {
  return { ...remember(memory, workspaceId), selected: workspaceId };
}

/** Forget a remembered workspace (not the selected one). */
export function forget(memory: WorkspaceMemory, workspaceId: string): WorkspaceMemory {
  if (memory.selected === workspaceId) return memory;
  return { ...memory, known: memory.known.filter((x) => x !== workspaceId) };
}

/**
 * The workspace requests carry: the user's selection, else the token's `workspace_id` claim,
 * else the configured default, else the development default (docs/identity.md, Client Sign-In).
 */
export function resolveWorkspace(memory: WorkspaceMemory, claim: string | null | undefined, configured: string | null | undefined, fallback: string): string {
  for (const candidate of [memory.selected, claim, configured]) {
    if (candidate && isWorkspaceId(candidate)) return candidate;
  }
  return fallback;
}

export interface WorkspaceOption {
  id: string;
  current: boolean;
  /**
   * Why the client lists it: an active membership the API reported, the default from sign-in or
   * configuration, or this browser's history.
   */
  source: 'member' | 'default' | 'remembered';
  /** Workspace name and the caller's role, when the API's membership list provided them. */
  name?: string | null;
  role?: string;
}

/** One of the caller's memberships as the switcher uses it (`GET /api/v1/me/workspace-memberships`). */
export interface MembershipEntry {
  workspaceId: string;
  workspaceName: string | null;
  role: string;
}

/** Map the API rows; rows with a malformed id are dropped rather than offered. */
export function membershipEntries(rows: readonly { workspace_id: string; workspace_name?: string | null; role: string }[]): MembershipEntry[] {
  return rows
    .filter((row) => typeof row.workspace_id === 'string' && isWorkspaceId(row.workspace_id))
    .map((row) => ({ workspaceId: row.workspace_id, workspaceName: row.workspace_name?.trim() || null, role: row.role }));
}

/**
 * Options for the switcher: current first, then the caller's memberships from the API (when they
 * could be loaded), then defaults, then remembered ones; no duplicates.
 */
export function workspaceOptions(current: string, defaults: (string | null | undefined)[], memory: WorkspaceMemory, memberships: readonly MembershipEntry[] | null = null): WorkspaceOption[] {
  const seen = new Set<string>();
  const options: WorkspaceOption[] = [];
  const byId = new Map((memberships ?? []).map((entry) => [entry.workspaceId, entry]));
  const add = (id: string | null | undefined, source: WorkspaceOption['source']) => {
    if (!id || !isWorkspaceId(id) || seen.has(id)) return;
    seen.add(id);
    const entry = byId.get(id);
    options.push(entry ? { id, current: id === current, source: 'member', name: entry.workspaceName, role: entry.role } : { id, current: id === current, source });
  };
  add(current, defaults.includes(current) ? 'default' : 'remembered');
  for (const entry of memberships ?? []) add(entry.workspaceId, 'member');
  for (const id of defaults) add(id, 'default');
  for (const id of memory.known) add(id, 'remembered');
  return options;
}

/** Second line of an option: its name (when different from the id) and the caller's role. */
export function membershipLabel(option: WorkspaceOption): string | null {
  if (option.source !== 'member') return null;
  const parts = [option.name && option.name !== option.id ? option.name : null, option.role ? `your role: ${option.role}` : null].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'Your membership';
}
