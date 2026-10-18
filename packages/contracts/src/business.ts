// Wire shapes of /api/v1/business (services/api/anum_api/business, docs/business.md), snake_case as
// the API sends them. Money is a number with two decimals in the record's currency. Values marked as
// assumptions (an opportunity's value and probability, a version's cost) are the owner's estimates.

export type BusinessCurrency = 'SAR' | 'AED' | 'USD';
export type ProductKind = 'product' | 'service_package';
export type ProductMaturity = 'idea' | 'prototype' | 'pilot' | 'live' | 'retired';
export type PriceType = 'fixed' | 'from' | 'range' | 'hourly' | 'quote';
export type OfferStatus = 'draft' | 'active' | 'retired';
export type RecordSource = 'manual' | 'csv_import' | 'research' | 'referral' | 'inbound';
export type ConsentBasis = 'unknown' | 'consent_given' | 'inbound_inquiry' | 'existing_relationship' | 'referral' | 'public_business_channel';
export type LeadState = 'discovered' | 'researched' | 'qualified' | 'contacted' | 'replied' | 'meeting' | 'offered' | 'won' | 'lost' | 'suppressed';
export type Criterion = 'industry_fit' | 'problem_evidence' | 'reachable_channel' | 'project_scale' | 'urgency_confirmed' | 'authorized_buyer';
export type Confidence = 'low' | 'medium' | 'high';
export type InferenceStatus = 'proposed' | 'accepted' | 'rejected';
export type OpportunityStage = 'discovery' | 'qualified' | 'demo' | 'quote' | 'negotiation' | 'won' | 'lost';
export type ProposalStatus = 'draft' | 'pending_approval' | 'accepted' | 'withdrawn';
export type ProjectStatus = 'planned' | 'active' | 'on_hold' | 'delivered' | 'closed';
export type MilestoneStatus = 'planned' | 'in_progress' | 'submitted' | 'accepted' | 'blocked';
export type DraftChannel = 'email' | 'whatsapp' | 'linkedin' | 'call_script';
export type DraftStatus = 'draft' | 'approved' | 'discarded';
export type ResearchStatus = 'succeeded' | 'partial' | 'failed' | 'blocked';
export type ApprovalState = 'none' | 'pending' | 'approved' | 'rejected' | 'expired';

export interface BusinessPage<T> { items: T[]; total: number; limit: number; offset: number }

export interface BusinessSettings { research_runs_per_day: number; research_searches_per_day: number; updated_by: string | null; updated_at: string | null }

export interface BusinessSetup {
  permissions: { read: boolean; write: boolean; manage: boolean; decide: boolean };
  /** Always false in this release: there is no send path. */
  sending_enabled: boolean;
  web_search: boolean;
  model_connected: boolean;
  settings: BusinessSettings;
  usage: { runs_today: number; searches_today: number };
  criteria: Criterion[];
  conversation_criteria: Criterion[];
  transitions: Record<LeadState, LeadState[]>;
  contactable_bases: ConsentBasis[];
}

interface Scoped { id: string; tenant_id: string; workspace_id: string; created_at: string; updated_at: string }

export interface CostAssumptions { labor_hours: number; hourly_cost: number; external_costs: number; contingency_pct: number }

export interface BusinessOffer extends Scoped {
  product_id: string; version: number; status: OfferStatus; currency: BusinessCurrency; price_type: PriceType;
  price_min: number | null; price_max: number | null; deliverables: string[]; limitations: string[]; cost: CostAssumptions; notes: string;
  activated_at: string | null; created_by: string;
  estimated_cost: number; margin_at_price: number | null; margin_negative: boolean;
}

export interface BusinessProduct extends Scoped {
  kind: ProductKind; name: string; name_ar: string; summary: string; maturity: ProductMaturity; integrations: string[]; created_by: string;
  active_offer: BusinessOffer | null; versions: number;
}
export interface BusinessProductDetail extends BusinessProduct { offers: BusinessOffer[] }

