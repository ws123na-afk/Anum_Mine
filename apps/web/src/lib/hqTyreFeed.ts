/**
 * The Tyre Pulse records the campus may animate (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md). Read only, for the open workspace, through the same
 * `/api/v1/tyre` routes the Tyre page uses: the connection (setup), the alert records ANUM derived from the synced data, and the dashboard's
 * stored event times (the newest alert's `latest_alert_at`, the last inspection's day and, when the source keeps one, its time
 * `last_inspection_at`; packages/contracts/src/tyreEventTimes.ts). Only ids, alert kinds and times are kept; subject labels, messages and
 * values never leave this function, and a time that is not a well formed ISO value is dropped, never repaired.
 */
import * as tyre from './tyreApi.ts';
import { ApiError } from './errors.ts';
import { tyreDay, tyreInstant, type TyreFeed } from './hqWorkMotion.ts';

export async function loadTyreFeed(): Promise<TyreFeed> {
  try {
    const setup = await tyre.getSetup();
    if (!setup.connection) return { state: 'not_connected' };
    const [alerts, dashboard] = await Promise.all([
      tyre.getAlerts({ status: 'active', limit: 50, offset: 0 }),
      tyre.getDashboard(),
    ]);
    return {
      state: 'connected',
      alerts: alerts.items.map((a) => ({ id: String(a.id), kind: String(a.kind), firstSeenAt: String(a.first_seen_at) })),
      latestAlertAt: tyreInstant(dashboard.latest_alert_at),
      lastInspection: tyreDay(dashboard.operations?.last_inspection),
      lastInspectionAt: tyreInstant(dashboard.operations?.last_inspection_at),
    };
  } catch (error) {
    return error instanceof ApiError && error.status === 403 ? { state: 'forbidden' } : { state: 'error' };
  }
}
