import type { Page, Route } from '@playwright/test';

// Mock of the office registry API (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md) for the headquarters
// specs. Every record carries its tenant and workspace; names are plainly fake test data.
export interface HqTenant { tenantId: string; workspaceId: string; label: string }
export const TENANT_A: HqTenant = { tenantId: 'tenant_local', workspaceId: 'workspace_foundation', label: 'Alpha' };
export const TENANT_B: HqTenant = { tenantId: 'tenant_other', workspaceId: 'workspace_other', label: 'Bravo' };
const stamp = '2026-10-20T08:00:00Z';

export interface HqFixtureOptions { rooms?: number; desks?: number; agentIds?: string[] }

export function hqRegistry(tenant: HqTenant, options: HqFixtureOptions = {}) {
  const base = (id: string) => ({ id, revision: 1, tenant_id: tenant.tenantId, workspace_id: tenant.workspaceId, created_at: stamp, updated_at: stamp });
  const roomCount = options.rooms ?? 3;
  const kinds = ['office', 'meeting', 'reception'] as const;
  const rooms = Array.from({ length: roomCount }, (_, i) => ({
    ...base(`${tenant.label}-room-${i}`), floor_id: `${tenant.label}-floor-0`, name: `${tenant.label} Room ${i + 1}`,
    kind: kinds[i % kinds.length], capacity: 6, scene_x: (i % 5) * 7, scene_z: Math.floor(i / 5) * 7, scene_width: 6, scene_depth: 6,
  }));
  const deskCount = options.desks ?? 2;
  const agents = options.agentIds ?? [];
  const desks = Array.from({ length: deskCount }, (_, i) => ({
    ...base(`${tenant.label}-desk-${i}`), room_id: rooms[i % rooms.length].id, label: `${tenant.label} Desk ${i + 1}`, agent_id: agents[i] ?? null,
  }));
  return {
    buildings: [{ ...base(`${tenant.label}-building`), name: `${tenant.label} Headquarters`, timezone: 'Asia/Dubai', latitude: null, longitude: null }],
    floors: [{ ...base(`${tenant.label}-floor-0`), building_id: `${tenant.label}-building`, level: 0, name: `${tenant.label} Ground` }],
    rooms, desks,
  };
}

export interface HqMock { requests: { path: string; workspace: string | null; tenant: string | null }[]; setTenant(t: HqTenant): void }

/** Serves GET /api/v1/office-spaces/* for the tenant named by the request headers (x-tenant-id, else tenant A). */
export async function mockOfficeSpaces(
  page: Page,
  options: { registries?: Record<string, ReturnType<typeof hqRegistry>>; hold?: (tenantId: string, kind: string) => Promise<void> | undefined } = {},
): Promise<HqMock> {
  const registries = options.registries ?? { [TENANT_A.tenantId]: hqRegistry(TENANT_A), [TENANT_B.tenantId]: hqRegistry(TENANT_B) };
  const mock: HqMock = { requests: [], setTenant: () => undefined };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)(\?.*)?$/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const headers = await request.allHeaders();
    const tenant = headers['x-tenant-id'] ?? TENANT_A.tenantId;
    mock.requests.push({ path: url.pathname, workspace: headers['x-workspace-id'] ?? null, tenant });
    const kind = url.pathname.split('/').pop() as 'buildings' | 'floors' | 'rooms' | 'desks';
    await options.hold?.(tenant, kind);
    const registry = registries[tenant];
    if (!registry) return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'No access.' } }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: registry[kind], next_cursor: null }) });
  });
  return mock;
}
