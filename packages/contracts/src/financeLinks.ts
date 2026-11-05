// Finance links and business allocations (docs/finance-links.md). Amounts are decimal strings.

export type FinanceLinkDimension = 'vehicle' | 'purchase' | 'document' | 'business' | 'trip' | 'project';
export type FinanceLinkRole = 'payment' | 'refund' | 'deposit' | 'evidence';
export type FinanceLinkStatus = 'active' | 'needs_review' | 'void';
export type BusinessAllocationTreatment = 'owner_contribution' | 'reimbursable';

export interface FinanceLink {
  id: string;
  canonical_id: string;
  dimension: FinanceLinkDimension;
  role: FinanceLinkRole;
  target_type: string;
  target_id: string;
  target_workspace_id: string | null;
  amount: string | null;
  currency: string | null;
  status: FinanceLinkStatus;
  status_reason: string | null;
  source_event_ids: string[];
  document_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  voided_at: string | null;
  version: number;
}

export interface FinanceLinkPage { items: FinanceLink[]; total: number; limit: number; offset: number }
export interface FinanceLinkResult { link: FinanceLink; replayed: boolean }

/** POST /api/v1/finance/links (business allocations use FinanceAllocationInput). */
export interface FinanceLinkInput {
  canonical_id: string;
  dimension: Exclude<FinanceLinkDimension, 'business'>;
  role?: FinanceLinkRole;
  target_type: string;
  target_id: string;
  amount?: string | null;
  currency?: string | null;
  document_id?: string | null;
}

export interface FinanceTransactionLinks {
  canonical_id: string;
  live: boolean;
  amounts: Record<string, string>;
  remaining: Record<FinanceLinkDimension, Record<string, string>>;
  links: FinanceLink[];
}

/** POST /api/v1/finance/allocations */
export interface FinanceAllocationInput {
  canonical_id: string;
  workspace_id: string;
  amount: string;
  currency: string;
  category?: string | null;
  project_id?: string | null;
  memo?: string | null;
  treatment?: BusinessAllocationTreatment;
}

/** One row a business workspace's members see (GET /api/v1/business/ledger-allocations). */
export interface BusinessLedgerAllocation {
  id: string;
  source_link_id: string;
  source_ref: string;
  allocated_by: string;
  posted_on: string;
  amount: string;
  currency: string;
  category: string;
  project_id: string | null;
  memo: string | null;
  treatment: BusinessAllocationTreatment;
  status: 'active' | 'needs_review';
  created_at: string;
}

export interface BusinessLedgerAllocationPage {
  items: BusinessLedgerAllocation[];
  total: number;
  limit: number;
  offset: number;
  totals: Record<string, string>;
}

/** A refusal: plain words in `detail`, a stable `code`, and figures when they help (`remaining`). */
export interface FinanceLinkRefusal { detail: string; code: string; remaining?: string; currency?: string; currencies?: string[] }
