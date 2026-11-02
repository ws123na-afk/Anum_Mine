/**
 * Green Concrete agents kit contract, version 1.
 * Server: services/api/anum_api/green_concrete_kit.py, green_concrete_ops.py and green_concrete_kit_api.py.
 * Spec: docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md.
 *
 * Role skills with the data each needs, their read tools, data gaps, the shared knowledge collection, routines
 * (created turned off) and the Green Concrete agents with their memory counts. Setup and the routine switch are
 * an owner's click (`workspace:manage`); every setup item carries its own hash.
 */
export const GREEN_CONCRETE_KIT_VERSION = 1 as const;

export interface GcText { en: string; ar: string }
export type GcSourceState = 'connected' | 'not_connected' | 'available' | 'no_access';
export type GcSkillState = 'ready' | 'not_connected' | 'no_access' | 'unavailable';

export interface GcKitSkill {
  id: string; version: string; name: GcText; description: GcText; tool: string;
  inputs: GcText[]; output: GcText; guardrails: GcText[];
  sources: { id: string; state: GcSourceState }[]; state: GcSkillState; roles: string[];
}
export interface GcKitTool {
  name: string; label: GcText; description: string; risk: 'low' | 'medium' | 'high' | 'blocked' | null;
  registered: boolean; read_only: true; permissions: string[]; sources: string[];
}
export interface GcKitRole { key: string; name: GcText; purpose: GcText; max_risk: 'low' | 'medium'; skills: string[]; tools: string[] }
export interface GcKitAgent {
  id: string; name: string; status: 'active' | 'paused'; role: string; can_edit: boolean;
  skills: string[]; tools: string[]; reads_collection: boolean; memories: number | null;
}
export type GcKnowledgeState = 'missing' | 'empty' | 'ready' | 'no_access';
export interface GcKnowledge {
  state: GcKnowledgeState;
  collection: { id: string; name: string; item_count: number; description: string } | null;
}
export type GcRoutineState = 'missing' | 'needs_agent' | 'off' | 'on' | 'no_access';
export interface GcRoutine {
  key: string; name: GcText; description: GcText; role: string; skill: string; cron: string; timezone: string;
  state: GcRoutineState; workflow_id: string | null; schedule_id: string | null; next_run_at: string | null;
  agent: { id: string; name: string; status: 'active' | 'paused' } | null;
}
export interface GcSetupItem { id: string; kind: 'knowledge' | 'routine'; content: Record<string, unknown>; hash: string }

export interface GcKit {
  version: 1; kit_version: number; as_of: string; lang: 'en' | 'ar';
  building: { id: string; name: string } | null;
  skills: GcKitSkill[]; tools: GcKitTool[]; roles: GcKitRole[]; agents: GcKitAgent[];
  sources: { id: string; state: GcSourceState; name: GcText; view: string | null }[];
  gaps: { id: string; name: GcText; detail: GcText }[];
  knowledge: GcKnowledge; routines: GcRoutine[]; items: GcSetupItem[];
  can_manage: boolean; can_manage_automations: boolean;
}

export type GcSetupOutcome = 'created' | 'already_done' | 'being_built' | 'stale' | 'failed';
export interface GcSetupResult {
  version: 1; created: number;
  results: { id: string; outcome: GcSetupOutcome; target_id?: string; enabled?: boolean; detail?: string }[];
}
export interface GcRoutineStateResult { key: string; enabled: boolean; workflow_id: string }
