// The document vault API (`/api/v1/documents`, docs/documents.md#api). Field names match the API's
// JSON. Dates are YYYY-MM-DD strings, times ISO 8601. Money values are decimal strings.

export type DocumentAccessClass = 'private' | 'workspace';
export type DocumentStatus = 'active' | 'deleted';
export type DocumentExpiryState = 'none' | 'valid' | 'soon' | 'expired';
export type DocumentLinkTarget = 'vehicle' | 'person' | 'business' | 'purchase' | 'finance_transaction';
export type DocumentExtractionKind = 'receipt' | 'dates';
export type DocumentExtractionStatus = 'queued' | 'pending' | 'confirmed' | 'rejected' | 'failed';

/** The built-in categories; a workspace adds its own with `vcat_...` ids and a label. */
export const DOCUMENT_CATEGORIES = [
  'passport', 'national_id', 'residency', 'visa', 'contract', 'salary_certificate', 'vehicle_registration',
  'insurance', 'bank_statement', 'receipt', 'invoice', 'purchase_order', 'agreement', 'warranty',
  'maintenance_invoice', 'certificate', 'other',
] as const;
export type BuiltInDocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

export interface DocumentFileSummary {
  name: string;
  content_type: string;
  size_bytes: number;
  version: number;
  kind: string | null;
  previewable: boolean;
  scan_status: 'clean' | 'infected' | null;
}

/** Confirmed receipt fields: data on the document only, never a finance posting. */
export interface DocumentReceipt {
  merchant: string | null;
  date: string | null;
  total: string | null;
  currency: string | null;
  tax: string | null;
  reference: string | null;
  items: { description: string; quantity: string; amount: string }[];
  confirmed_by: string;
  confirmed_at: string;
  extraction_id: string;
}

export interface VaultDocument {
  id: string;
  title: string;
  category: string;
  access_class: DocumentAccessClass;
  status: DocumentStatus;
  issue_date: string | null;
  expiry_date: string | null;
  expiry_state: DocumentExpiryState;
  days_left: number | null;
  issuer: string | null;
  country: string | null;
  tags: string[];
  description: string;
  /** The last four characters only ("•••• 1234"); the full number is never listed. */
  number_masked: string | null;
  assistant_may_read: boolean;
  reminder_days: number | null;
  retention_until: string | null;
  receipt: DocumentReceipt | null;
  source: 'web' | 'device' | 'api';
  owner_user_id: string;
  is_owner: boolean;
  can_edit: boolean;
  row_version: number;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
  deleted_at: string | null;
  purge_after: string | null;
  file: DocumentFileSummary | null;
}

export interface DocumentExtractionField { value: string; confidence: number; source: string | null }

export interface DocumentExtraction {
  id: string;
  document_id: string;
  kind: DocumentExtractionKind;
  status: DocumentExtractionStatus;
  engine: string;
  languages: string;
  fields: Partial<Record<'merchant' | 'date' | 'total' | 'currency' | 'tax' | 'reference' | 'issue_date' | 'expiry_date', DocumentExtractionField>>;
  items: { description: string; quantity: string; amount: string }[];
  /** e.g. `instructions_in_text`, `items_unreliable`, `hijri_date_seen`, `no_text`. */
  warnings: string[];
  error: string | null;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface DocumentAccessGrant { document_id: string; user_id: string; granted_by: string; granted_at: string }
export interface DocumentLink { id: string; document_id: string; target_type: DocumentLinkTarget; target_id: string; label: string; status: 'proposed' | 'confirmed'; created_by: string; created_at: string }
export interface DocumentActivity { id: string; document_id: string; actor: string; action: string; detail: Record<string, string | number | boolean>; created_at: string }

export interface VaultDocumentDetail extends VaultDocument {
  shared_with: DocumentAccessGrant[];
  links: DocumentLink[];
  extractions: DocumentExtraction[];
  /** The access log: for people who may change the document only (empty otherwise). */
  activity: DocumentActivity[];
  can_share: boolean;
  can_extract: boolean;
}

export interface VaultDocumentPage { items: VaultDocument[]; next_cursor: string | null }

export interface VaultSettings {
  reminder_days: number;
  trash_days: number;
  ocr_receipts: boolean;
  ocr_identity_dates: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface VaultCategory { id: string; label: string | null; built_in: boolean; reads: DocumentExtractionKind | null; identity: boolean }

export interface VaultMeta {
  summary: { active: number; private: number; expiring: number; expired: number; review: number; trash: number };
  categories: VaultCategory[];
  settings: VaultSettings;
  ocr_connected: boolean;
  can_write: boolean;
  can_manage: boolean;
  max_upload_bytes: number;
  accepted: string;
  accepted_extensions: string[];
  link_types: DocumentLinkTarget[];
  reminder_window_days: number;
}

export interface VaultReviewItem { extraction: DocumentExtraction; document: VaultDocument }

export interface VaultVersion {
  id: string;
  version: number;
  content_type: string;
  size_bytes: number;
  created_by: string;
  created_at: string;
  replaced_by: string;
  replaced_at: string;
  downloadable: boolean;
}
export interface VaultVersionPage { current_version: number; current_size_bytes: number; items: VaultVersion[]; total: number }

export interface VaultDevice {
  id: string;
  name: string;
  hint: string;
  scopes: string[];
  status: 'active' | 'revoked';
  last_seen_at: string | null;
  last_error: string | null;
  upload_count: number;
  created_at: string;
}
/** The token is shown once; ANUM keeps only its hash. */
export interface VaultDeviceCreated { device: VaultDevice; token: string; upload_url: string }

export interface VaultDocumentPatch {
  row_version: number;
  title?: string;
  category?: string;
  access_class?: DocumentAccessClass;
  issue_date?: string | null;
  expiry_date?: string | null;
  issuer?: string | null;
  country?: string | null;
  tags?: string[];
  description?: string;
  /** Stored encrypted; empty clears it. */
  number?: string;
  reminder_days?: number | null;
  retention_until?: string | null;
  assistant_may_read?: boolean;
}
