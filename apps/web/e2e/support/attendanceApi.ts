// Mocked /api/v1/attendance answers (docs/attendance.md) shared by attendance.spec.ts and the workspace mock
// (accessibility, layout and journey gates). Synthetic values only: example site names, round times, no coordinates.
const now = new Date();
const pad = (n: number) => String(n).padStart(2, '0');
const dubaiDay = (offset = 0) => { const d = new Date(now.getTime() + 4 * 3600_000 + offset * 86_400_000); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; };
/** An instant at a Dubai local hour on a day offset from today. */
const at = (offset: number, hour: number) => new Date(`${dubaiDay(offset)}T${pad(hour)}:00:00+04:00`).toISOString();
const stamp = now.toISOString();

type Row = Record<string, unknown>;
const H = 3600;
function teamRow(userId: string, extra: Row = {}): Row {
  return { user_id: userId, me: false, day: dubaiDay(0), time_zone: 'Asia/Dubai', role_title: null, status: { at_site: false, site_id: null, on_break: false, remote: false, since: null },
    site_names: [], first_check_in: null, first_check_in_source: null, breaks: [], break_seconds: 0, worked_seconds: 0, presence_seconds: 0, remote_seconds: 0,
    scheduled_seconds: 0, open: false, flags: [], reports_shared: false, report: null, ...extra };
}
export interface AttendanceState {
  profile: Row;
  sites: Row[];
  events: Row[];
  corrections: Row[];
  reports: Row[];
  shares: { mine: Row[]; with_me: Row[] };
  denied: boolean;
  failToday: boolean;
  aiText: string | null;
  /** Colleagues who share their record with the signed-in person (read only), with their reports when shared. */
  colleagues: Row[];
  colleagueReports: Record<string, Row[]>;
}

const site = (id: string, name: string, kind = 'workplace') => ({ id, name, kind, address_label: null, radius_m: 150, archived: false, created_at: stamp, updated_at: stamp });
const event = (id: string, kind: string, occurred: string, extra: Row = {}) => ({
  id, kind, occurred_at: occurred, received_at: occurred, time_zone: 'Asia/Dubai', site_id: 'att_site_office', source: 'web', quality: 'ok', status: 'active', note: null, ...extra,
});

export function attendanceState({ empty = false, denied = false, failToday = false, checkedIn = false }: { empty?: boolean; denied?: boolean; failToday?: boolean; checkedIn?: boolean } = {}): AttendanceState {
  const events = empty ? [] : [
    event('att_evt_1', 'arrive', at(-1, 8)), event('att_evt_2', 'arrive', at(-1, 11), { site_id: 'att_site_tower', source: 'device' }),
    event('att_evt_3', 'leave', at(-1, 13), { site_id: 'att_site_tower', source: 'device' }), event('att_evt_4', 'arrive', at(-1, 14)),
    event('att_evt_5', 'leave', at(-1, 17), { source: 'manual' }),
    event('att_evt_6', 'arrive', at(-2, 22)),
    ...(checkedIn ? [event('att_evt_7', 'arrive', new Date(now.getTime() - 2 * 3600_000).toISOString())] : []),
  ];
  return {
    profile: {
      owner_user_id: 'user_local', organisation: empty ? null : 'Example Contracting', department: empty ? null : 'Field', role_title: empty ? null : 'Site engineer',
      workplace_site_id: empty ? null : 'att_site_office', time_zone: 'Asia/Dubai', week_starts_on: 0,
      schedule: empty ? [] : [0, 1, 2, 3, 4].map((weekday) => ({ weekday, start: '08:00', end: '17:00' })), daily_overtime_minutes: empty ? null : 480,
      weekly_overtime_minutes: null, location_consent_at: empty ? null : stamp, tracking_paused: false, paused_at: null, ai_drafts_enabled: !empty,
      missed_check_alerts: true, last_alert_day: null, version: empty ? 0 : 1, updated_at: empty ? null : stamp, saved: !empty, location_consent: !empty,
      tenant_id: 'tenant_local', workspace_id: 'workspace_foundation',
    },
    sites: empty ? [] : [site('att_site_office', 'Head office'), site('att_site_tower', 'Tower B site', 'site')],
    events,
    corrections: empty ? [] : [{ id: 'att_fix_1', action: 'add', event_id: null, replacement_event_id: 'att_evt_5', before: null, after: { kind: 'leave', occurred_at: at(-1, 17), site_id: 'att_site_office', source: 'manual' }, reason: 'Forgot to check out', created_at: stamp }],
    reports: empty ? [] : [{ id: 'att_rep_1', day: dubaiDay(-1), project: 'Tower B', tasks_done: ['Checked the pumps', 'Signed the delivery note'], tasks_pending: ['Order valves'],
      site_visits: [{ site_id: 'att_site_tower', name: 'Tower B site', note: null }], issues: 'Late cement delivery', time_spent_minutes: 420, priority: 'high',
      follow_up_on: dubaiDay(2), notes: null, file_ids: [], drafted_with_ai: false, version: 1, created_at: stamp, updated_at: stamp }],
    shares: { mine: [], with_me: [] },
    denied, failToday,
    aiText: 'Checked the pumps and signed the delivery note on Tower B. Valves still need ordering.',
    colleagues: empty ? [] : [
      teamRow('user_manager', { role_title: 'Operations lead', status: { at_site: true, site_id: 'x', on_break: false, remote: false, since: at(0, 8) }, site_names: ['Head office'],
        first_check_in: at(0, 8), first_check_in_source: 'device', breaks: [{ start: at(0, 12), end: at(0, 13) }], break_seconds: H, worked_seconds: 3 * H, presence_seconds: 4 * H,
        scheduled_seconds: 8 * H, reports_shared: true, report: { status: 'submitted', updated_at: at(0, 9), submitted_at: at(0, 9), summary: 'Supplier follow ups' } }),
      teamRow('user_field', { role_title: 'Field coordinator', scheduled_seconds: 8 * H }),
    ],
    colleagueReports: empty ? {} : { user_manager: [{ id: 'att_rep_m1', day: dubaiDay(0), project: null, tasks_done: ['Supplier follow ups'], tasks_pending: ['Vendor meeting'],
      site_visits: [], issues: 'Waiting for a quote', time_spent_minutes: null, priority: 'normal', follow_up_on: null, notes: null, file_ids: [], drafted_with_ai: false,
      version: 1, created_at: stamp, updated_at: stamp, submitted_at: at(0, 9) }] },
  };
}

