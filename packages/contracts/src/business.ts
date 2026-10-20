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

export interface BusinessSettings { research_runs_per_day: number; research_searches_per_day: number; billing: BillingProfile; updated_by: string | null; updated_at: string | null }

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

export type LineKind = 'labor' | 'service' | 'external_cost' | 'other';
export interface LineItem { description: string; kind: LineKind; quantity: number; unit_price: number }
export interface MilestonePlan { title: string; amount: number; due_in_days: number | null; acceptance: string }

export interface ProposalContent {
  currency: BusinessCurrency; summary: string; scope: string; deliverables: string[]; exclusions: string[]; assumptions: string[];
  acceptance_criteria: string[]; line_items: LineItem[]; milestones: MilestonePlan[]; tax_rate_pct: number; tax_note: string; internal_cost: number;
  payment_terms: string; valid_until: string | null;
}

export interface ProposalVersionView extends ProposalContent {
  id: string; proposal_id: string; version: number; content_hash: string; locked: boolean; change_order_id?: string | null; created_by: string; created_at: string;
  subtotal: number; external_costs: number; tax: number; total: number; milestone_total: number; margin: number; margin_pct: number | null;
  issues: string[]; warnings: string[];
}

export interface ProposalApproval { id: string; payload_hash: string | null; status: string; arguments: Record<string, unknown>; expires_at: string | null }

