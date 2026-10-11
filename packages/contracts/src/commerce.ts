// Wire shapes of /api/v1/commerce (services/api/anum_api/commerce, docs/commerce.md), snake_case
// as the API sends them. Money is per currency (SAR, AED); amounts are numbers with two decimals.

export type CommerceChannel = 'amazon' | 'noon' | 'own_store';
export type ConnectorHealth = 'pending' | 'ok' | 'throttled' | 'auth_failed' | 'error' | 'disabled';
export type OwnStoreMode = 'anum360' | 'rest' | 'supabase' | 'webhook';
export type CommerceOrderStatus = 'pending' | 'unshipped' | 'partially_shipped' | 'shipped' | 'delivered' | 'cancelled' | 'returned';
export type CommerceAlertKind = 'low_stock' | 'late_shipment' | 'return_spike' | 'buy_box_lost' | 'competitor_price' | 'sync_failed';
export type CommerceAlertSeverity = 'info' | 'warning' | 'critical';
export type CommerceAlertStatus = 'open' | 'acknowledged' | 'resolved';

export interface CommercePermissions { read: boolean; operate: boolean; manage: boolean; pii: boolean }

export interface CommerceMarketplace { id: string; name: string; country?: string; currency?: string }

export interface CommerceSetup {
  permissions: CommercePermissions;
  connectors: number;
  channels: { channel: CommerceChannel; marketplaces: CommerceMarketplace[] }[];
  own_store_modes: OwnStoreMode[];
  sync_scheduler: boolean;
}

export interface CommerceConnector {
  id: string;
  channel: CommerceChannel;
  name: string;
  marketplaces: string[];
  marketplace_names: string[];
  settings: Record<string, unknown>;
  enabled: boolean;
  credentials_configured: boolean;
  credential_hint: string | null;
  health: ConnectorHealth;
  last_error: string | null;
  last_sync_at: string | null;
  last_success_at: string | null;
  next_sync_at: string | null;
  sync_counts: Record<string, number>;
  capabilities: string[];
  webhook_path: string | null;
  created_at: string;
  updated_at: string;
}

/** POST /connectors and PATCH /connectors/{id}. Credentials are write-only. */
export interface CommerceConnectorInput {
  channel?: CommerceChannel;
  name?: string;
  marketplaces?: string[];
  settings?: Record<string, unknown>;
  credentials?: Record<string, string>;
  enabled?: boolean;
}

export interface CommerceConnectorCreated { connector: CommerceConnector; webhook_secret: string | null }

export interface CommerceSyncReport {
  connector_id: string;
  status: 'ok' | 'partial' | 'throttled' | 'auth_failed' | 'error' | 'skipped';
  message: string;
  counts: Record<string, number>;
  alerts_raised: number;
}

export interface TyreSpec {
  size?: string | null;
  width_mm?: number | null;
  aspect_ratio?: number | null;
  construction?: string | null;
  rim_inches?: number | null;
  load_index?: string | null;
  speed_index?: string | null;
  dot_week?: number | null;
  dot_year?: number | null;
  season?: string | null;
}

export interface CommerceListing {
  id: string;
  connector_id: string;
  channel: CommerceChannel;
  marketplace_id: string;
  marketplace: string;
  sku: string;
  external_id: string | null;
  title: string;
  price: number | null;
  currency: string | null;
  quantity: number | null;
  status: string;
  fulfillment: string | null;
  buy_box_price: number | null;
  buy_box_winner: boolean | null;
  lowest_competitor_price: number | null;
  updated_at: string;
}

export interface CommerceProduct {
  id: string;
  sku: string;
  title: string;
  title_ar: string | null;
  brand: string | null;
  tyre: TyreSpec;
  cost_price: number | null;
  currency: string | null;
  low_stock_threshold: number | null;
  listings: CommerceListing[];
  total_stock: number | null;
  created_at: string;
  updated_at: string;
}

export interface CommerceProductInput {
  sku?: string;
  title?: string | null;
  title_ar?: string | null;
  brand?: string | null;
  tyre?: TyreSpec | null;
  cost_price?: number | null;
  currency?: string | null;
  low_stock_threshold?: number | null;
}

export interface CommerceOrderLine { sku: string; external_line_id: string | null; title: string; quantity: number; unit_price: number | null; total: number | null }

export interface CommerceOrder {
  id: string;
  connector_id: string;
  channel: CommerceChannel;
  marketplace_id: string;
  marketplace: string;
  external_id: string;
  status: CommerceOrderStatus;
  purchased_at: string;
  ship_by: string | null;
  total_amount: number;
  currency: string | null;
  item_count: number;
  fulfillment: string | null;
  ship_city: string | null;
  ship_country: string | null;
  lines: CommerceOrderLine[];
  late: boolean;
}

export interface CommerceCustomer {
  id: string;
  city: string | null;
  country: string | null;
  orders_count: number;
  pii_visible: boolean;
  name: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
}

export interface CommerceReturn {
  id: string;
  channel: CommerceChannel;
  order_external_id: string;
  sku: string | null;
  quantity: number;
  reason: string | null;
  status: string;
  amount: number | null;
  currency: string | null;
  returned_at: string;
}

export interface CommerceOrderDetail extends CommerceOrder { customer: CommerceCustomer | null; returns: CommerceReturn[] }

export interface CommerceAlert {
  id: string;
  kind: CommerceAlertKind;
  severity: CommerceAlertSeverity;
  title: string;
  detail: string;
  subject: string | null;
  channel: CommerceChannel | null;
  connector_id: string | null;
  status: CommerceAlertStatus;
  acknowledged_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommercePage<T> { items: T[]; total: number; limit: number; offset: number }

export interface CommerceSettings {
  low_stock_threshold: number;
  handling_hours: number;
  return_spike_count: number;
  return_spike_ratio: number;
  price_alert_percent: number;
  sync_interval_minutes: number;
  updated_at?: string | null;
}

export interface CommerceDashboard {
  start: string;
  end: string;
  tz: string;
  currency: string | null;
  currencies: { currency: string; revenue: number; orders: number; units: number }[];
  by_channel: { channel: CommerceChannel; label: string; marketplace_id: string; marketplace: string; currency: string; revenue: number; orders: number; units: number }[];
  by_day: { date: string; channel: CommerceChannel; revenue: number; orders: number; units: number }[];
  top_products: { sku: string; title: string; title_ar: string | null; tyre_size: string | null; units: number; revenue: number; currency: string }[];
  pending_orders: { id: string; external_id: string; channel: CommerceChannel; marketplace: string; status: CommerceOrderStatus; purchased_at: string; ship_by: string | null; late: boolean; total_amount: number; currency: string | null; item_count: number }[];
  pending_total: number;
  late_total: number;
  low_stock: { listing_id: string; sku: string; title: string; channel: CommerceChannel; marketplace: string; quantity: number; threshold: number }[];
  returns: { count: number; units: number; amount: Record<string, number>; rate: number };
  price_alerts: { listing_id: string; sku: string; marketplace: string; price: number | null; buy_box_price: number | null; buy_box_winner: boolean | null; lowest_competitor_price: number | null; currency: string | null }[];
  open_alerts: number;
  fees: Record<string, number>;
  truncated: boolean;
  connectors: number;
}

export interface CommerceSummary { text: string; data: Record<string, unknown> }
