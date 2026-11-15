// Vehicles, fuel and mileage (docs/vehicles.md#api), mirror of services/api/anum_api/vehicles.
// Records hold quantities, odometer values and details. Money is never posted here: a cost points at a
// canonical Finance transaction through a finance link, and `payment` is a read copy of that link.

export type VehicleOwnership = 'personal' | 'business';
export type VehicleCountry = 'SA' | 'AE';
export type VehicleType = 'car' | 'suv' | 'pickup' | 'van' | 'truck' | 'motorcycle' | 'bus' | 'other';
export type VehicleFuelType = 'petrol_91' | 'petrol_95' | 'petrol_98' | 'diesel' | 'electric' | 'hybrid' | 'lpg' | 'other';
export type VehicleServiceKind =
  | 'maintenance' | 'oil' | 'tyres' | 'battery' | 'repair' | 'inspection' | 'insurance' | 'registration' | 'fine' | 'toll'
  | 'parking' | 'warranty' | 'wash' | 'other';
export type VehiclePaymentMethod = 'card' | 'apple_pay' | 'cash' | 'fleet_card' | 'bank_transfer' | 'other';
export type VehicleTripPurpose = 'business' | 'personal' | 'commute';
export type VehicleCostKind = 'fuel_entry' | 'service_record' | 'purchase';
export type VehicleRecordKind = 'fuel' | 'trips' | 'service';

export const VEHICLE_FUEL_TYPES: VehicleFuelType[] = ['petrol_91', 'petrol_95', 'petrol_98', 'diesel', 'electric', 'hybrid', 'lpg', 'other'];
export const VEHICLE_TYPES: VehicleType[] = ['car', 'suv', 'pickup', 'van', 'truck', 'motorcycle', 'bus', 'other'];
export const VEHICLE_SERVICE_KINDS: VehicleServiceKind[] = [
  'maintenance', 'oil', 'tyres', 'battery', 'repair', 'inspection', 'insurance', 'registration', 'fine', 'toll', 'parking', 'warranty', 'wash', 'other',
];
export const VEHICLE_PAYMENT_METHODS: VehiclePaymentMethod[] = ['card', 'apple_pay', 'cash', 'fleet_card', 'bank_transfer', 'other'];

/** A read copy of the finance link behind a cost. Another member's payment shows no ledger ids. */
export interface VehiclePayment {
  link_id: string | null;
  canonical_id: string | null;
  status: 'active' | 'needs_review' | 'void';
  status_reason: string | null;
  amount: string;
  currency: string;
  paid_by: string;
  recorded_as_cash: boolean;
  linked_at: string;
  /** `allocation`: a business vehicle's cost paid from a personal card and allocated to this business in Finance
   * (docs/vehicles.md#business-allocations); `link_id` and `canonical_id` are empty, `source_ref` quotes the row. */
  source?: 'link' | 'allocation';
  allocation_id?: string | null;
  source_ref?: string | null;
}

export interface VehicleDueItem {
  vehicle_id: string;
  registration: string;
  kind: 'insurance' | 'registration' | 'inspection' | 'service';
  service_kind?: VehicleServiceKind;
  source_id: string;
  due_on: string | null;
  days_left: number | null;
  due_km: string | null;
  km_left: string | null;
}

export interface Vehicle {
  id: string;
  owner_user_id: string;
  ownership: VehicleOwnership;
  registration: string;
  /** Masked to the last four characters in lists and for people who may not edit the vehicle. */
  vin: string | null;
  vin_masked: boolean;
  nickname: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  vehicle_type: VehicleType;
  country: VehicleCountry;
  currency: string;
  fuel_type: VehicleFuelType;
  tank_litres: string | null;
  acquisition_date: string | null;
  purchase_price: string | null;
  residual_value: string | null;
  useful_life_months: number | null;
  purchase_payment: VehiclePayment | null;
  insurance_expires_on: string | null;
  registration_expires_on: string | null;
  inspection_expires_on: string | null;
  notes: string | null;
  archived_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  mine: boolean;
  odometer_km: string | null;
  due: VehicleDueItem[];
}