export interface BusinessProposal extends Scoped {
  opportunity_id: string; account_id: string; title: string; status: ProposalStatus; current_version: number; accepted_version: number | null;
  accepted_hash: string | null; accepted_by: string | null; accepted_at: string | null; requested_hash: string | null; requested_version: number | null;
  approval_id: string | null; approval_task_id: string | null; decision_note: string; sent_at: string | null; created_by: string;
  account_name: string; opportunity_name: string; current: ProposalVersionView;
  versions: { version: number; content_hash: string; locked: boolean; change_order_id?: string | null; total: number; created_at: string }[];
  approval_state: ApprovalState; approval: ProposalApproval | null; project_id: string | null;
  /** What the customer decided in their portal about the version waiting for acceptance. */
  portal_decision: PortalDecisionSummary | null;
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

// Change orders, the usage and cost ledger and project profitability (docs/business.md#change-orders).
export type ChangeOrderStatus = 'draft' | 'pending_approval' | 'approved' | 'rejected' | 'withdrawn';
export interface ChangeOrderLine extends LineItem { action: 'add' | 'remove' }

export interface BusinessChangeOrder extends Scoped {
  project_id: string; proposal_id: string; account_id: string; number: number; title: string; request: string; requested_by: string;
  lines: ChangeOrderLine[]; timeline_days: number; internal_cost_delta: number; status: ChangeOrderStatus; currency: BusinessCurrency;
  base_version: number | null; base_hash: string | null; content_hash: string | null; subtotal_delta: number | null; tax_delta: number | null;
  total_delta: number | null; result_version: number | null; submitted_by: string | null; submitted_at: string | null; decided_by: string | null;
  decided_at: string | null; decision_note: string; created_by: string;
  project_name: string; issues: string[]; current_version: number | null; stale: boolean;
  /** What the customer decided in their portal about this exact change order. */
  portal_decision: PortalDecisionSummary | null;
}

export interface ChangeOrderInput {
  title: string; request: string; requested_by: string; lines: ChangeOrderLine[]; timeline_days: number; internal_cost_delta: number;
}

export type UsageKind = 'labour' | 'ai_usage' | 'hosting' | 'messaging' | 'other';

export interface BusinessUsageEntry extends Scoped {
  account_id: string; project_id: string | null; kind: UsageKind; description: string; occurred_on: string; quantity: number; unit_cost: number;
  amount: number; currency: BusinessCurrency; billable: boolean; source: 'manual' | 'model_budget'; external_ref: string | null; created_by: string;
  account_name: string; project_name: string | null;
}

export interface BusinessProfitability {
  project_id: string; currency: BusinessCurrency; accepted_version: number; current_version: number; accepted_revenue: number;
  change_orders_revenue: number; approved_change_orders: number; revenue: number; tax: number; contract_total: number;
  cash_collected: number | null; outstanding: number | null; labour_hours: number | null; labour_cost: number | null; direct_costs: number | null;
  costs: { kind: UsageKind; amount: number; entries: number }[]; billable_costs: number | null; non_billable_costs: number | null;
  gross_margin: number | null; gross_margin_pct: number | null; other_currency: { currency: BusinessCurrency; amount: number; entries: number }[];
  reconciled: boolean; notes: string[];
}

export interface FunnelStep { stage: 'created' | 'qualified' | 'contacted' | 'meeting' | 'offered' | 'won'; reached: number; of: number; rate_pct: number | null; from_previous_pct: number | null }
export interface MoneyFigure { currency: BusinessCurrency; amount: number }

export interface BusinessKpi {
  window_from: string; window_to: string; days: number; leads_created: number; funnel: FunnelStep[]; by_state: Partial<Record<LeadState, number>>;
  open_opportunities: number; pipeline_assumed: MoneyFigure[]; pipeline_weighted: MoneyFigure[]; opportunities_won: number; opportunities_lost: number;
  proposals_sent: number; proposals_accepted: number; contracted: MoneyFigure[]; cash_collected: MoneyFigure[]; research_runs: number; web_searches: number;
  truncated: boolean; notes: string[];
}

// -- support tickets (/api/v1/business/support, docs/business.md#support-tickets) -----------------
// Targets are working hours inside the policy's working days and hours, never wall-clock: there is
// no 24/7 cover. A ticket links to the inbound email or WhatsApp conversation by id only.

export type TicketSeverity = 'critical' | 'high' | 'normal' | 'low';
export type TicketStatus = 'new' | 'triaged' | 'in_progress' | 'waiting_on_customer' | 'resolved' | 'closed';
export type TicketSource = 'manual' | 'email' | 'whatsapp';
export type TicketEntitlement = 'project' | 'support_plan' | 'none';
export type ResolutionCode = 'fixed' | 'workaround' | 'answered' | 'no_fault_found' | 'duplicate' | 'out_of_scope';
export type ReplyChannel = 'email' | 'whatsapp';
export type ReplyStatus = 'draft' | 'pending_approval' | 'sent' | 'failed' | 'discarded';
/** One SLA clock: met or missed once done, paused while waiting on the customer, else breached, due soon or on track. */
export type SlaClock = 'none' | 'met' | 'missed' | 'paused' | 'breached' | 'due_soon' | 'on_track';

export interface SeverityTarget { response_hours: number; resolution_hours: number }

export interface SupportPolicy {
  timezone: string;
  /** Monday 0 to Sunday 6. */
  work_days: number[];
  start_hour: number;
  end_hour: number;
  targets: Record<TicketSeverity, SeverityTarget>;
  note: string;
}

export interface SupportSetup {
  policy: SupportPolicy;
  severities: TicketSeverity[];
  statuses: TicketStatus[];
  open_statuses: TicketStatus[];
  transitions: Record<TicketStatus, TicketStatus[]>;
  resolution_codes: ResolutionCode[];
  entitlements: TicketEntitlement[];
  can_write: boolean;
  can_manage: boolean;
  can_decide: boolean;
  can_link_email: boolean;
  can_link_whatsapp: boolean;
}

export interface SupportAssignee { user_id: string; role: string; you: boolean }

export interface SupportSummary { open: number; new: number; unassigned: number; escalated: number; breached: number; waiting_on_customer: number; truncated: boolean }

export interface TicketSla { response: SlaClock; resolution: SlaClock; breached: boolean }

export interface SupportTicket extends Scoped {
  title: string; description: string; account_id: string | null; contact_id: string | null; project_id: string | null;
  requester_name: string; requester_email: string | null; requester_phone: string | null; requester_verified: boolean;
  entitlement: TicketEntitlement; support_offer_id: string | null; entitlement_note: string;
  severity: TicketSeverity; status: TicketStatus; assignee_id: string | null;
  source: TicketSource; source_ref: string | null; source_account_id: string | null;
  reported_at: string; response_due_at: string | null; resolution_due_at: string | null; first_responded_at: string | null;
  sla_paused_at: string | null; sla_paused_minutes: number;
  escalated: boolean; escalation_reason: string; escalated_at: string | null; escalated_by: string | null;
  resolution_code: ResolutionCode | null; resolution_summary: string; resolved_at: string | null; resolved_by: string | null;
  closed_at: string | null; closed_by: string | null; reopened_count: number; created_by: string;
  /** Short reference such as T-1A2B3C. */
  reference: string;
  account_name: string | null; contact_name: string | null; project_name: string | null; support_plan_name: string | null;
  sla: TicketSla;
  /** The statuses this ticket may move to now. */
  allowed: TicketStatus[];
}

export interface TicketReplyApproval {
  id: string; task_id: string; action: string; risk_level: string; status: string; reason: string; created_at: string; decided_at: string | null;
  arguments: Record<string, unknown>; payload_hash: string | null; expires_at: string | null; decided_by: string | null; decision_reason: string | null;
}

export interface TicketReply extends Scoped {
  ticket_id: string; channel: ReplyChannel; body: string; status: ReplyStatus; generator: string; external_ref: string | null;
  approval_id: string | null; task_id: string | null; error: string; requested_by: string | null; requested_at: string | null; sent_at: string | null;
  created_by: string;
  approval: TicketReplyApproval | null;
}

export interface TicketActivity extends BusinessActivity { ticket_id: string | null }

export interface TicketDetail extends SupportTicket {
  replies: TicketReply[];
  activities: TicketActivity[];
  can_reply: boolean;
  reply_channel: ReplyChannel | null;
}

export interface TicketReplyRequest { ticket: TicketDetail; reply: TicketReply; detail: string }

/** Fields a ticket accepts on create and PATCH (all optional on PATCH; null clears a link). */
export interface TicketFields {
  title?: string; description?: string; severity?: TicketSeverity;
  account_id?: string | null; contact_id?: string | null; project_id?: string | null;
  requester_name?: string; requester_email?: string | null; requester_phone?: string | null;
  entitlement?: TicketEntitlement; support_offer_id?: string | null; entitlement_note?: string; assignee_id?: string | null;
}

// -- invoices and the customer portal (docs/business.md#invoices, docs/business-portal.md) ----------------------------

export type InvoiceStatus = 'draft' | 'pending_approval' | 'sent' | 'paid' | 'void';
export type InvoiceLineSource = 'manual' | 'offer' | 'milestone' | 'contract' | 'usage';
export type InvoicePaymentState = 'unpaid' | 'partially_paid' | 'paid' | 'credited';
export type CreditNoteStatus = 'issued' | 'void';
export type PaymentNoticeStatus = 'pending' | 'confirmed' | 'rejected';
export type PaymentMethod = 'bank_transfer' | 'cash' | 'card' | 'cheque' | 'other';

/** Who issues the invoices and how they are paid; copied onto each invoice when it is sent. */
export interface BillingProfile {
  legal_name: string; address: string; tax_label: string; tax_number: string; email: string; phone: string; bank_details: string;
  footer_note: string; invoice_prefix: string; default_due_days: number; default_tax_rate_pct: number;
}

export interface InvoiceLine {
  description: string; kind: LineKind; quantity: number; unit_price: number; tax_rate_pct: number; source: InvoiceLineSource; source_id: string | null;
  gross_total: number | null;
  /** Worked out by the server. */
  net: number; tax: number; total: number;
}

export interface InvoicePayment extends Scoped {
  invoice_id: string; amount: number; paid_on: string; method: PaymentMethod; reference: string; note: string; recorded_by: string;
}

/** Money an owner took off a sent invoice; the invoice itself never changes (docs/business.md#credit-notes). */
export interface CreditNote extends Scoped {
  invoice_id: string; account_id: string; seq: number; number: string; status: CreditNoteStatus; currency: BusinessCurrency; amount: number; subtotal: number; tax: number;
  reason: string; issue_date: string; issued_by: string; voided_by: string | null; voided_at: string | null; void_reason: string;
}

/** What a customer reported paying from the portal. It changes no balance until a person confirms it. */
export interface PaymentNotice extends Scoped {
  invoice_id: string; account_id: string; access_id: string; contact_id: string; payer_name: string; amount: number; paid_on: string; method: PaymentMethod;
  reference: string; note: string; status: PaymentNoticeStatus; decided_by: string | null; decided_at: string | null; decision_note: string; payment_id: string | null;
}

export interface InvoiceCan {
  edit: boolean; request_send: boolean; cancel_request: boolean; delete: boolean; void: boolean; record_payment: boolean; remove_payment: boolean;
  credit: boolean; void_credit_note: boolean; decide_notice: boolean;
}

export interface BusinessInvoice extends Scoped {
  account_id: string; project_id: string | null; status: InvoiceStatus; currency: BusinessCurrency; lines: InvoiceLine[];
  subtotal: number; tax: number; total: number; amount_paid: number; amount_credited: number; due_in_days: number;
  seq: number | null; number: string | null; issue_date: string | null; due_date: string | null;
  customer_reference: string; notes: string; payment_terms: string;
  bill_to_name: string; bill_to_address: string; bill_to_email: string; bill_to_tax_number: string;
  seller: BillingProfile | null; content_hash: string | null; requested_hash: string | null; approval_id: string | null;
  requested_by: string | null; requested_at: string | null; sent_by: string | null; sent_at: string | null; paid_at: string | null;
  voided_by: string | null; voided_at: string | null; void_reason: string; decision_note: string; created_by: string;
  account_name: string; project_name: string | null; balance: number; overdue: boolean; days_overdue: number; payment_state: InvoicePaymentState;
  payments: InvoicePayment[];
  credit_notes: CreditNote[];
  payment_notices: PaymentNotice[];
  approval_state: ApprovalState;
  approval: TicketReplyApproval | null;
  /** Why a draft cannot be sent for approval yet. */
  issues: string[];
  can: InvoiceCan;
}

export interface InvoiceListItem {
  id: string; number: string | null; status: InvoiceStatus; account_id: string; account_name: string; project_id: string | null; project_name: string | null;
  currency: BusinessCurrency; total: number; amount_paid: number; amount_credited: number; balance: number; issue_date: string | null; due_date: string | null;
  overdue: boolean; days_overdue: number; payment_state: InvoicePaymentState; customer_reference: string; notices_pending: number; lines: number; updated_at: string; created_at: string;
}

export interface InvoiceSummary {
  counts: Record<InvoiceStatus, number>;
  currencies: { currency: BusinessCurrency; outstanding: number; overdue: number; overdue_count: number; invoiced_this_month: number; collected_this_month: number; drafts: number }[];
  truncated: boolean;
  /** Customer payment reports waiting for a member or owner. */
  notices_pending: number;
  notes: string[];
}

export interface BillableMilestone {
  id: string; position: number; title: string; amount: number; due_on: string | null; status: MilestoneStatus; change_order_number: number | null;
  billable: boolean; reason: string;
}
export interface BillableUsage {
  id: string; description: string; kind: UsageKind; occurred_on: string; quantity: number; unit_cost: number; amount: number; currency: BusinessCurrency;
  project_id: string | null; billable: boolean; reason: string;
}
export interface BillableOffer { id: string; label: string; currency: BusinessCurrency; price_type: PriceType; price: number | null }
export interface Billable {
  account_id: string; project_id: string | null; currency: BusinessCurrency; tax_rate_pct: number;
  milestones: BillableMilestone[];
  contract: { contract_total: number; invoiced: number; remaining: number } | null;
  usage: BillableUsage[];
  offers: BillableOffer[];
}

export interface ManualInvoiceLine { description: string; kind?: LineKind; quantity?: number; unit_price: number; tax_rate_pct?: number | null }
export interface InvoiceFields {
  due_in_days?: number | null; customer_reference?: string | null; notes?: string | null; payment_terms?: string | null;
  bill_to_name?: string | null; bill_to_address?: string | null; bill_to_email?: string | null; bill_to_tax_number?: string | null;
}
export interface InvoiceCreate extends InvoiceFields {
  account_id?: string; project_id?: string; contact_id?: string; currency?: BusinessCurrency;
  milestone_ids?: string[]; contract_amount?: number; usage_ids?: string[]; usage_markup_pct?: number;
  offer_id?: string; offer_quantity?: number; offer_unit_price?: number; lines?: ManualInvoiceLine[]; tax_rate_pct?: number;
}
export interface InvoiceUpdate extends InvoiceFields { lines?: ManualInvoiceLine[]; remove_lines?: number[]; tax_rate_pct?: number }
export interface PaymentInput { amount: number; paid_on: string; method?: PaymentMethod; reference?: string; note?: string }
export interface CreditNoteInput { amount: number; reason: string }

export type PortalDecisionValue = 'approved' | 'declined';

/** What a customer decided about the version waiting for the owner (on proposals and change orders). */
export interface PortalDecisionSummary {
  decision: PortalDecisionValue; signer_name: string; note: string; decided_at: string; version: number | null; content_hash: string; current: boolean;
}

export interface PortalAccessItem {
  id: string; account_id: string; contact_id: string; contact_name: string; contact_email: string | null; status: 'active' | 'revoked'; expired: boolean;
  can_decide: boolean; expires_at: string; last_used_at: string | null; created_at: string; created_by: string; revoked_at: string | null;
}
/** The link is shown once: only its hash is kept. */
export interface PortalAccessCreated extends PortalAccessItem { token: string; portal_path: string }
export interface PortalAccessInput { account_id: string; contact_id: string; expires_in_days?: number; can_decide?: boolean }
export interface PortalDecisionRow {
  id: string; subject_kind: 'proposal' | 'change_order'; subject_id: string; version: number | null; decision: PortalDecisionValue; signer_name: string; note: string;
  decided_at: string; contact_name: string;
}

// The customer's own view (/api/v1/portal). No internal figure, note, assignee or SLA ever appears here.

export interface PortalMe {
  business_name: string; account_name: string; contact_name: string; expires_at: string; can_decide: boolean;
  counts: { offers_waiting: number; change_orders_waiting: number; invoices_open: number; invoices_overdue: number; tickets_open: number };
  projects: { id: string; name: string }[];
}
export interface PortalDecisionView { decision: PortalDecisionValue; signer_name: string; note: string; decided_at: string }
export interface PortalOfferSummary {
  id: string; title: string; status: 'waiting' | 'accepted'; currency: BusinessCurrency; total: number; version: number; sent_at: string | null;
  valid_until: string | null; expired: boolean; decision: PortalDecisionView | null; can_decide: boolean;
}
export interface PortalOffer extends PortalOfferSummary {
  summary: string; scope: string; deliverables: string[]; exclusions: string[]; assumptions: string[]; acceptance_criteria: string[];
  line_items: { description: string; quantity: number; unit_price: number; amount: number }[];
  milestones: { title: string; amount: number; due_in_days: number | null; acceptance: string }[];
  subtotal: number; tax: number; tax_rate_pct: number; tax_note: string; payment_terms: string; content_hash: string;
}
export interface PortalDecisionInput { decision: PortalDecisionValue; content_hash: string; signer_name: string; note?: string }
export interface PortalChangeSummary {
  id: string; number: number; title: string; project_name: string; status: 'waiting' | 'approved'; currency: BusinessCurrency; total_delta: number | null;
  timeline_days: number; decision: PortalDecisionView | null; can_decide: boolean; stale: boolean;
}
export interface PortalChange extends PortalChangeSummary {
  request: string; subtotal_delta: number | null; tax_delta: number | null; content_hash: string;
  lines: { action: 'add' | 'remove'; description: string; quantity: number; unit_price: number; amount: number }[];
}
export interface PortalInvoiceSummary {
  id: string; number: string; status: InvoiceStatus; currency: BusinessCurrency; total: number; amount_paid: number; amount_credited: number; balance: number; issue_date: string | null;
  due_date: string | null; overdue: boolean; days_overdue: number; payment_state: InvoicePaymentState; customer_reference: string;
}
export interface PortalInvoice extends PortalInvoiceSummary {
  lines: { description: string; quantity: number; unit_price: number; tax_rate_pct: number; total: number }[];
  subtotal: number; tax: number; notes: string; payment_terms: string; bill_to_name: string; bill_to_address: string; bill_to_tax_number: string;
  seller: { legal_name: string; address: string; tax_label: string; tax_number: string; email: string; phone: string; bank_details: string };
  payments: { paid_on: string; amount: number; method: PaymentMethod; reference: string }[];
  credit_notes: { number: string; issue_date: string; amount: number; reason: string; status: CreditNoteStatus }[];
  payment_notices: { id: string; amount: number; paid_on: string; method: PaymentMethod; reference: string; status: PaymentNoticeStatus; decision_note: string; created_at: string }[];
  /** A sent invoice with a balance, on a link that may act, in a workspace that is not paused. */
  can_report_payment: boolean;
  void_reason: string;
}
export interface PortalPaymentNoticeInput { payer_name: string; amount: number; paid_on: string; method: PaymentMethod; reference?: string; note?: string }
export interface PortalTicketSummary {
  id: string; reference: string; title: string; severity: TicketSeverity; status: TicketStatus; project_name: string | null; reported_at: string;
  updated_at: string; opened_here: boolean;
}
export interface PortalTicket extends PortalTicketSummary {
  description: string; resolution_summary: string; resolved_at: string | null; replies: { body: string; sent_at: string | null }[];
}
export interface PortalTicketInput { title: string; description: string; severity?: 'low' | 'normal' | 'high'; project_id?: string | null }
