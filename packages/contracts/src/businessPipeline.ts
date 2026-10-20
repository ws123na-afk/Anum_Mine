// Business prospecting pipeline and outreach outbox (docs/business.md#prospecting-pipeline, #outreach).
// Mirrors services/api/anum_api/business/api_pipeline.py and api_outreach.py.
import type { BusinessDraft, BusinessEvidence, BusinessInference, Criterion } from './business.js';

export type SignalStatus = 'proposed' | 'confirmed' | 'rejected';
export type RunKind = 'discover' | 'profile';
export type RunStatus = 'estimated' | 'running' | 'succeeded' | 'partial' | 'stopped' | 'failed' | 'interrupted';
export type OutboxStatus = 'scheduled' | 'pending_approval' | 'held' | 'sending' | 'sent' | 'failed' | 'cancelled' | 'rejected' | 'needs_review';
export type ReplyKind = 'reply' | 'opt_out' | 'bounce' | 'escalate';
export type CompanySize = 'micro' | 'small' | 'medium' | 'large' | 'enterprise';

export interface BusinessSignal {
  code: string;
  label: string;
  description: string;
  category: 'discovery' | 'profile' | 'channel';
  value_type: 'bool' | 'num' | 'text';
  cost_note: string;
  criterion: Criterion | null;
  status: SignalStatus;
  decided_by: string | null;
  decided_at: string | null;
}

export interface PipelineSettings {
  fetches_per_day: number;
  pages_per_lead: number;
  sending_enabled: boolean;
  daily_quota: Record<string, number>;
  max_bounce_rate_pct: number;
  follow_up_max: number;
  follow_up_min_days: number;
  paused: boolean;
  paused_reason: string;
  paused_at: string | null;
  research_searches_per_day: number;
  research_runs_per_day: number;
  usage_today: { runs: number; searches: number; page_fetches: number; model_calls: number };
  tools: { web_search: boolean; page_fetch: boolean };
  model_connected: boolean;
  can_manage: boolean;
  excluded: string[];
}

export interface TargetProfileInput {
  industries: string[];
  company_sizes: CompanySize[];
  regions: string[];
  roles: string[];
  keywords: string[];
  disqualifiers: string[];
  trigger_events: string[];
  company_size_note: string;
  value_proposition: string;
  value_proposition_ar: string;
}

export interface TargetProfile extends TargetProfileInput {
  product_id: string;
  product_name: string;
  saved: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface RunEstimate {
  searches: number;
  page_fetches: number;
  model_calls: number;
  estimated_tokens: number;
  estimated_cost_usd: number | null;
  searches_left_today: number;
  fetches_left_today: number;
  budget_cost_left_usd: number | null;
  budget_tokens_left: number | null;
  fits: boolean;
  problems: string[];
  notes: string[];
}

export interface RunStep { at: string; step: string; status: string; summary: string; target: string | null }

export interface PipelineRun {
  id: string;
  kind: RunKind;
  status: RunStatus;
  product_id: string | null;
  plan: { queries?: string[]; terms?: string[]; places?: string[]; lead_ids?: string[]; skipped?: string[]; max_new_leads?: number };
  estimate: RunEstimate;
  used: { searches: number; page_fetches: number; model_calls: number };
  steps: RunStep[];
  results: { lead_ids?: string[]; known?: number; junk?: number; possible_duplicates?: { name: string; domain: string; matches_account_id: string }[]; profiled?: string[]; observations?: number; inferences?: number };
  stop_requested: boolean;
  stop_reason: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  finished_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BusinessObservation {
  id: string;
  lead_id: string;
  account_id: string;
  run_id: string | null;
  evidence_id: string | null;
  signal_code: string;
  value_num: number | null;
  value_text: string;
  url: string;
  excerpt: string;
  observed_on: string;
  confidence: number;
}

export interface PipelineRunDetail extends PipelineRun {
  leads: { id: string; title: string; state: string; account_name: string; domain: string | null }[];
  evidence: BusinessEvidence[];
  observations: BusinessObservation[];
}

export interface ScoreSuggestion { criterion: Criterion; score: number; reason: string; rule: string; observation_ids: string[]; evidence_ids: string[] }

export interface CriterionLine {
  criterion: Criterion;
  score: number | null;
  reason: string;
  evidence_ids: string[];
  known: boolean;
  conversation_only: boolean;
  suggestion: ScoreSuggestion | null;
}

export interface ProductFit { product_id: string; product_name: string; score: number | null; disqualified: boolean; reasons: string[]; current: boolean }

export interface LeadProfile {
  lead_id: string;
  score_total: number;
  score_known: number;
  product_fits: ProductFit[];
  best_fit: ProductFit | null;
  breakdown: CriterionLine[];
  observations: BusinessObservation[];
  facts: BusinessEvidence[];
  inferences: BusinessInference[];
  signal_labels: Record<string, string>;
}

export interface OutboxApproval {
  id: string;
  task_id?: string;
  action: string;
  arguments?: Record<string, unknown>;
  payload_hash: string | null;
  status: string;
  [key: string]: unknown;
}

export interface OutboxItem {
  id: string;
  lead_id: string;
  contact_id: string;
  draft_id: string;
  channel: 'email' | 'whatsapp';
  step: number;
  follow_up_of: string | null;
  sequence_id: string;
  recipient: string;
  subject: string;
  body: string;
  content_hash: string;
  status: OutboxStatus;
  gate_issues: string[];
  approval_id: string | null;
  send_after: string | null;
  sent_at: string | null;
  provider_ref: string;
  note: string;
  created_at: string;
  updated_at: string;
  company: string;
  contact_name: string;
  lead_title: string;
  lead_state: string;
  due: boolean;
  approval: OutboxApproval | null;
}

export interface RateFigure { count: number; of: number; rate_pct: number | null }

export interface OutreachOverview {
  sending_enabled: boolean;
  paused: boolean;
  paused_reason: string;
  daily_quota: Record<string, number>;
  sent_today: Record<string, number>;
  waiting: Record<string, number>;
  metrics: {
    days: number;
    sent: Record<string, number>;
    sent_total: number;
    by_status: Record<OutboxStatus, number>;
    replies: RateFigure;
    opt_outs: RateFigure;
    bounces: RateFigure;
    escalations: number;
    opt_out_alarm: boolean;
    truncated: boolean;
  };
  channels_connected: string[];
}

export interface ReadyDraft { draft: BusinessDraft; lead_title: string; company: string; contact_name: string; queued_for: string[] }

export interface ReplyEvent {
  id: string;
  lead_id: string;
  contact_id: string | null;
  outbox_id: string | null;
  channel: string;
  kind: ReplyKind;
  cancelled: number;
  source: string;
  note: string;
  created_at: string;
}
