// The document vault API (`/api/v1/documents`, docs/documents.md#api). Field names match the API's
// JSON. Dates are YYYY-MM-DD strings, times ISO 8601. Money values are decimal strings.

export type DocumentAccessClass = 'private' | 'workspace';
export type DocumentStatus = 'active' | 'deleted';
export type DocumentExpiryState = 'none' | 'valid' | 'soon' | 'expired';
export type DocumentLinkTarget = 'vehicle' | 'person' | 'business' | 'purchase' | 'finance_transaction'
  // Evidence on a business's books, attached from Business, Books (docs/business-books.md#supporting-documents-and-evidence).
  | 'business_bill' | 'business_invoice' | 'business_adjustment' | 'business_journal';
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

/** A list row: the document, its file, how many links (and the first) and whether a scan waits for review. */
export interface VaultDocumentRow extends VaultDocument {
  links_count: number;
  first_link: DocumentLink | null;
  review_pending: boolean;
}

export interface VaultDocumentPage { items: VaultDocumentRow[]; next_cursor: string | null }

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
  summary: { active: number; private: number; expiring: number; expired: number; review: number; trash: number; linked: number };
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

/** One record a confirmed receipt may belong to (docs/documents.md#receipt-matches): a payment of the caller's own
 * Finance ledger (`target_id`: its canonical id) or a purchase with no receipt yet. A proposal only. */
export interface VaultReceiptProposal {
  kind: 'payment' | 'purchase';
  target_id: string;
  on: string;
  amount: string;
  currency: string;
  /** The payment's merchant words or the purchase's shop. */
  label: string;
  /** Whether the receipt's merchant and this record share a meaningful word (null when either names none). */
  merchant_agrees: boolean | null;
  confidence: string;
}

/** `GET /api/v1/documents/{id}/matches`: what a receipt may be the receipt of. */
export interface VaultReceiptMatches {
  document_id: string;
  receipt: { total: string; currency: string; on: string; merchant: string } | null;
  /** Why nothing is proposed: not a receipt category, or its scanned values are not confirmed yet. */
  reason: 'not_a_receipt' | 'not_confirmed' | null;
  can_edit: boolean;
  payments: VaultReceiptProposal[];
  payments_unavailable: 'finance_access' | null;
  purchases: VaultReceiptProposal[];
  purchases_unavailable: 'purchase_access' | 'not_installed' | null;
  /** Payments (canonical ids) and purchases the document is already linked to. */
  linked_payments: string[];
  linked_purchases: string[];
  /** Matches the person said are wrong: not proposed again until restored (older servers send none). */
  dismissed?: VaultDismissedMatch[];
}

export interface VaultDismissedMatch { kind: 'payment' | 'purchase'; target_id: string; label: string; dismissed_by: string; dismissed_at: string }

/** `GET /api/v1/documents/{id}/vehicle-proposals`: the vehicles an insurance or registration document may belong to (docs/documents.md#vehicle-papers). */
export interface VaultVehicleProposals {
  document_id: string;
  category: string;
  /** The vehicle date the document sets. */
  field: 'insurance_expires_on' | 'registration_expires_on' | null;
  expiry_date: string | null;
  reason: 'not_a_vehicle_paper' | 'no_expiry' | 'in_trash' | null;
  can_edit: boolean;
  vehicles: VaultVehicleProposal[];
  vehicles_unavailable: 'vehicle_access' | null;
  linked_vehicles: string[];
  /** The first renewal reminder's day (the person's reminder days before the expiry). */
  reminder_from?: string;
  reminder_days?: number;
}

export interface VaultVehicleProposal {
  vehicle_id: string; label: string; registration: string; ownership: string; current_expiry: string | null;
  would_change: boolean; matches_registration: boolean; linked: boolean;
}
