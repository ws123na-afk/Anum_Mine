export type LifecycleStatus = 'draft' | 'active' | 'disabled' | 'archived';
export type PolicyEffect = 'allow' | 'deny' | 'require_approval';
export type PolicyDecision = 'allowed' | 'denied' | 'approval_required';
export type ExportStatus = 'queued' | 'running' | 'completed' | 'failed' | 'expired';
export type RegionHealth = 'healthy' | 'degraded' | 'unavailable';

// Tenant-level role from the identity provider (Keycloak realm role), separate from the
// workspace roles. Only it may change tenant-wide policy: policy packs, role templates,
// organization approval rules and memory governance (docs/governance-and-scale.md).
export type OrganizationRole = 'org_admin';

// Wire shape of GET /api/v1/organization/governance (governance.py: GovernanceSummary).
// `can_manage_organization_policy` is the API's answer for the caller: clients show the
// tenant-wide controls read-only, with the reason, when it is false.
export interface GovernanceSummary {
  tenant_id: string;
  policy_packs: number;
  active_policy_packs: number;
  role_templates: number;
  approval_rules: number;
  memory_governance_configured: boolean;
  can_manage_organization_policy: boolean;
}

// Wire shapes of /api/v1/role-templates and /api/v1/policy-packs
// (services/api/anum_api/governance.py: RoleTemplate, PolicyRule, PolicyPack), snake_case.
export interface OrganizationRoleTemplate {
  id: string;
  tenant_id: string;
  name: string;
  permissions: string[];
  created_at: string;
}

export interface PolicyRule {
  action: string;
  effect: PolicyEffect;
  conditions: Record<string, unknown>;
}

export interface PolicyPack {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  version: number;
  active: boolean;
  rules: PolicyRule[];
  created_by: string;
  created_at: string;
}

export interface PolicyEvaluation {
  id: string;
  tenantId: string;
  workspaceId?: string;
  policyPackId: string;
  policyPackVersion: number;
  principalId: string;
  action: string;
  resource: string;
  decision: PolicyDecision;
  matchedRuleIds: string[];
  reason: string;
  correlationId: string;
  evaluatedAt: string;
}

export interface RetentionPolicy {
  id: string;
  tenantId: string;
  resourceType: 'audit_event' | 'memory' | 'voice_transcript' | 'task_artifact';
  retentionDays: number | null;
  legalHold: boolean;
  region?: string;
  status: LifecycleStatus;
  updatedAt: string;
}

export interface AuditExport {
  id: string;
  tenantId: string;
  requestedBy: string;
  format: 'jsonl' | 'csv';
  status: ExportStatus;
  from: string;
  to: string;
  filters: Record<string, string[]>;
  objectKey?: string;
  sha256?: string;
  recordCount?: number;
  createdAt: string;
  expiresAt?: string;
}

export interface MarketplacePackage {
  id: string;
  publisherId: string;
  kind: 'skill' | 'integration' | 'policy_pack';
  name: string;
  version: string;
  manifestDigest: string;
  signature: string;
  requestedScopes: string[];
  status: 'pending_review' | 'published' | 'suspended' | 'withdrawn';
  publishedAt?: string;
}

export interface MarketplaceInstallation {
  id: string;
  tenantId: string;
  packageId: string;
  packageVersion: string;
  grantedScopes: string[];
  installedBy: string;
  status: 'installed' | 'disabled' | 'upgrade_required' | 'revoked';
  installedAt: string;
}

export interface RegionPlacement {
  tenantId: string;
  homeRegion: string;
  allowedRegions: string[];
  dataResidencyMode: 'strict' | 'preferred';
  failoverMode: 'manual' | 'automatic';
  updatedAt: string;
}

export interface RegionStatus {
  region: string;
  health: RegionHealth;
  acceptingTraffic: boolean;
  replicationLagSeconds?: number;
  checkedAt: string;
}

export interface ModelRoutingPolicy {
  id: string;
  tenantId: string;
  name: string;
  status: LifecycleStatus;
  allowedProviders: string[];
  allowedModels: string[];
  fallbackModels: string[];
  maxCostUsdPerRun?: number;
  maxLatencyMs?: number;
  requiredRegion?: string;
  dataClassification?: string;
  updatedAt: string;
}

export interface RoutingDecision {
  id: string;
  tenantId: string;
  policyId: string;
  provider: string;
  model: string;
  region: string;
  reasonCodes: string[];
  estimatedCostUsd?: number;
  correlationId: string;
  decidedAt: string;
}

export interface CostBudget {
  id: string;
  tenantId: string;
  workspaceId?: string;
  period: 'daily' | 'monthly';
  limitUsd: number;
  warnAtPercent: number;
  enforcement: 'alert' | 'throttle' | 'block';
  status: LifecycleStatus;
  updatedAt: string;
}

export interface ServiceLevelObjective {
  id: string;
  service: string;
  region?: string;
  indicator: 'availability' | 'latency' | 'durability';
  target: number;
  windowDays: number;
}

export interface EnterpriseIncident {
  id: string;
  severity: 'sev1' | 'sev2' | 'sev3' | 'sev4';
  status: 'investigating' | 'identified' | 'monitoring' | 'resolved';
  affectedRegions: string[];
  affectedTenants: string[];
  summary: string;
  startedAt: string;
  resolvedAt?: string;
}