function dayRow(day: string, worked: number, extra: Row = {}) {
  return { day, time_zone: 'Asia/Dubai', presence_seconds: worked, remote_seconds: 0, break_seconds: 0, worked_seconds: worked, verified_seconds: worked,
    estimated_seconds: 0, open_seconds: 0, scheduled_seconds: 9 * H, overtime_seconds: Math.max(0, worked - 8 * H), first_arrival: null, last_departure: null,
    site_ids: ['att_site_office'], flags: [], segments: [], reduced: false, ...extra };
}

function days(state: AttendanceState): Row[] {
  if (!state.events.length) return [];
  return [
    dayRow(dubaiDay(-2), 0, { flags: ['missing_checkout'], verified_seconds: 0, presence_seconds: 0, first_arrival: at(-2, 22),
      segments: [{ kind: 'site', start: at(-2, 22), end: at(-2, 22), end_kind: 'missing', site_id: 'att_site_office', verified: false, seconds: 0, start_event_id: 'att_evt_6', end_event_id: null }] }),
    dayRow(dubaiDay(-1), 8 * H, { first_arrival: at(-1, 8), last_departure: at(-1, 17), site_ids: ['att_site_office', 'att_site_tower'], flags: ['adjusted'], segments: [
      { kind: 'site', start: at(-1, 8), end: at(-1, 11), end_kind: 'switch', site_id: 'att_site_office', verified: true, seconds: 3 * H, start_event_id: 'att_evt_1', end_event_id: 'att_evt_2' },
      { kind: 'site', start: at(-1, 11), end: at(-1, 13), end_kind: 'event', site_id: 'att_site_tower', verified: true, seconds: 2 * H, start_event_id: 'att_evt_2', end_event_id: 'att_evt_3' },
      { kind: 'site', start: at(-1, 14), end: at(-1, 17), end_kind: 'event', site_id: 'att_site_office', verified: true, seconds: 3 * H, start_event_id: 'att_evt_4', end_event_id: 'att_evt_5' },
    ] }),
  ];
}

function today(state: AttendanceState): Row {
  const mine = state.events.filter((e) => String(e.occurred_at) >= at(0, 0));
  const open = mine.filter((e) => e.kind === 'arrive' && e.status === 'active').length > mine.filter((e) => e.kind === 'leave' && e.status === 'active').length;
  const last = mine.at(-1);
  return {
    day: dubaiDay(0), time_zone: 'Asia/Dubai',
    status: { at_site: open, site_id: open ? (last?.site_id ?? null) : null, on_break: false, remote: false, since: open ? (last?.occurred_at ?? null) : null },
    totals: open ? dayRow(dubaiDay(0), 2 * H, { verified_seconds: 0, open_seconds: 2 * H, overtime_seconds: 0 }) : null,
    events: mine, tracking_paused: state.profile.tracking_paused, location_consent: state.profile.location_consent, profile_saved: state.profile.saved,
  };
}

