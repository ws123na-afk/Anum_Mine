/**
 * Tyre Pulse event times, read only (wire format: snake_case JSON). Server: `GET /api/v1/tyre/dashboard` in
 * services/api/anum_api/tyre/api.py and fleet.py. Docs: docs/tyre-planning.md, docs/ANUM_MINE_HQ_CAMPUS_MOTION.md.
 *
 * Both values are ISO 8601 instants in UTC, taken from stored records and never estimated, or null when unknown:
 * - `operations.last_inspection_at`: the time of the last inspection, only when every inspection of that day stores a
 *   time. A source that keeps dates only gives null, and `operations.last_inspection` (a calendar day) is all there is.
 * - `latest_alert_at`: when ANUM first saw the newest alert that is not resolved (its stored `first_seen_at`).
 */
export interface TyreOperationsEventTimes {
  last_inspection: string | null;
  last_inspection_at?: string | null;
}

export interface TyreDashboardEventTimes {
  operations?: TyreOperationsEventTimes;
  latest_alert_at?: string | null;
}