export interface VehicleInput {
  ownership?: VehicleOwnership;
  registration: string;
  vin?: string | null;
  nickname?: string | null;
  make?: string | null;
  model?: string | null;
  year?: number | null;
  vehicle_type?: VehicleType;
  country: VehicleCountry;
  currency?: string | null;
  fuel_type: VehicleFuelType;
  tank_litres?: string | null;
  acquisition_date?: string | null;
  purchase_price?: string | null;
  residual_value?: string | null;
  useful_life_months?: number | null;
  insurance_expires_on?: string | null;
  registration_expires_on?: string | null;
  inspection_expires_on?: string | null;
  notes?: string | null;
  odometer_km?: string | null;
}

export type VehiclePatch = Partial<Omit<VehicleInput, 'ownership' | 'odometer_km'>> & { version: number };

interface VehicleChild {
  id: string;
  owner_user_id: string;
  vehicle_id: string;
  visibility: VehicleOwnership;
  vehicle_owner_id: string;
}

export interface VehicleOdometerReading extends VehicleChild {
  reading_km: string;
  read_at: string;
  source: 'manual' | 'fuel' | 'trip_start' | 'trip_end' | 'service' | 'device' | 'correction';
  source_id: string | null;
  correction: boolean;
  reason: string | null;
  created_at: string;
}

interface VehicleRecordBase extends VehicleChild {
  version: number;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  mine: boolean;
}

export interface VehicleFuelEntry extends VehicleRecordBase {
  filled_at: string;
  station: string | null;
  fuel_type: VehicleFuelType;
  litres: string;
  price_per_litre: string | null;
  /** The pump's figure: a detail. The cost is the linked payment. */
  total: string | null;
  currency: string;
  odometer_km: string | null;
  full_tank: boolean;
  payment_method: VehiclePaymentMethod | null;
  payment: VehiclePayment | null;
  /** A business allocation that fits this record, on lists of a business vehicle's records (older servers send none). */
  allocation_proposal?: VehicleAllocationProposal | null;
  document_id: string | null;
  notes: string | null;
}

export interface VehicleTrip extends VehicleRecordBase {
  status: 'open' | 'closed';
  started_at: string;
  ended_at: string | null;
  start_odometer_km: string | null;
  end_odometer_km: string | null;
  distance_km: string | null;
  distance_basis: 'typed' | 'previous_reading' | null;
  purpose: VehicleTripPurpose;
  purpose_note: string | null;
  project_id: string | null;
  origin: string | null;
  destination: string | null;
  notes: string | null;
  source: 'web' | 'device';
  device_id: string | null;
}

export interface VehicleServiceRecord extends VehicleRecordBase {
  kind: VehicleServiceKind;
  service_on: string;
  odometer_km: string | null;
  workshop: string | null;
  description: string | null;
  reference: string | null;
  next_due_on: string | null;
  next_due_km: string | null;
  warranty_until: string | null;
  trip_id: string | null;
  /** The receipt's figure: a detail. The cost is the linked payment. */
  receipt_total: string | null;
  currency: string;
  payment_method: VehiclePaymentMethod | null;
  payment: VehiclePayment | null;
  /** A business allocation that fits this record, on lists of a business vehicle's records (older servers send none). */
  allocation_proposal?: VehicleAllocationProposal | null;
  document_id: string | null;
}

export interface VehicleFuelInput {
  filled_at: string;
  station?: string | null;
  fuel_type?: VehicleFuelType | null;
  litres: string;
  price_per_litre?: string | null;
  total?: string | null;
  currency?: string | null;
  odometer_km?: string | null;
  full_tank?: boolean;
  payment_method?: VehiclePaymentMethod | null;
  notes?: string | null;
}

export interface VehicleTripInput {
  started_at: string;
  ended_at?: string | null;
  start_odometer_km?: string | null;
  end_odometer_km?: string | null;
  purpose?: VehicleTripPurpose;
  purpose_note?: string | null;
  project_id?: string | null;
  origin?: string | null;
  destination?: string | null;
  notes?: string | null;
}

export interface VehicleServiceInput {
  kind: VehicleServiceKind;
  service_on: string;
  odometer_km?: string | null;
  workshop?: string | null;
  description?: string | null;
  reference?: string | null;
  next_due_on?: string | null;
  next_due_km?: string | null;
  warranty_until?: string | null;
  trip_id?: string | null;
  receipt_total?: string | null;
  currency?: string | null;
  payment_method?: VehiclePaymentMethod | null;
}

export interface VehiclePage<T> { items: T[]; total: number; limit: number; offset: number }

export interface VehicleMoney { currency: string; amount: string }

