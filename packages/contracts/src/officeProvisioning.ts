/**
 * ANUM MINE governed agent provisioning contract, version 1.
 * Server: services/api/anum_api/office_provisioning.py and office_provisioning_api.py.
 * Spec: docs/ANUM_MINE_AGENT_PROVISIONING.md.
 *
 * Flow: gaps (from stored records) -> proposal (what would be created, with a payload hash) -> confirm
 * (click only, `workspace:manage`). Nothing is created without the confirm call.
 */
export const OFFICE_PROVISIONING_VERSION = 1 as const;

export type ProvisioningTemplateId =
  | 'orders_desk' | 'sales_followup' | 'support_triage' | 'finance_reconciliation'
  | 'fleet_maintenance' | 'operations_dispatcher' | 'compliance_reviewer';

export type ProvisioningSeverity = 'critical' | 'warning';
export type ProvisioningCoverageState = 'gap' | 'covered' | 'no_signal' | 'source_not_connected' | 'source_no_access';

export interface ProvisioningReason {
  code: 'orders_without_office' | 'leads_without_owner' | 'stuck_without_office' | 'approvals_stuck' | 'empty_department';
  count?: number; stuck?: number; critical?: number;
  department?: string; rooms?: { id: string; name: string }[];
}

export interface ProvisioningGap {
  key: string; template_id: ProvisioningTemplateId; severity: ProvisioningSeverity;
  reasons: ProvisioningReason[]; flow_based: boolean;
}

export interface ProvisioningCoverage {
  template_id: ProvisioningTemplateId; work_kinds: string[]; state: ProvisioningCoverageState;
  handler: { agent_id: string; agent_name: string; agent_status: 'active' | 'paused'; desk_id: string | null } | null;
}

export interface ProvisioningGaps {
  version: 1; workspace_id: string; as_of: string;
  sources: Record<string, string>;
  gaps: ProvisioningGap[]; coverage: ProvisioningCoverage[];
  limits: { agents: number; max_agents: number };
  can_confirm: boolean; templates: ProvisioningTemplateId[];
}

export interface ProvisioningToolState {
  name: string; state: 'present' | 'missing'; reason: 'not_registered' | 'not_permitted' | null;
  risk: string | null; category: string | null; detail: string | null;
}
export interface ProvisioningSkillState {
  id: string; name: string; state: 'present' | 'missing'; reason: 'not_available' | 'needs_tools' | null; source: string | null;
}
export interface ProvisioningMissing {
  kind: 'tool' | 'skill' | 'capability'; id: string; reason: string; en: string; ar: string;
}

export interface ProvisioningTemplate {
  id: ProvisioningTemplateId; department: string; room_department: string | null;
  name: { en: string; ar: string }; description: { en: string; ar: string };
  work_kinds: string[]; icon: string; color: string; model_route: string;
  autonomy: { read: string; write: string; external: string };
  monthly_cost_limit_usd: number; knowledge_mode: string;
  tools: ProvisioningToolState[]; skills: ProvisioningSkillState[];
  limited: boolean; missing: ProvisioningMissing[];
}

export interface ProvisioningPlacementCandidate {
  room_id: string; room_name: string; floor_id: string; building_id: string; team_department: string | null;
  linked: boolean; fits: boolean; mode: 'existing_desk' | 'new_desk' | 'none';
  vacant: number; desks: number; capacity: number;
}

export interface ProvisioningPlacement {
  mode: 'existing_desk' | 'new_desk' | 'none';
  room_id: string | null; desk_id: string | null; desk_label: string | null; reason: string | null;
  room_name: string | null; floor_id: string | null; building_id: string | null;
  candidates: ProvisioningPlacementCandidate[];
}

export interface ProvisioningProposal {
  version: 1; payload_hash: string;
  content: {
    template_id: ProvisioningTemplateId; catalog_version: number; lang: 'en' | 'ar';
    agent: {
      name: string; icon: string; color: string; category: string; department: string; description: string;
      instructions: string; model_route: string; allowed_skills: string[]; allowed_tools: string[];
      autonomy: { read: string; write: string; external: string }; knowledge_mode: string; sharing: string; template_id: string;
    };
    initial_status: 'paused'; work_kinds: string[];
    missing: { kind: string; id: string; reason: string }[]; limited: boolean;
    budget: { monthly_cost_limit_usd: number };
    placement: { mode: string; room_id: string | null; desk_id: string | null; desk_label: string | null; reason: string | null };
  };
  template: ProvisioningTemplate; names: { en: string; ar: string };
  placement: ProvisioningPlacement;
  gap: ProvisioningGap | null; coverage: ProvisioningCoverage;
  can_confirm: boolean;
  creates: { agent: number; desk: number; seat: number; budget_cap: number; ledger_row: number };
}

export interface ProvisioningConfirm {
  payload_hash: string; input_mode: 'click'; lang: 'en' | 'ar'; room_id?: string;
}

export interface ProvisioningResult {
  version: 1; outcome: 'created' | 'resumed' | 'already_provisioned'; template_id: ProvisioningTemplateId;
  agent: { id: string; name: string; status: 'active' | 'paused'; version: number; department: string | null;
    allowed_tools: string[]; allowed_skills: string[] } | null;
  desk_id: string | null; placement: ProvisioningPlacement; missing: ProvisioningMissing[]; limited: boolean;
  budget: { monthly_cost_limit_usd: number }; work_kinds: string[]; paused: boolean;
}
