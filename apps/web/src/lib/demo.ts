// Sales demo workspace (docs/demo-workspace.md): GET /api/v1/demo and POST /api/v1/demo/reset.
// Owner only; the API answers 403 to anyone else.
import { apiRequest } from './api';

export type DemoStatus = {
  workspaceId: string;
  name: string;
  exists: boolean;
  records: number;
  counts: Record<string, number>;
  filledAt: string | null;
  sendsBlocked: boolean;
};

type ApiDemoStatus = {
  workspace_id: string;
  name: string;
  exists: boolean;
  records: number;
  counts: Record<string, number>;
  filled_at: string | null;
  sends_blocked: boolean;
};

/** The Demo workspace's id prefix: the API reserves it for the tenant's demo workspace. */
export const DEMO_WORKSPACE_PREFIX = 'ws_demo_';
export const isDemoWorkspace = (workspaceId: string): boolean => workspaceId.startsWith(DEMO_WORKSPACE_PREFIX);

export function mapDemoStatus(raw: ApiDemoStatus): DemoStatus {
  return {
    workspaceId: raw.workspace_id,
    name: raw.name,
    exists: raw.exists,
    records: raw.records,
    counts: raw.counts ?? {},
    filledAt: raw.filled_at,
    sendsBlocked: raw.sends_blocked,
  };
}

export async function getDemoStatus(): Promise<DemoStatus> {
  return mapDemoStatus(await apiRequest<ApiDemoStatus>('/api/v1/demo', { method: 'GET' }));
}

export async function resetDemo(): Promise<DemoStatus> {
  return mapDemoStatus(await apiRequest<ApiDemoStatus>('/api/v1/demo/reset', { method: 'POST' }));
}