export interface BusinessAccount extends Scoped {
  name: string; name_key: string; website: string | null; domain: string | null; industry: string; country: string; city: string; notes: string;
  source: RecordSource; owner_id: string; do_not_contact: boolean; dnc_reason: string; created_by: string;
}
export interface BusinessAccountRow extends BusinessAccount { contacts: number; open_leads: number }
export interface BusinessAccountDetail extends BusinessAccount { contacts: BusinessContact[]; leads: BusinessLead[] }

export interface BusinessContact extends Scoped {
  account_id: string; name: string; role_title: string; email: string | null; phone: string | null; profile_url: string | null;
  consent_basis: ConsentBasis; consent_note: string; consent_recorded_at: string | null; do_not_contact: boolean; dnc_reason: string; source: RecordSource;
  created_by: string;
  /** Masked addresses of this person on the tenant's contact opt-out list. */
  opted_out: string[];
  block_reason: string | null;
  contactable: boolean;
}

export interface CriterionScore { score: number | null; reason: string; evidence_ids: string[]; scored_by: string | null; scored_at: string | null }

export interface BusinessLead extends Scoped {
  account_id: string; contact_id: string | null; product_id: string | null; title: string; state: LeadState; owner_id: string; source: RecordSource;
  score: Partial<Record<Criterion, CriterionScore>>; score_total: number; score_known: number; next_step: string; lost_reason: string; suppressed_reason: string;
  last_verified_at: string | null; researched_at: string | null; qualified_at: string | null; contacted_at: string | null; replied_at: string | null;
  meeting_at: string | null; offered_at: string | null; won_at: string | null; lost_at: string | null; suppressed_at: string | null; created_by: string;
  account_name: string; account_domain: string | null; contact_name: string | null; product_name: string | null; block_reason: string | null;
}

export interface BusinessEvidence extends Scoped {
  lead_id: string; account_id: string; url: string; url_key: string; title: string; published_on: string | null; excerpt: string;
  confidence: Confidence; source: RecordSource; research_run_id: string | null; created_by: string;
}

export interface BusinessInference extends Scoped {
  lead_id: string; statement: string; criterion: Criterion | null; suggested_score: number | null; confidence: Confidence; basis_evidence_ids: string[];
  generator: 'human' | 'template' | 'model'; status: InferenceStatus; reviewed_by: string | null; created_by: string;
}

export interface BusinessActivity {
  id: string; lead_id: string | null; account_id: string | null; opportunity_id: string | null; proposal_id: string | null; project_id: string | null;
  kind: string; summary: string; metadata: Record<string, unknown>; actor: string; idempotency_key: string | null; created_at: string;
}

export interface BusinessDraft extends Scoped {
  lead_id: string; contact_id: string | null; channel: DraftChannel; language: 'en' | 'ar'; subject: string; body: string; status: DraftStatus;
  generator: 'template' | 'model' | 'human'; untrusted_source: boolean; issues: string[]; approved_hash: string | null; approved_by: string | null;
  approved_at: string | null; created_by: string;
}

export interface ResearchStep { step: 'find' | 'profile' | 'score' | 'draft'; status: 'done' | 'skipped' | 'failed' | 'blocked'; summary: string }
export interface BusinessResearchRun extends Scoped {
  lead_id: string; status: ResearchStatus; queries: number; results: number; evidence_added: number; inferences_added: number; steps: ResearchStep[];
  error: string; created_by: string;
}

export interface BusinessLeadDetail {
  lead: BusinessLead;
  account: BusinessAccount;
  contact: BusinessContact | null;
  product: BusinessProduct | null;
  evidence: BusinessEvidence[];
  inferences: BusinessInference[];
  activities: BusinessActivity[];
  drafts: BusinessDraft[];
  research_runs: BusinessResearchRun[];
  opportunity_id: string | null;
  qualification_gaps: string[];
  allowed_transitions: LeadState[];
  consent_gap: string | null;
}

export interface BusinessDuplicate { kind: 'account' | 'contact' | 'lead'; id: string; label: string; reason: string }

