// Business tax (docs/business-tax.md), mirror of services/api/anum_api/business/api_tax.py. VAT settings and the rate
// table per business, tax codes on invoice lines and bills, the VAT return read from the journal (KSA, UAE VAT201 or a
// summary), and ZATCA e-invoicing readiness behind a switch that is off by default. Nothing here files a return or
// calls ZATCA: the API refuses submission until the business is onboarded, which this release never does.

export type TaxJurisdiction = 'SA' | 'AE' | 'OTHER';
export type TaxCode = 'standard' | 'zero_rated' | 'export' | 'exempt' | 'out_of_scope' | 'reverse_charge' | 'import_customs';
export type SalesTaxCode = 'standard' | 'zero_rated' | 'export' | 'exempt' | 'out_of_scope';
export type TaxFrequency = 'monthly' | 'quarterly';

export const TAX_JURISDICTIONS: TaxJurisdiction[] = ['SA', 'AE', 'OTHER'];
export const TAX_CODES: TaxCode[] = ['standard', 'zero_rated', 'export', 'exempt', 'out_of_scope', 'reverse_charge', 'import_customs'];
export const SALES_TAX_CODES: SalesTaxCode[] = ['standard', 'zero_rated', 'export', 'exempt', 'out_of_scope'];
export const TAX_FREQUENCIES: TaxFrequency[] = ['quarterly', 'monthly'];

export interface TaxSettings {
  id: string;
  jurisdiction: TaxJurisdiction;
  registered: boolean;
  registration_number: string;
  registration_date: string | null;
  filing_frequency: TaxFrequency;
  period_start_month: number;
  zatca_enabled: boolean;
  zatca_onboarding: 'not_started';
  seller_crn: string;
  seller_street: string;
  seller_building: string;
  seller_district: string;
  seller_city: string;
  seller_postal_code: string;
  version: number;
  updated_by: string;
  updated_at: string;
  created_at: string;
}

export interface TaxSettingsInput {
  jurisdiction: TaxJurisdiction;
  registered: boolean;
  registration_number: string;
  registration_date: string | null;
  filing_frequency: TaxFrequency;
  period_start_month: number;
  zatca_enabled: boolean;
  seller_crn: string;
  seller_street: string;
  seller_building: string;
  seller_district: string;
  seller_city: string;
  seller_postal_code: string;
  version: number | null;
}

export interface TaxRate {
  id: string;
  jurisdiction: TaxJurisdiction;
  code: TaxCode;
  rate_pct: string;
  effective_from: string;
  effective_to: string | null;
  note: string;
}

export interface TaxRateInput {
  code: TaxCode;
  rate_pct: string;
  effective_from: string;
  effective_to: string | null;
  note: string;
}

export interface TaxPeriod {
  from: string;
  to: string;
}

export interface TaxNotice {
  en: string;
  ar: string;
}

export interface TaxOverview {
  settings: TaxSettings | null;
  rates: TaxRate[];
  can_edit: boolean;
  jurisdictions: TaxJurisdiction[];
  codes: TaxCode[];
  sales_codes: SalesTaxCode[];
  frequencies: TaxFrequency[];
  periods: TaxPeriod[];
  notice: TaxNotice;
}

export type TaxBoxSection = 'sales' | 'sales_total' | 'purchases' | 'purchases_total' | 'net';

export interface TaxBox {
  box: string;
  section: TaxBoxSection;
  label: string;
  amount: string;
  vat: string;
  adjustment_amount: string;
  adjustment_vat: string;
  documents: number;
}

export interface TaxInfoRow {
  key: string;
  label: string;
  amount: string;
  vat: string;
  documents: number;
}

export type TaxWarningCode =
  | 'not_set_up' | 'not_registered' | 'unposted' | 'missing_rate' | 'draft_bills' | 'inferred' | 'unknown' | 'member_paid' | 'manual_vat'
  | 'truncated' | 'base_currency' | 'period_open';

export interface TaxWarning {
  code: TaxWarningCode;
  text: string;
  count?: number;
  currencies?: string[];
}

export interface TaxReturn {
  period: TaxPeriod;
  jurisdiction: TaxJurisdiction;
  base_currency: string;
  registration_number: string;
  closed: boolean;
  closed_through: string | null;
  complete: boolean;
  boxes: TaxBox[];
  info: TaxInfoRow[];
  notes: string[];
  warnings: TaxWarning[];
  journal: {
    output_vat_movement: string;
    input_vat_movement: string;
    output_vat_in_return: string;
    input_vat_in_return: string;
    manual_output: string;
    manual_input: string;
    ties: boolean;
  };
}

export interface TaxReturnDocument {
  entry_id: string;
  source_kind: 'invoice' | 'credit_note' | 'bill';
  source_id: string | null;
  description: string;
  entry_date: string;
  event_date: string;
  box: string;
  tax_code: TaxCode | 'unknown';
  inferred: boolean;
  adjustment: boolean;
  net: string;
  vat: string;
}

export interface ZatcaDocument {
  id: string;
  source_kind: 'invoice' | 'credit_note';
  source_id: string;
  document_number: string;
  type_code: '388' | '381' | '383';
  subtype: '0100000' | '0200000';
  counter: number;
  uuid: string;
  invoice_hash: string;
  previous_hash: string;
  issue_date: string;
  qr: string;
  missing: string[];
  status: 'generated';
  created_by: string;
  created_at: string;
  replayed?: boolean;
}

export interface ZatcaInvoiceRow {
  invoice_id: string;
  number: string | null;
  issue_date: string | null;
  currency: string;
  total: string;
  tax: string;
  buyer: string;
  kind: 'standard' | 'simplified';
  uncoded_lines: number;
  document: ZatcaDocument | null;
}

export interface ZatcaPanel {
  enabled: boolean;
  onboarding: 'not_started';
  missing: string[];
  notice: TaxNotice;
  onboarding_required: string;
  can_edit: boolean;
  counter: number;
  last_hash: string;
  generated: number;
  invoices: { items: ZatcaInvoiceRow[]; total: number; limit: number; offset: number };
}
