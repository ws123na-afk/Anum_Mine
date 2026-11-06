// The Team page (docs/team-work.md#team-page): directory filters, departments with their open work,
// and the role access matrix from `GET /workspaces/current/roles`. Pure, so it is unit tested.
import type { TeamPerson, Workload } from '@anum/contracts';

export type AccessLevel = 'full' | 'edit' | 'read' | 'none';
export interface RoleAccess { role: string; permissions: readonly string[] }
const READ_ACTIONS = new Set(['read', 'use']);

/** The areas (the part before ":") and, per role, how much of each it may do compared with the owner. */
export function accessMatrix(roles: readonly RoleAccess[]): { areas: string[]; levels: Record<string, Record<string, AccessLevel>> } {
  const areaOf = (permission: string) => permission.split(':')[0];
  const areas = [...new Set(roles.flatMap((r) => r.permissions.map(areaOf)))].sort();
  const owner = roles.find((r) => r.role === 'owner');
  const levels: Record<string, Record<string, AccessLevel>> = {};
  for (const role of roles) {
    levels[role.role] = {};
    for (const area of areas) {
      const mine = role.permissions.filter((p) => areaOf(p) === area);
      const full = (owner ?? role).permissions.filter((p) => areaOf(p) === area);
      levels[role.role][area] = !mine.length ? 'none' : mine.length >= full.length ? 'full' : mine.every((p) => READ_ACTIONS.has(p.split(':')[1] ?? '')) ? 'read' : 'edit';
    }
  }
  return { areas, levels };
}

export interface DirectoryFilter { q: string; department: string; role: string }
export const NO_DIRECTORY_FILTER: DirectoryFilter = { q: '', department: '', role: '' };

const fold = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();

/** People matching the search (name, email, job title, user id), department ("none" = without one) and role. */
export function filterPeople(people: readonly TeamPerson[], filter: DirectoryFilter): TeamPerson[] {
  const q = fold(filter.q.trim());
  return people.filter((p) => (!filter.role || p.role === filter.role)
    && (!filter.department || (filter.department === 'none' ? !p.department : p.department === filter.department))
    && (!q || [p.display_name, p.email ?? '', p.job_title ?? '', p.user_id].some((v) => fold(v).includes(q))))
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
}

export interface DepartmentRow { department: string | null; people: TeamPerson[]; open: number; overdue: number; inProgress: number; dueThisWeek: number }

/** People grouped by department (people without one last), with their open, overdue and due-this-week work. */
export function departmentRows(people: readonly TeamPerson[], workload: Workload | null): DepartmentRow[] {
  const rows = new Map<string | null, DepartmentRow>();
  for (const person of people) {
    const key = person.department ?? null;
    const row = rows.get(key) ?? { department: key, people: [], open: 0, overdue: 0, inProgress: 0, dueThisWeek: 0 };
    row.people.push(person);
    const load = workload?.rows.find((r) => r.assignee_type === 'user' && r.assignee_id === person.user_id);
    if (load) { row.open += load.open; row.overdue += load.overdue; row.inProgress += load.in_progress; row.dueThisWeek += load.due_this_week; }
    rows.set(key, row);
  }
  return [...rows.values()].sort((a, b) => (a.department === null ? 1 : b.department === null ? -1 : b.people.length - a.people.length || a.department.localeCompare(b.department)));
}

/** "AS" from "Aisha Saleh"; the first two letters of a single name. */
export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  return (words.length === 1 ? words[0].slice(0, 2) : `${words[0][0]}${words[words.length - 1][0]}`).toUpperCase();
}