export interface ImportRow { row: number; company: string; status: 'created' | 'duplicate' | 'suppressed' | 'invalid'; message: string; lead_id: string | null }
export interface ImportResult { dry_run: boolean; columns: string[]; rows: ImportRow[]; created: number; duplicates: number; suppressed: number; invalid: number }

export interface BusinessOpportunity extends Scoped {
  lead_id: string | null; account_id: string; product_id: string | null; name: string; stage: OpportunityStage; currency: BusinessCurrency;
  /** The owner's assumption, not a forecast. */
  value_amount: number | null;
  probability_pct: number | null;
  assumption_note: string; next_step: string; expected_decision_on: string | null; last_interaction_at: string | null; lost_reason: string;
  won_at: string | null; lost_at: string | null; owner_id: string; created_by: string;
  account_name: string; lead_title: string | null; product_name: string | null; weighted_value: number | null; proposals: number;
}

export interface LineItem { description: string; kind: 'labor' | 'service' | 'external_cost' | 'other'; quantity: number; unit_price: number }
export interface MilestonePlan { title: string; amount: number; due_in_days: number | null; acceptance: string }

export interface ProposalContent {
  currency: BusinessCurrency; summary: string; scope: string; deliverables: string[]; exclusions: string[]; assumptions: string[];
  acceptance_criteria: string[]; line_items: LineItem[]; milestones: MilestonePlan[]; tax_rate_pct: number; tax_note: string; internal_cost: number;
  payment_terms: string; valid_until: string | null;
}

export interface ProposalVersionView extends ProposalContent {
  id: string; proposal_id: string; version: number; content_hash: string; locked: boolean; created_by: string; created_at: string;
  subtotal: number; external_costs: number; tax: number; total: number; milestone_total: number; margin: number; margin_pct: number | null;
  issues: string[]; warnings: string[];
}

export interface ProposalApproval { id: string; payload_hash: string | null; status: string; arguments: Record<string, unknown>; expires_at: string | null }

export interface BusinessProposal extends Scoped {
  opportunity_id: string; account_id: string; title: string; status: ProposalStatus; current_version: number; accepted_version: number | null;
  accepted_hash: string | null; accepted_by: string | null; accepted_at: string | null; requested_hash: string | null; requested_version: number | null;
  approval_id: string | null; approval_task_id: string | null; decision_note: string; sent_at: string | null; created_by: string;
  account_name: string; opportunity_name: string; current: ProposalVersionView;
  versions: { version: number; content_hash: string; locked: boolean; total: number; created_at: string }[];
  approval_state: ApprovalState; approval: ProposalApproval | null; project_id: string | null;
}

export interface BusinessMilestone extends Scoped {
  project_id: string; position: number; title: string; amount: number; currency: BusinessCurrency; due_on: string | null; status: MilestoneStatus;
  acceptance_criteria: string; accepted_by: string | null; accepted_at: string | null; paid_amount: number | null; paid_on: string | null; notes: string;
}

export interface BusinessProject extends Scoped {
  proposal_id: string; proposal_version: number; opportunity_id: string; account_id: string; name: string; status: ProjectStatus;
  currency: BusinessCurrency; contract_total: number; owner_id: string; start_on: string | null; contract_limits: string; created_by: string;
  account_name: string; milestones: BusinessMilestone[]; accepted_milestones: number; accepted_value: number; paid_value: number;
}

export interface FunnelStep { stage: 'created' | 'qualified' | 'contacted' | 'meeting' | 'offered' | 'won'; reached: number; of: number; rate_pct: number | null; from_previous_pct: number | null }
export interface MoneyFigure { currency: BusinessCurrency; amount: number }

export interface BusinessKpi {
  window_from: string; window_to: string; days: number; leads_created: number; funnel: FunnelStep[]; by_state: Partial<Record<LeadState, number>>;
  open_opportunities: number; pipeline_assumed: MoneyFigure[]; pipeline_weighted: MoneyFigure[]; opportunities_won: number; opportunities_lost: number;
  proposals_sent: number; proposals_accepted: number; contracted: MoneyFigure[]; cash_collected: MoneyFigure[]; research_runs: number; web_searches: number;
  truncated: boolean; notes: string[];
}
