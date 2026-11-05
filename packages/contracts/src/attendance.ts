// Work attendance and daily reports (docs/attendance.md#api), mirror of services/api/anum_api/attendance.
// Every record is the person's own; another member reads it only through an active share.

export type AttendanceEventKind = 'arrive' | 'leave' | 'break_start' | 'break_end' | 'remote_start' | 'remote_end';
export type AttendanceEventSource = 'web' | 'device' | 'manual';
export type AttendanceSiteKind = 'workplace' | 'site' | 'client' | 'home';
export type AttendancePriority = 'low' | 'normal' | 'high' | 'urgent';
export type AttendanceFlag =
  | 'missing_checkout' | 'no_arrival' | 'duplicate' | 'late_event' | 'adjusted' | 'poor_location'
  | 'leave_without_arrival' | 'leave_other_site' | 'end_without_start' | 'break_outside_work';

export interface AttendanceShift { weekday: number; start: string; end: string }

export interface AttendanceProfile {
  organisation: string | null;
  department: string | null;
  role_title: string | null;
  workplace_site_id: string | null;
  time_zone: string;
  week_starts_on: number;
  schedule: AttendanceShift[];
  daily_overtime_minutes: number | null;
  weekly_overtime_minutes: number | null;
  location_consent_at: string | null;
  tracking_paused: boolean;
  paused_at: string | null;
  ai_drafts_enabled: boolean;
  missed_check_alerts: boolean;
  version: number;
  updated_at: string | null;
  saved: boolean;
  location_consent: boolean;
  owner_user_id: string;
}

export interface AttendanceProfileInput {
  organisation?: string | null;
  department?: string | null;
  role_title?: string | null;
  workplace_site_id?: string | null;
  time_zone: string;
  week_starts_on: number;
  schedule: AttendanceShift[];
  daily_overtime_minutes: number | null;
  weekly_overtime_minutes: number | null;
  ai_drafts_enabled: boolean;
  missed_check_alerts: boolean;
  expected_version: number;
}

export interface AttendanceSite {
  id: string;
  name: string;
  kind: AttendanceSiteKind;
  address_label: string | null;
  radius_m: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface AttendanceSiteInput { name: string; kind: AttendanceSiteKind; address_label?: string | null; radius_m: number }

export interface AttendanceEvent {
  id: string;
  kind: AttendanceEventKind;
  occurred_at: string;
  received_at: string;
  time_zone: string;
  site_id: string | null;
  source: AttendanceEventSource;
  quality: 'ok' | 'poor';
  status: 'active' | 'voided' | 'superseded';
  note: string | null;
}

export interface AttendanceRecordResult { event: AttendanceEvent; duplicate: boolean }

export interface AttendanceSegment {
  kind: 'site' | 'remote' | 'break';
  start: string;
  end: string | null;
  end_kind: 'event' | 'switch' | 'open' | 'estimated' | 'missing';
  site_id: string | null;
  verified: boolean;
  seconds: number;
  start_event_id: string;
  end_event_id: string | null;
}

export interface AttendanceDay {
  day: string;
  time_zone: string;
  presence_seconds: number;
  remote_seconds: number;
  break_seconds: number;
  worked_seconds: number;
  verified_seconds: number;
  estimated_seconds: number;
  open_seconds: number;
  scheduled_seconds: number;
  overtime_seconds: number | null;
  first_arrival: string | null;
  last_departure: string | null;
  site_ids: string[];
  flags: AttendanceFlag[];
  segments: AttendanceSegment[];
  reduced: boolean;
}

export interface AttendanceToday {
  day: string;
  time_zone: string;
  status: { at_site: boolean; site_id: string | null; on_break: boolean; remote: boolean; since: string | null };
  totals: AttendanceDay | null;
  events: AttendanceEvent[];
  tracking_paused: boolean;
  location_consent: boolean;
  profile_saved: boolean;
}

export interface AttendancePeriod {
  start: string;
  end: string;
  days_worked: number;
  presence_seconds: number;
  remote_seconds: number;
  break_seconds: number;
  worked_seconds: number;
  verified_seconds: number;
  estimated_seconds: number;
  scheduled_seconds: number;
  overtime_seconds: number | null;
  overtime_configured: boolean;
  flags: Partial<Record<AttendanceFlag, number>>;
  days: AttendanceDay[];
}

export interface AttendanceSiteVisit { day: string; site_id: string; start: string; end: string | null; seconds: number; end_kind: string }

export interface AttendanceCorrection {
  id: string;
  action: 'add' | 'change' | 'void';
  event_id: string | null;
  replacement_event_id: string | null;
  before: { kind: AttendanceEventKind; occurred_at: string; site_id: string | null } | null;
  after: { kind: AttendanceEventKind; occurred_at: string; site_id: string | null } | null;
  reason: string;
  created_at: string;
}

export interface AttendanceCorrectionInput {
  action: 'add' | 'change' | 'void';
  event_id?: string;
  kind?: AttendanceEventKind;
  occurred_at?: string;
  site_id?: string | null;
  reason: string;
}

export interface AttendanceReportVisit { site_id: string | null; name: string; note: string | null }

export interface AttendanceDailyReport {
  id: string;
  day: string;
  project: string | null;
  tasks_done: string[];
  tasks_pending: string[];
  site_visits: AttendanceReportVisit[];
  issues: string | null;
  time_spent_minutes: number | null;
  priority: AttendancePriority;
  follow_up_on: string | null;
  notes: string | null;
  file_ids: string[];
  drafted_with_ai: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export type AttendanceDailyReportInput = Omit<AttendanceDailyReport, 'id' | 'day' | 'version' | 'created_at' | 'updated_at'> & {
  expected_version: number | null;
};

export interface AttendanceShare {
  id: string;
  owner_user_id: string;
  viewer_user_id: string;
  include_reports: boolean;
  created_at: string;
  revoked_at: string | null;
}

export interface AttendanceShares { mine: AttendanceShare[]; with_me: AttendanceShare[] }

export interface AttendanceDraft { text: string; period: 'day' | 'week'; start: string; end: string; report_days: string[] }

export interface AttendancePage<T> { items: T[]; total: number }