export function attendanceReply(state: AttendanceState, method: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const path = url.pathname;
  if (!path.startsWith('/api/v1/attendance')) return null;
  return attendanceOwnReply(state, method, url, body);
}

/** Colleagues for the share picker (Team people), used by attendance.spec.ts only. */
export const ATTENDANCE_PEOPLE = [
    { user_id: 'user_local', role: 'member', display_name: 'You', has_profile: false, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'Asia/Dubai', assignable: true },
    { user_id: 'user_manager', role: 'owner', display_name: 'Layla Manager', has_profile: false, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'Asia/Dubai', assignable: true },
    { user_id: 'user_field', role: 'member', display_name: 'Omar Field', has_profile: false, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'Asia/Dubai', assignable: true },
];

function attendanceOwnReply(state: AttendanceState, method: string, url: URL, body: unknown): { status: number; body: unknown } {
  const path = url.pathname;
  if (state.denied) return { status: 403, body: { error: { code: 'forbidden', message: 'Attendance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.', details: [] } } };
  const sub = path.slice('/api/v1/attendance'.length);
  const b = (body ?? {}) as Row;
  if (method === 'GET' && sub === '/today') {
    if (state.failToday) return { status: 503, body: { error: { code: 'unavailable', message: 'The database is not answering.', details: [] } } };
    // A colleague's shared record keeps their own time zone (here Karachi), unlike the signed-in person's Dubai.
    return { status: 200, body: url.searchParams.get('person') ? { ...today(state), time_zone: 'Asia/Karachi' } : today(state) };
  }
  if (method === 'GET' && sub === '/team') {
    const t = today(state) as { status: Row; totals: Row | null; events: Row[] };
    const mineToday = state.reports.find((r) => r.day === dubaiDay(0));
    const first = t.events.find((e) => e.kind === 'arrive');
    const me = teamRow('user_local', { me: true, role_title: state.profile.role_title, status: t.status, site_names: first ? ['Head office'] : [],
      first_check_in: first?.occurred_at ?? null, first_check_in_source: first?.source ?? null, worked_seconds: Number(t.totals?.worked_seconds ?? 0),
      scheduled_seconds: state.profile.schedule && (state.profile.schedule as Row[]).length ? 9 * H : 0, reports_shared: true,
      report: mineToday ? { status: mineToday.submitted_at ? 'submitted' : 'draft', updated_at: mineToday.updated_at, submitted_at: mineToday.submitted_at ?? null, summary: (mineToday.tasks_done as string[]).join('; ') } : { status: 'none', updated_at: null, submitted_at: null, summary: null } });
    const rows = [me, ...state.colleagues];
    return { status: 200, body: { day: url.searchParams.get('date'), generated_at: new Date().toISOString(), rows, total: rows.length } };
  }
  if (method === 'GET' && sub === '/profile') return { status: 200, body: state.profile };
  if (method === 'PUT' && sub === '/profile') {
    state.profile = { ...state.profile, ...b, version: Number(state.profile.version) + 1, saved: true };
    return { status: 200, body: state.profile };
  }
  if (method === 'POST' && sub === '/tracking') {
    const action = b.action;
    state.profile = { ...state.profile, location_consent: action !== 'withdraw', location_consent_at: action === 'withdraw' ? null : stamp, tracking_paused: action === 'pause' };
    return { status: 200, body: state.profile };
  }
  if (method === 'GET' && sub === '/sites') return { status: 200, body: state.sites };
  if (method === 'POST' && sub === '/sites') {
    const created = site(`att_site_${state.sites.length + 1}`, String(b.name), String(b.kind ?? 'site'));
    state.sites.push(created);
    return { status: 201, body: created };
  }
  if (method === 'DELETE' && sub.startsWith('/sites/')) {
    const id = decodeURIComponent(sub.split('/')[2]);
    state.sites = state.sites.map((s) => (s.id === id ? { ...s, archived: true } : s));
    return { status: 204, body: null };
  }
  if (method === 'POST' && sub === '/check') {
    const existing = state.events.find((e) => e.note === b.idempotency_key);
    if (existing) return { status: 200, body: { event: existing, duplicate: true } };
    const created = event(`att_evt_${state.events.length + 10}`, String(b.kind), new Date().toISOString(), { site_id: b.site_id ?? null, note: b.idempotency_key });
    state.events.push(created);
    return { status: 201, body: { event: created, duplicate: false } };
  }
  if (method === 'GET' && sub === '/events') return { status: 200, body: state.events };
  if (method === 'GET' && sub === '/days') return { status: 200, body: days(state) };
  if (method === 'GET' && sub === '/summary') {
    const list = days(state);
    const worked = list.reduce((sum, d) => sum + Number(d.worked_seconds), 0);
    return { status: 200, body: { start: dubaiDay(-6), end: dubaiDay(0), days_worked: list.filter((d) => Number(d.worked_seconds) > 0).length, presence_seconds: worked,
      remote_seconds: 0, break_seconds: 0, worked_seconds: worked, verified_seconds: worked, estimated_seconds: 0, scheduled_seconds: 45 * H, overtime_seconds: 0,
      overtime_configured: state.profile.daily_overtime_minutes !== null, flags: {}, days: list } };
  }
  if (method === 'GET' && sub.startsWith('/export.')) return { status: 200, body: 'date,time_zone\n' };
  if (method === 'GET' && sub === '/corrections') return { status: 200, body: { items: state.corrections, total: state.corrections.length } };
  if (method === 'POST' && sub === '/corrections') {
    const created = { id: `att_fix_${state.corrections.length + 2}`, action: b.action, event_id: b.event_id ?? null, replacement_event_id: null, before: null,
      after: b.action === 'void' ? null : { kind: b.kind, occurred_at: b.occurred_at, site_id: b.site_id ?? null, source: 'manual' }, reason: b.reason, created_at: new Date().toISOString() };
    state.corrections.unshift(created);
    return { status: 201, body: created };
  }
  const person = url.searchParams.get('person');
  if (method === 'GET' && person && sub.startsWith('/daily-reports')) {
    const theirs = state.colleagueReports[person];
    if (!theirs) return { status: 404, body: { error: { code: 'not_found', message: 'Not found. This person has not shared their attendance with you, or stopped sharing it.', details: [] } } };
    if (sub === '/daily-reports') return { status: 200, body: { items: theirs, total: theirs.length } };
    const found = theirs.find((r) => r.day === decodeURIComponent(sub.split('/')[2]));
    return found ? { status: 200, body: found } : { status: 404, body: { error: { code: 'not_found', message: 'No daily report for that day yet.', details: [] } } };
  }
  if (method === 'GET' && sub === '/daily-reports') {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const items = state.reports.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
    return { status: 200, body: { items, total: items.length } };
  }
  if (method === 'POST' && sub === '/daily-reports/draft') return { status: 200, body: { text: state.aiText, period: b.period, start: dubaiDay(-6), end: dubaiDay(0), report_days: [dubaiDay(-1)] } };
  if (sub.startsWith('/daily-reports/')) {
    const day = decodeURIComponent(sub.split('/')[2]);
    const found = state.reports.find((r) => r.day === day);
    if (method === 'GET') return found ? { status: 200, body: found } : { status: 404, body: { error: { code: 'not_found', message: 'No daily report for that day yet.', details: [] } } };
    if (method === 'PUT') {
      const { submit, ...fields } = b;
      const saved = { ...(found ?? { id: `att_rep_${state.reports.length + 2}`, created_at: stamp, submitted_at: null }), ...fields, day, version: found ? Number(found.version) + 1 : 1, updated_at: stamp,
        submitted_at: found?.submitted_at ?? (submit ? new Date().toISOString() : null) };
      state.reports = [saved, ...state.reports.filter((r) => r.day !== day)];
      return { status: found ? 200 : 201, body: saved };
    }
    if (method === 'DELETE') { state.reports = state.reports.filter((r) => r.day !== day); return { status: 204, body: null }; }
  }
  if (method === 'GET' && sub === '/shares') return { status: 200, body: state.shares };
  if (method === 'POST' && sub === '/shares') {
    const created = { id: 'att_shr_1', owner_user_id: 'user_local', viewer_user_id: b.viewer_user_id, include_reports: Boolean(b.include_reports), created_at: stamp, revoked_at: null };
    state.shares.mine.push(created);
    return { status: 201, body: created };
  }
  if (method === 'DELETE' && sub.startsWith('/shares/')) { state.shares.mine = []; return { status: 204, body: null }; }
  return { status: 404, body: { error: { code: 'not_found', message: 'Not found.', details: [] } } };
}
