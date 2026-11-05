// Settings, Integrations, iPhone: one device enrollment for every iPhone Shortcut (docs/iphone-integrations.md).

export type IphoneScope = 'finance.sms' | 'finance.wallet' | 'vehicle' | 'attendance' | 'documents' | 'purchases';
export type IphoneDeviceStatus = 'active' | 'paused' | 'revoked';
export type IphoneEventOutcome = 'ok' | 'failed' | 'denied' | 'test';

export interface IphoneDevice {
  id: string;
  name: string;
  status: IphoneDeviceStatus;
  token_hint: string;
  scopes: IphoneScope[];
  created_at: string;
  rotated_at: string | null;
  revoked_at: string | null;
  last_seen_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
}

export interface IphoneIntegration {
  key: string;
  scope: IphoneScope;
  enabled: boolean;
  allowed: boolean;
  ingest_path: string;
  method: string;
  body_fields: string[];
  per_minute: number;
  per_day: number;
  devices_with_scope: number;
  last_connection_at: string | null;
  last_ok_at: string | null;
  failed_7d: number;
  needs_safety_check: boolean;
}

export interface IphoneOverview {
  devices: IphoneDevice[];
  integrations: IphoneIntegration[];
  scopes: IphoneScope[];
  max_devices: number;
  safety_check_passed: boolean;
  ping_path: string;
}

export interface IphoneDeviceInput { name: string; scopes: IphoneScope[] }
/** The token is shown once; ANUM keeps only its hash. */
export interface IphoneDeviceToken { device: IphoneDevice; token: string; headers: Record<string, string> }
export interface IphoneTestResult { integration: string; passed: boolean; checks: { check: string; passed: boolean }[] }
export interface IphoneEvent { id: string; device_id: string; outcome: IphoneEventOutcome; code: string | null; created_at: string }
export interface IphoneEventPage { items: IphoneEvent[]; total: number; limit: number; offset: number }
