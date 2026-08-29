export type SkillRiskLevel = 'low' | 'medium' | 'high' | 'blocked';

export interface SkillVersion {
  id: string;
  skillId: string;
  version: string;
  name: string;
  description: string;
  instructions: string;
  requiredTools: string[];
  riskLevel: SkillRiskLevel;
  publisherTenantId: string;
  createdBy: string;
  createdAt: string;
}

export interface SkillInstallation {
  id: string;
  tenantId: string;
  workspaceId: string;
  skillVersionId: string;
  skillId: string;
  version: string;
  approvedTools: string[];
  enabled: boolean;
  installedBy: string;
  installedAt: string;
}

export interface WorkspaceFile {
  id: string;
  tenantId: string;
  workspaceId: string;
  name: string;
  contentType: string;
  sizeBytes: number;
  sha256: string;
  createdBy: string;
  createdAt: string;
}
