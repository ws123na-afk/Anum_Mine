// Integrations, skills and marketplace calls over the live API (docs/tools-and-integrations.md,
// docs/skills.md, docs/governance-and-scale.md). Every call carries the current tenant and
// workspace through the shared request's headers.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import type { IntegrationState, MarketPackage, PackageInstall, SkillDetail, SkillInstallLike, SkillVersionLike, WorkspaceCredential } from './catalog';

const get = <T>(path: string) => apiRequest<T>(path, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown = {}) => apiRequest<T>(path, { method, body: JSON.stringify(body) });
const id = encodeURIComponent;

async function remove(path: string, prefix: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response, prefix);
}

export const getIntegrationStates = () => get<IntegrationState[]>('/api/v1/integrations');
export const testIntegration = (integrationId: string) => send<IntegrationState>('POST', `/api/v1/integrations/${id(integrationId)}/test`);
export const saveIntegrationCredential = (integrationId: string, secret: string) => send<WorkspaceCredential>('PUT', `/api/v1/integrations/${id(integrationId)}/credential`, { secret });
export const deleteIntegrationCredential = (integrationId: string) => remove(`/api/v1/integrations/${id(integrationId)}/credential`, 'Removing the credential failed');

export const getSkillDetail = (skillId: string, version?: string) => get<SkillDetail>(`/api/v1/skills/${id(skillId)}${version ? `?version=${id(version)}` : ''}`);
export const publishSkillVersion = (body: unknown) => send<SkillVersionLike>('POST', '/api/v1/skills/versions', body);
export const rollbackSkill = (skillId: string, version: string) => send<SkillInstallLike>('POST', `/api/v1/skills/installations/${id(skillId)}/rollback`, { version });

export const getPackages = () => get<MarketPackage[]>('/api/v1/marketplace/packages');
export const getPackageInstalls = () => get<PackageInstall[]>('/api/v1/marketplace/installs');
export const installPackage = (packageId: string, version: string) => send<PackageInstall>('POST', `/api/v1/marketplace/packages/${id(packageId)}/install`, { version });
export const uninstallPackage = (packageId: string) => remove(`/api/v1/marketplace/packages/${id(packageId)}/install`, 'Uninstalling failed');
export const savePackage = (pkg: MarketPackage) => send<MarketPackage>('PUT', `/api/v1/marketplace/packages/${id(pkg.id)}`, pkg);
export const deletePackage = (packageId: string) => remove(`/api/v1/marketplace/packages/${id(packageId)}`, 'Deleting the package failed');
