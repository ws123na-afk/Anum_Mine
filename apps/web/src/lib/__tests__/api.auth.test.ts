import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetValidToken = vi.fn();
let mockIsOidcEnabled = false;

vi.mock('../auth', () => ({
  get isOidcEnabled() {
    return mockIsOidcEnabled;
  },
  getValidToken: mockGetValidToken,
  getTenantContext: vi.fn(() => null),
}));

describe('request() auth-header branching', () => {
  beforeEach(() => {
    vi.resetModules();
    mockGetValidToken.mockReset();
    mockIsOidcEnabled = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the stub tenant headers and no Authorization header by default', async () => {
    mockIsOidcEnabled = false;
    const { request } = await import('../api');

    await request('/api/v1/tasks');

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(headers['x-tenant-id']).toBe('tenant_local');
    expect(headers['x-workspace-id']).toBe('workspace_foundation');
    expect(headers['x-user-id']).toBe('user_local');
    expect(headers['x-user-roles']).toBe('owner,member');
    expect(headers.authorization).toBeUndefined();
  });

  it('sends only a Bearer token when OIDC mode is enabled - no stub headers', async () => {
    mockIsOidcEnabled = true;
    mockGetValidToken.mockResolvedValue('a-real-access-token');
    const { request } = await import('../api');

    await request('/api/v1/tasks');

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(headers.authorization).toBe('Bearer a-real-access-token');
    expect(headers['x-tenant-id']).toBeUndefined();
    expect(headers['x-workspace-id']).toBeUndefined();
    expect(headers['x-user-id']).toBeUndefined();
    expect(headers['x-user-roles']).toBeUndefined();
  });

  it('sends no Authorization header when OIDC mode has no token yet (e.g. re-authenticating)', async () => {
    mockIsOidcEnabled = true;
    mockGetValidToken.mockResolvedValue(null);
    const { request } = await import('../api');

    await request('/api/v1/tasks');

    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(headers.authorization).toBeUndefined();
  });
});
