import { afterEach, describe, expect, it, vi } from 'vitest';

// `updateToken`'s resolved/rejected behavior is controlled per-test via this
// indirection, since the mock Keycloak instance itself is created lazily
// inside auth.ts (module-private) and tests have no direct reference to it.
let updateTokenImpl: () => Promise<boolean> = async () => true;

const mockInit = vi.fn();
const mockLogin = vi.fn();
const mockLogout = vi.fn();

class MockKeycloak {
  token: string | undefined = undefined;
  tokenParsed: Record<string, unknown> | undefined = undefined;
  authenticated = false;

  constructor(public config: unknown) {}

  init = mockInit.mockImplementation(async () => {
    this.token = 'initial-access-token';
    this.tokenParsed = {
      sub: 'user-1',
      tenant_id: 'tenant_x',
      workspace_id: 'workspace_x',
      roles: ['owner', 'member'],
    };
    this.authenticated = true;
    return true;
  });

  updateToken = vi.fn(async (_minValidity: number) => {
    const refreshed = await updateTokenImpl();
    if (refreshed) {
      this.token = 'refreshed-access-token';
    }
    return refreshed;
  });

  login = mockLogin.mockImplementation(async () => {});
  logout = mockLogout.mockImplementation(async () => {});
}

vi.mock('keycloak-js', () => ({ default: MockKeycloak }));

async function loadAuthModule(authMode: string | undefined) {
  vi.resetModules();
  vi.unstubAllEnvs();
  if (authMode !== undefined) {
    vi.stubEnv('VITE_ANUM_AUTH_MODE', authMode);
  }
  updateTokenImpl = async () => true;
  return import('../auth');
}

afterEach(() => {
  vi.unstubAllEnvs();
  mockInit.mockClear();
  mockLogin.mockClear();
  mockLogout.mockClear();
});

describe('stub_headers mode (default - no VITE_ANUM_AUTH_MODE set)', () => {
  it('is inert: everything is a safe no-op, Keycloak is never constructed', async () => {
    const auth = await loadAuthModule(undefined);

    expect(auth.authMode).toBe('stub_headers');
    expect(auth.isOidcEnabled).toBe(false);

    await expect(auth.initAuth()).resolves.toBe(true);
    expect(mockInit).not.toHaveBeenCalled();

    await expect(auth.getValidToken()).resolves.toBeNull();
    expect(auth.getTenantContext()).toBeNull();

    expect(() => auth.logout()).not.toThrow();
    expect(mockLogout).not.toHaveBeenCalled();
  });

  it('treats any value other than exactly "oidc" as stub_headers', async () => {
    const auth = await loadAuthModule('OIDC');
    expect(auth.isOidcEnabled).toBe(false);
  });
});

describe('oidc mode (VITE_ANUM_AUTH_MODE=oidc)', () => {
  it('initAuth calls Keycloak.init with login-required + PKCE S256, and only once', async () => {
    const auth = await loadAuthModule('oidc');
    expect(auth.isOidcEnabled).toBe(true);

    await auth.initAuth();
    await auth.initAuth();

    expect(mockInit).toHaveBeenCalledTimes(1);
    expect(mockInit).toHaveBeenCalledWith({
      onLoad: 'login-required',
      pkceMethod: 'S256',
      checkLoginIframe: false,
    });
  });

  it('getValidToken refreshes the token and returns it', async () => {
    const auth = await loadAuthModule('oidc');
    await auth.initAuth();

    const token = await auth.getValidToken();

    expect(token).toBe('refreshed-access-token');
  });

  it('getValidToken re-authenticates and returns null when refresh fails', async () => {
    const auth = await loadAuthModule('oidc');
    await auth.initAuth();
    updateTokenImpl = async () => {
      throw new Error('refresh token expired');
    };

    const token = await auth.getValidToken();

    expect(token).toBeNull();
    expect(mockLogin).toHaveBeenCalledTimes(1);
  });

  it('getTenantContext maps token claims the same way the backend does', async () => {
    const auth = await loadAuthModule('oidc');
    await auth.initAuth();

    expect(auth.getTenantContext()).toEqual({
      tenantId: 'tenant_x',
      workspaceId: 'workspace_x',
      userId: 'user-1',
      roles: ['owner', 'member'],
    });
  });

  it('getTenantContext returns null before initAuth has populated a token', async () => {
    const auth = await loadAuthModule('oidc');
    expect(auth.getTenantContext()).toBeNull();
  });

  it('logout calls Keycloak.logout with a redirect back to this origin', async () => {
    const auth = await loadAuthModule('oidc');
    await auth.initAuth();

    auth.logout();

    expect(mockLogout).toHaveBeenCalledWith({ redirectUri: window.location.origin });
  });
});