export interface VehicleReport {
  vehicle_id: string;
  start: string | null;
  end: string | null;
  odometer_km: string | null;
  distance_km: string;
  /** Per currency, never added across currencies. */
  spend: VehicleMoney[];
  cost_per_km: VehicleMoney[];
  monthly: { month: string; currency: string; amount: string }[];
  by_category: { currency: string; category: string; amount: string }[];
  fuel_litres: string;
  /** Kilometres and litres per month in the period; a month's distance counts from the reading before it. */
  usage: VehicleUsageMonth[];
  fuel_price_trend: { month: string; currency: string; fuel_type: VehicleFuelType; price_per_litre: string }[];
  consumption: {
    average_per_100: string | null;
    segments: { start_id: string; end_id: string; km: string; litres: string; per_100: string; ended_on: string }[];
  };
  anomalies: { kind: 'over_tank' | 'high_consumption' | 'price_outlier' | 'payment_changed' | 'payment_undone' | 'payment_mismatch';
    record_id: string; detail: Record<string, string> }[];
  unlinked_records: number;
  ownership_cost: {
    operating: VehicleMoney[];
    depreciation: { currency: string; purchase_price: string; residual_value: string; monthly: string; months: number;
      accumulated: string; book_value: string } | null;
    capital_paid: VehicleMoney | null;
  };
}

export interface VehicleOverview {
  start: string | null;
  end: string | null;
  vehicles: number;
  spend: VehicleMoney[];
  monthly: { month: string; currency: string; amount: string }[];
  by_vehicle: { vehicle_id: string; registration: string; currency: string; amount: string }[];
  due: VehicleDueItem[];
  unlinked_records: number;
  /** Kilometres and litres per month across the vehicles (quantities, never money). */
  usage: VehicleUsageMonth[];
  /** Each vehicle's kilometres, litres and average L/100 km (full-to-full chains) in the period. */
  by_vehicle_usage: { vehicle_id: string; distance_km: string; fuel_litres: string; average_per_100: string | null }[];
}

export interface VehicleUsageMonth { month: string; distance_km: string; fuel_litres: string }

export interface VehiclePreferences {
  owner_user_id: string;
  trip_places: boolean;
  default_vehicle_id: string | null;
  reminder_days: number;
  reminders: boolean;
  version: number;
  updated_at: string | null;
}

/** One of the caller's own Finance payments, offered in the payment picker. */
export interface VehiclePaymentCandidate {
  canonical_id: string;
  posted_on: string;
  merchant: string;
  category: string | null;
  amount: string;
  currency: string;
  remaining: string;
  linked_to_vehicle: boolean;
}

/** GET /api/v1/vehicles/allocations: this business's allocations the caller made that no vehicle record counts yet. */
export interface VehicleAllocationCandidate {
  allocation_id: string;
  posted_on: string;
  amount: string;
  currency: string;
  category: string;
  memo: string | null;
  source_ref: string;
}

/** A business allocation proposed as a business vehicle record's cost (docs/vehicles.md#business-allocations): the person confirms it. */
export interface VehicleAllocationProposal extends VehicleAllocationCandidate {
  /** Days between the allocation's payment and the record. */
  days_apart: number;
}

export interface VehicleLinkInput { canonical_id: string; amount?: string | null; /** Needed when the payment has more than one currency. */ currency?: string | null }
export interface VehicleCashInput { account_id: string; amount: string; posted_on: string }
export interface VehiclePaymentResult { payment: VehiclePayment; replayed: boolean }

/** POST /api/v1/vehicles/device/sessions from an iPhone Shortcut (scope `vehicle`). */
export interface VehicleDrivingEvent {
  action: 'start' | 'end' | 'test';
  vehicle?: string | null;
  occurred_at?: string | null;
  idempotency_key?: string | null;
  /** What the person typed: the phone never reads the car's odometer. */
  odometer_km?: string | null;
  purpose?: VehicleTripPurpose | null;
  fuel_litres?: string | null;
  fuel_total?: string | null;
  fuel_full?: boolean;
  parking_total?: string | null;
}

export interface VehicleRefusal { detail: string; code: string; [extra: string]: unknown }

// ---- Running a client fleet (docs/vehicles.md#fleet) ------------------------------------------------------------

export type FleetCategory = 'fuel' | 'service' | 'tyres' | 'other';
export type FleetMoney = Record<FleetCategory, string> & { currency: string; total: string };

