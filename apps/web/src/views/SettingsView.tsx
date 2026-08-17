import type { TenantContext } from '@anum/contracts';
import { Boxes, Info, KeyRound, LogOut, PlugZap, Server } from 'lucide-react';
import { isOidcEnabled, logout } from '../lib/auth';

const API_BASE_URL = (import.meta.env.VITE_ANUM_API_URL as string | undefined) ?? 'http://localhost:8000';

const INTEGRATION_CATEGORIES = [
  { label: 'REST integrations', description: 'Standard web APIs (e.g. GitHub, calendars, storage).' },
  { label: 'MCP integrations', description: 'Structured tool ecosystems via mediated adapters.' },
  { label: 'Webhooks', description: 'Inbound events from external systems.' },
  { label: 'Browser / desktop tools', description: 'User-local actions, planned for later phases.' },
];

interface IdentityRowProps {
  label: string;
  value: string;
}

function IdentityRow({ label, value }: IdentityRowProps) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 160px) minmax(0, 1fr)',
        gap: 'var(--space-3)',
        padding: 'var(--space-2) 0',
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      <dt
        style={{
          color: 'var(--color-text-muted)',
          fontSize: 'var(--text-sm)',
          fontWeight: 700,
        }}
      >
        {label}
      </dt>
      <dd
        style={{
          margin: 0,
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-sm)',
          color: 'var(--color-text)',
          wordBreak: 'break-all',
        }}
      >
        {value}
      </dd>
    </div>
  );
}

export default function SettingsView({ tenantContext }: { tenantContext: TenantContext }) {
  return (
    <div>
      <div className="viewHeader">
        <div>
          <p className="eyebrow">Reference</p>
          <h2>Identity, integrations &amp; environment</h2>
        </div>
      </div>

      {/* Identity & tenant */}
      <section className="card" aria-labelledby="settings-identity-heading">
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
          <KeyRound size={20} aria-hidden="true" style={{ color: 'var(--color-accent-500)', flex: '0 0 auto', marginTop: 2 }} />
          <div style={{ flex: '1 1 auto' }}>
            <h3 id="settings-identity-heading">Identity &amp; tenant</h3>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-1)' }}>
              {isOidcEnabled ? (
                <>
                  Signed in via Keycloak (OIDC) — this identity comes from your validated access
                  token&apos;s <code>tenant_id</code> / <code>workspace_id</code> / <code>sub</code> /{' '}
                  <code>roles</code> claims, the same claims the API independently validates
                  server-side (see <code>anum_api/oidc_auth.py</code>). This panel is a read-only
                  display of that token, not a separate source of truth.
                </>
              ) : (
                <>
                  ANUM Phase 1 uses stub <code>x-tenant-id</code> / <code>x-workspace-id</code> /{' '}
                  <code>x-user-id</code> / <code>x-user-roles</code> request headers to establish
                  identity — there is no verification on this deployment. Set{' '}
                  <code>VITE_ANUM_AUTH_MODE=oidc</code> (build-time) to switch to real Keycloak login.
                </>
              )}
            </p>

            <dl style={{ margin: 'var(--space-4) 0 0' }}>
              <IdentityRow label="Tenant ID" value={tenantContext.tenantId} />
              <IdentityRow label="Workspace ID" value={tenantContext.workspaceId} />
              <IdentityRow label="User ID" value={tenantContext.userId} />
              <IdentityRow label="Roles" value={tenantContext.roles.join(', ') || '(none)'} />
            </dl>

            {isOidcEnabled && (
              <div className="actions" style={{ marginTop: 'var(--space-4)' }}>
                <button type="button" className="secondary" onClick={() => logout()}>
                  <LogOut size={16} aria-hidden="true" /> Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Integrations */}
      <section className="panel" aria-labelledby="settings-integrations-heading">
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
          <PlugZap size={20} aria-hidden="true" style={{ color: 'var(--color-accent-500)', flex: '0 0 auto', marginTop: 2 }} />
          <div style={{ flex: '1 1 auto' }}>
            <h3 id="settings-integrations-heading">Integrations</h3>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-1)' }}>
              The backend does not yet expose an integrations API. Nothing below is connected or configurable —
              this is a preview of the categories ANUM intends to support.
            </p>
          </div>
        </div>

        <div className="emptyState" style={{ marginTop: 'var(--space-4)' }}>
          <Boxes size={28} aria-hidden="true" />
          <strong style={{ fontSize: 'var(--text-base)' }}>No integrations connected</strong>
          <p style={{ margin: 0, maxWidth: 480 }}>
            Integration management (REST, MCP, webhooks, credential scoping and revocation) is on the roadmap but
            not implemented. There is nothing to connect or manage here yet.
          </p>
        </div>

        <ul
          style={{
            listStyle: 'none',
            margin: 'var(--space-4) 0 0',
            padding: 0,
            display: 'grid',
            gap: 'var(--space-2)',
          }}
        >
          {INTEGRATION_CATEGORIES.map((category) => (
            <li
              key={category.label}
              className="listRow"
              style={{ opacity: 0.65 }}
              aria-disabled="true"
            >
              <Boxes size={18} aria-hidden="true" />
              <div style={{ flex: '1 1 auto' }}>
                <strong>{category.label}</strong>
                <p>{category.description}</p>
              </div>
              <span className="pill--info" style={{ flex: '0 0 auto' }}>
                Coming soon
              </span>
            </li>
          ))}
        </ul>
      </section>

      {/* Environment */}
      <section className="panel" aria-labelledby="settings-environment-heading">
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)' }}>
          <Server size={20} aria-hidden="true" style={{ color: 'var(--color-accent-500)', flex: '0 0 auto', marginTop: 2 }} />
          <div style={{ flex: '1 1 auto' }}>
            <h3 id="settings-environment-heading">Environment</h3>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-1)' }}>
              The API base URL this web app is configured to call.
            </p>
            <dl style={{ margin: 'var(--space-4) 0 0' }}>
              <IdentityRow label="API base URL" value={API_BASE_URL} />
            </dl>
          </div>
        </div>
      </section>

      <div className="notice" style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
        <Info size={16} aria-hidden="true" style={{ flex: '0 0 auto', marginTop: 2 }} />
        <span>
          This view only reflects information that is actually available today (the tenant context passed into the
          app and the configured API URL). Nothing here calls a settings, integrations, or identity endpoint,
          because none exists yet.
        </span>
      </div>
    </div>
  );
}
