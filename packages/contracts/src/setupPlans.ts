// Setup plans (docs/setup-plan.md): a checked-in business blueprint previewed and applied to a
// workspace through each feature's own routes. Mirrors anum_api/setup_plans.py.

export type SetupPlanPhase = 'pilot' | 'all';
export type SetupPlanSection =
  | 'workspaces' | 'governance' | 'knowledge' | 'budget' | 'skills' | 'agents' | 'assistants'
  | 'commands' | 'automations' | 'controls' | 'home' | 'offices' | 'business_agents' | 'manual' | 'unsupported';
export type SetupPlanAction =
  | 'create' | 'update' | 'present' | 'kept' | 'skipped' | 'later' | 'manual' | 'builtin' | 'unsupported';
export type SetupPlanOutcome =
  | 'created' | 'updated' | 'present' | 'kept' | 'skipped' | 'failed' | 'busy' | 'not_applied';

export interface SetupPlanLink { view: string; id: string | null }

export interface SetupPlanItem {
  key: string;
  section: SetupPlanSection;
  kind: string;
  name: string;
  action: SetupPlanAction;
  reason_code: string;
  reason: string;
  week: number | null;
  link: SetupPlanLink | null;
  facts: string[];
  outcome: SetupPlanOutcome | null;
  error: string | null;
}

export interface SetupPlanWeek { week: number; title: string }

export interface SetupPlanSummary {
  id: string;
  version: number;
  name: string;
  summary: string;
  currency: string;
  timezone: string;
  business_hours: string;
  pilot_agents: string[];
  weeks: SetupPlanWeek[];
  counts: Record<string, number>;
  applied_items: number;
  last_applied_at: string | null;
}

export interface SetupPlanSupportNote { label: string; plan: string; support: 'supported' | 'partial' | 'unsupported'; anum: string }

/** What the person chose on the page; the preview and the apply take the same options. */
export interface SetupPlanOptions {
  /** Also make the plan's business agents in each business workspace (whole plan; checked per workspace). */
  business_workspaces: boolean;
  /** New and untouched plan automations run as their agent (its tools, autonomy, caps and loop limits). */
  run_automations_as_agents: boolean;
  /** Seat each agent at a free desk of its headquarters office, where one exists. */
  seat_in_offices: boolean;
}

export interface SetupPlanPreview {
  plan: SetupPlanSummary;
  phase: SetupPlanPhase;
  overwrite_edited: boolean;
  options: SetupPlanOptions;
  access: { owner: boolean; organization_admin: boolean; can_apply: boolean; notes: string[] };
  budget_aed: number;
  budget_usd: number;
  items: SetupPlanItem[];
  sections: { section: SetupPlanSection; total: number; actions: Record<string, number> }[];
  approval_thresholds: SetupPlanSupportNote[];
  memory_rules: SetupPlanSupportNote[];
}

export interface SetupPlanApplyRequest extends SetupPlanOptions { phase: SetupPlanPhase; overwrite_edited: boolean; plan_version: number }

/** The live checklist (GET /setup-plans/{id}/checklist): each manual step read from real state. */
export type SetupChecklistState = 'done' | 'todo' | 'partial' | 'cannot_check' | 'no_access';

export interface SetupChecklistDetail { label: string; state: SetupChecklistState; note: string | null }

export interface SetupChecklistItem {
  key: string;
  check: string | null;
  title: string;
  detail: string;
  state: SetupChecklistState;
  summary: string;
  view: string | null;
  details: SetupChecklistDetail[];
}

export interface SetupChecklist { plan_id: string; done: number; total: number; items: SetupChecklistItem[] }

export interface SetupPlanApplyResult extends SetupPlanPreview {
  outcomes: Record<string, number>;
  applied_at: string;
}

export type PilotCheckStatus = 'pass' | 'fail' | 'not_run';

export interface PilotCheckResult {
  plan_id: string;
  check_id: string;
  status: PilotCheckStatus;
  note: string;
  evidence: string;
  updated_by: string;
  updated_at: string;
}

export interface PilotCheck {
  id: string;
  group: 'customer_care' | 'cod' | 'owner';
  title: string;
  input: string;
  expected: string;
  approval_gate: string;
  pass_criteria: string;
  support: 'supported' | 'partial' | 'unsupported';
  anum: string;
  verified_by: string | null;
  result: PilotCheckResult | null;
}

export interface PilotChecks {
  plan_id: string;
  gate: string;
  total: number;
  passed: number;
  failed: number;
  not_run: number;
  done: boolean;
  checks: PilotCheck[];
}

export interface PilotCheckUpdate { status: PilotCheckStatus; note: string; evidence: string }
