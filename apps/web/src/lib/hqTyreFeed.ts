/**
 * The Tyre Pulse records the campus may animate (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md). Read only, for the open workspace, through the same
 * `/api/v1/tyre` routes the Tyre page uses: the connection (setup), the alert records ANUM derived from the synced data, and the dashboard's
 * date of the last inspection. Only ids, alert kinds and times are kept; subject labels, messages and values never leave this function.
 */
import * as tyre from './tyreApi.ts';
import { ApiError } from './errors.ts';
import type { TyreFeed } from './hqWorkMotion.ts';

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
      lastInspection: dashboard.operations?.last_inspection ? String(dashboard.operations.last_inspection).slice(0, 10) : null,
    };
  } catch (error) {
    return error instanceof ApiError && error.status === 403 ? { state: 'forbidden' } : { state: 'error' };
  }
}
