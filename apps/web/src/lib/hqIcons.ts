/**
 * The headquarters icon set (docs/ANUM_MINE_HQ_LABELS.md, "Icons"): one set of line icons on a 24 unit grid, round caps, drawn with a
 * 1.8 unit stroke, paths only. The label cards draw them as the `--hq-kind-icon` masks of `hqLabels.css` (`data-kind`, `data-dept`); the
 * billboards draw these paths on their canvas. `test/hqIcons.test.ts` keeps the style sheet and this table equal. This module is only
 * imported by the lazy billboard chunk, so the paths cost the headquarters entry nothing. A room card and a billboard draw the icon of a room's
 * stored department (`team_department`, only a department the catalogue names) and fall back to the icon of its kind. Pure: no DOM.
 */

/** The room kind icons, exactly the paths of the `data-kind` masks in `hqLabels.css`. */
export const KIND_ICONS: Readonly<Record<string, string>> = {
  default: 'M4 20V6l8-3 8 3v14M9 20v-5h6v5',
  office: 'M4 8h16v11H4zM9 8V5h6v3M4 13h16',
  meeting: 'M3 17h18M5 17v-3a7 7 0 0 1 14 0v3M9 9h.01M15 9h.01M12 6h.01',
  reception: 'M4 18h16M6 18a6 6 0 0 1 12 0M12 8V5M10 5h4',
  workshop: 'M14 6a4 4 0 1 0 4 4l-3 1-2-2 1-3zM12 12l-7 7',
  warehouse: 'M3 10l9-6 9 6v9H3zM8 19v-6h8v6',
  lounge: 'M5 9h11v5a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4zM16 10h2a2 2 0 0 1 0 4h-2',
  command: 'M3 5h18v11H3zM8 20h8M12 16v4',
};

/** One icon per department of the catalogue (`team.dept.*`), exactly the paths of the `data-dept` masks in `hqLabels.css`. */
export const DEPARTMENT_ICONS: Readonly<Record<string, string>> = {
  sales: 'M4 17l5-5 4 4 7-7M15 9h5v5',
  support: 'M5 14v-2a7 7 0 0 1 14 0v2M5 14h3v5H5zM16 14h3v5h-3z',
  operations: 'M3 7h11v9H3zM14 10h4l3 3v3h-7M5 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0M15 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
  finance: 'M3 7h18v10H3zM9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M6 12h.01M18 12h.01',
  tyre_engineering: 'M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M8 12a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 3v5M12 16v5M3 12h5M16 12h5',
  marketing: 'M3 10v4h4l7 4V6l-7 4zM17 9a4 4 0 0 1 0 6M7 14l1 5h3',
  technology: 'M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14',
  research: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M7 15h10',
  legal: 'M12 4v16M8 20h8M5 7h14M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z',
  compliance: 'M8 4h8v3H8zM6 5H5v15h14V5h-1M9 13l2 2 4-4',
  security: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4',
  hr: 'M6 8a3 3 0 1 0 6 0a3 3 0 1 0-6 0M3 20a6 6 0 0 1 12 0M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 6',
  procurement: 'M3 4h2l2 11h11l2-8H6M9 20h.01M17 20h.01',
  executive: 'M4 18h16M4 18L3 8l5 4 4-7 4 7 5-4-1 10',
  custom: 'M5 5h5v5H5zM14 5h5v5h-5zM5 14h5v5H5zM14 14h5v5h-5z',
};

/** An icon key: `dept:<department>` or `kind:<room kind>`. */
export type IconKey = `dept:${string}` | `kind:${string}`;

/** The icon of a room: its stored department when the set has one, else its kind (an unknown kind takes the default building). */
export function roomIconKey(room: Readonly<{ kind?: string | null; teamDepartment?: string | null }>): IconKey {
  const dept = room.teamDepartment ?? null;
  if (dept && Object.hasOwn(DEPARTMENT_ICONS, dept)) return `dept:${dept}`;
  const kind = room.kind ?? '';
  return `kind:${Object.hasOwn(KIND_ICONS, kind) ? kind : 'default'}`;
}

/** The path of an icon key; an unknown key draws the default building. */
export function iconPath(key: string): string {
  const [set, name] = key.split(':');
  const table = set === 'dept' ? DEPARTMENT_ICONS : KIND_ICONS;
  return (name && Object.hasOwn(table, name) ? table[name] : undefined) ?? KIND_ICONS.default;
}