/** One vehicle's month in GET /api/v1/vehicles/fleet/costs: kilometres, litres and its costs per currency. */
export interface FleetVehicleRow {
  vehicle_id: string;
  registration: string;
  nickname: string | null;
  make: string | null;
  model: string | null;
  currency: string;
  archived: boolean;
  distance_km: string;
  previous_distance_km: string;
  fuel_litres: string;
  litres_per_100km: string | null;
  costs: (FleetMoney & { previous_total: string; cost_per_km: string | null })[];
}

/** A month of the fleet, per currency: costs by category, kilometres of the vehicles kept in that currency, cost per km. */
export interface FleetMonth extends FleetMoney {
  month: string;
  distance_km: string;
  cost_per_km: string | null;
}

export interface FleetTotal extends FleetMonth {
  previous_total: string;
  /** From linked payments and business allocations. */
  linked: string;
  /** From the figures on the records (pump and receipt totals), as an imported client fleet carries them. */
  recorded: string;
}

export interface FleetWorst { vehicle_id: string; registration: string; nickname: string | null; currency: string; cost_per_km: string | null; total: string; distance_km: string }
export interface FleetRenewal { vehicle_id: string; registration: string; nickname: string | null; kind: 'insurance' | 'registration' | 'inspection'; due_on: string; days_left: number }

/** The Tyre planning snapshot beside the figures (read only, never added to them). */
export interface FleetTyres {
  status: 'ok' | 'denied' | 'not_connected' | 'not_synced';
  matched: number;
  currency?: string;
  synced_at?: string | null;
  tyre_cost?: string;
  tyres_due?: number;
  vehicles: { vehicle_id: string; registration: string; tyre_vehicle: string; tyre_cost: string; tyres_due: number; next_removal: string | null }[];
}

export interface FleetCosts {
  month: string;
  start: string;
  end: string;
  /** The twelve months to `month`, oldest first. */
  months: string[];
  vehicles: number;
  distance_km: string;
  fuel_litres: string;
  totals: FleetTotal[];
  trend: FleetMonth[];
  rows: FleetVehicleRow[];
  worst: FleetWorst[];
  renewals: FleetRenewal[];
  tyres: FleetTyres;
  generated_at: string;
}

/** POST /api/v1/vehicles/fleet/report: a PDF or Excel file, made for the request and never stored or sent. */
export interface FleetReportInput {
  month: string;
  language: 'en' | 'ar';
  format: 'pdf' | 'xlsx';
  company_name: string;
  notes?: string;
}

export type FleetImportKind = 'vehicles' | 'fuel' | 'service' | 'odometer';
export type FleetRowStatus = 'new' | 'exists' | 'duplicate_in_file' | 'imported' | 'invalid';
export type FleetRowAction = 'create' | 'update' | 'skip';
export type FleetIssueCode = 'required' | 'number' | 'date' | 'future' | 'range' | 'choice' | 'unknown_vehicle' | 'ambiguous_vehicle' | 'archived_vehicle'
  | 'odometer' | 'registration' | 'vin' | 'currency' | 'other';

export interface FleetImportFile {
  kind: FleetImportKind;
  format: 'csv' | 'xlsx';
  csv?: string;
  file_base64?: string;
  file_name?: string;
  /** Field to column; omitted: the server suggests one from the headers. */
  mapping?: Record<string, string> | null;
  country?: 'SA' | 'AE';
  date_order?: 'dmy' | 'mdy';
}

export interface FleetImportRow {
  row: number;
  registration: string;
  summary: Record<string, string | null>;
  status: FleetRowStatus;
  action: FleetRowAction;
  actions: FleetRowAction[];
  vehicle_id: string | null;
  duplicate_of: number | null;
  issues: { code: FleetIssueCode; field: string | null; value: string; text: string }[];
}

export interface FleetImportPreview {
  kind: FleetImportKind;
  columns: string[];
  fields: { field: string; required: boolean }[];
  mapping: Record<string, string>;
  suggested: boolean;
  rows: FleetImportRow[];
  counts: { rows: number; new: number; exists: number; duplicate_in_file: number; imported: number; invalid: number };
  fingerprint: string;
}

export interface FleetImportApply extends FleetImportFile {
  mapping: Record<string, string>;
  fingerprint: string;
  decisions: { row: number; action: FleetRowAction }[];
}

export interface FleetImportResult { kind: FleetImportKind; rows: number; created: number; updated: number; skipped: number; readings: number }
