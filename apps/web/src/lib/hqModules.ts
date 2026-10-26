/**
 * Module panels of a building: commerce (Live Operations, Current Flow) and tyre. A panel shows
 * numbers only when the workspace has the module connected and the API answered; otherwise it
 * says why ("Not connected", "No access", "Unavailable") and links to the setup.
 */
export type ModuleState<T> =
  | Readonly<{ state: 'loading' }>
  | Readonly<{ state: 'not_connected' }>
  | Readonly<{ state: 'forbidden' }>
  | Readonly<{ state: 'error'; message: string }>
  | Readonly<{ state: 'ready'; data: T }>;

export type CommerceLive = Readonly<{
  orders: number; waiting: number; late: number; returns: number; openAlerts: number; currency: string | null;
  /** Listings under their stock threshold (the dashboard's `low_stock` list); null when the answer did not carry it. */
  lowStock: number | null;
}>;

export type TyreLive = Readonly<{
  vehicles: number; fitted: number; inStock: number | null; belowMinimum: number; overdueInspections: number; dueNext30: number; criticalAlerts: number; warningAlerts: number;
}>;

type CommerceDashboardLike = Readonly<{
  by_channel: ReadonlyArray<Readonly<{ orders: number }>>;
  pending_total: number; late_total: number; returns: Readonly<{ count: number }>; open_alerts: number; currency: string | null;
  low_stock?: ReadonlyArray<unknown>;
}>;

export function commerceLive(d: CommerceDashboardLike): CommerceLive {
  return {
    orders: d.by_channel.reduce((n, c) => n + (Number.isFinite(c.orders) ? c.orders : 0), 0),
    waiting: d.pending_total, late: d.late_total, returns: d.returns.count, openAlerts: d.open_alerts, currency: d.currency,
    lowStock: Array.isArray(d.low_stock) ? d.low_stock.length : null,
  };
}

type TyreDashboardLike = Readonly<{
  summary: Readonly<{ vehicles: number; tyres_fitted: number; tyres_in_stock?: number; below_minimum: number; overdue_inspections: number; due_next_30_days: number }>;
  alert_counts: Partial<Record<string, number>>;
}>;

export function tyreLive(d: TyreDashboardLike): TyreLive {
  return {
    vehicles: d.summary.vehicles, fitted: d.summary.tyres_fitted, inStock: Number.isFinite(d.summary.tyres_in_stock) ? (d.summary.tyres_in_stock as number) : null, belowMinimum: d.summary.below_minimum,
    overdueInspections: d.summary.overdue_inspections, dueNext30: d.summary.due_next_30_days,
    criticalAlerts: d.alert_counts.critical ?? 0, warningAlerts: d.alert_counts.warning ?? 0,
  };
}

/** The four steps of the commerce flow strip, in order, each with its real count. */
export function commerceFlow(c: CommerceLive): Array<{ key: 'received' | 'waiting' | 'late' | 'returned'; count: number }> {
  return [
    { key: 'received', count: c.orders },
    { key: 'waiting', count: c.waiting },
    { key: 'late', count: c.late },
    { key: 'returned', count: c.returns },
  ];
}

export function moduleErrorState(status: number | null, message: string): ModuleState<never> {
  if (status === 403) return { state: 'forbidden' };
  return { state: 'error', message };
}
