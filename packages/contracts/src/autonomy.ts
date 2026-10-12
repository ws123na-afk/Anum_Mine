// Autonomy policy (docs/autonomy.md): when ANUM acts on its own, the guardrails it keeps,
// and the activity feed of what it did. Client-side shapes (camelCase) of the
// /api/v1/autonomy routes; the web maps the API's snake_case fields onto them.

export type ToolCategory = 'email' | 'whatsapp' | 'commerce' | 'developer' | 'files' | 'tasks' | 'marketing' | 'general';
export type AutonomyLevel = 'ask_always' | 'act_and_notify' | 'act_silently';
export type ActionKind =
  | 'read' | 'draft' | 'create' | 'reply' | 'send' | 'archive' | 'pause' | 'update'
  | 'delete' | 'price_change' | 'stock_change' | 'payment' | 'merge_code' | 'publish' | 'other';
export type AutonomousActionStatus = 'done' | 'undone';
export type AutonomyProposalStatus = 'collecting' | 'pending' | 'accepted' | 'declined';
export type AutonomyRuleSource = 'manual' | 'proposal' | 'tighten';

/** Local time window when nothing with an effect runs on its own. `end` before `start` crosses midnight. */
export interface QuietHours {
  start: string;
  end: string;
  timezone: string;
}

/** Tenant-wide floors (organization administrators). */
export interface OrganizationGuardrails {
  spendingLimit: number | null;
  maxAutoAmount: number | null;
  maxAutoRecipients: number | null;
  maxAutoPriceChangePercent: number | null;
  categoryCeilings: Partial<Record<ToolCategory, AutonomyLevel>>;
  dailyCaps: Record<string, number>;
  quietHours: QuietHours | null;
  blockedRecipients: string[];
  blockedDomains: string[];
  updatedBy: string | null;
  updatedAt: string | null;
}

/** One workspace's settings (owners). */
export interface WorkspaceAutonomySettings {
  categoryLevels: Partial<Record<ToolCategory, AutonomyLevel>>;
  amountThreshold: number;
  recipientsThreshold: number;
  priceChangePercentThreshold: number;
  dailyCaps: Record<string, number>;
  quietHours: QuietHours | null;
  blockedRecipients: string[];
  blockedDomains: string[];
  approvedTemplates: string[];
  proposalAfter: number;
  updatedBy: string | null;
  updatedAt: string | null;
}

export interface AutonomyCategoryView {
  category: ToolCategory;
  level: AutonomyLevel;
  ceiling: AutonomyLevel | null;
  effective: AutonomyLevel;
}

export interface AutonomyKindRecommendation {
  kind: ActionKind;
  recommended: AutonomyLevel;
  protected: boolean;
  note: string;
}

export interface AutonomyOverview {
  settings: WorkspaceAutonomySettings;
  guardrails: OrganizationGuardrails;
  categories: AutonomyCategoryView[];
  recommendations: AutonomyKindRecommendation[];
  pendingProposals: number;
  canManageWorkspace: boolean;
  canManageGuardrails: boolean;
  canTighten: boolean;
}

export interface AutonomyRule {
  id: string;
  patternKey: string;
  tool: string;
  template: string | null;
  category: ToolCategory;
  actionKind: ActionKind;
  level: AutonomyLevel;
  source: AutonomyRuleSource;
  createdBy: string;
  createdAt: string;
}

export interface AutonomyProposal {
  id: string;
  patternKey: string;
  tool: string;
  template: string | null;
  category: ToolCategory;
  actionKind: ActionKind;
  proposedLevel: AutonomyLevel;
  approvals: number;
  status: AutonomyProposalStatus;
  createdAt: string;
}

/** One action ANUM took on its own. `undoToken` is present only when the caller may undo it now. */
export interface AutonomousAction {
  id: string;
  taskId: string;
  tool: string;
  category: ToolCategory;
  actionKind: ActionKind;
  level: AutonomyLevel;
  patternKey: string;
  template: string | null;
  summary: string;
  target: string | null;
  status: AutonomousActionStatus;
  requestedBy: string;
  createdAt: string;
  reversible: boolean;
  undoAvailable: boolean;
  undoExpiresAt: string | null;
  undoToken: string | null;
  canUndo: boolean;
  undoneBy: string | null;
  undoneAt: string | null;
  undoError: string | null;
  patternLevel: AutonomyLevel | null;
}

export interface AutonomousActionPage {
  items: AutonomousAction[];
  nextCursor: string | null;
}
