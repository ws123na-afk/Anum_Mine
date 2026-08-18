import { render, screen, within } from '@testing-library/react';
import type { TenantContext } from '@anum/contracts';
import SettingsView from '../SettingsView';

const tenantContext: TenantContext = {
  tenantId: 'tenant_acme_42',
  workspaceId: 'workspace_rocket',
  userId: 'user_jane_doe',
  roles: ['owner', 'reviewer'],
};

describe('SettingsView', () => {
  it('renders the tenant, workspace, user, and roles from the tenantContext prop', () => {
    render(<SettingsView tenantContext={tenantContext} />);

    expect(screen.getByText(tenantContext.tenantId)).toBeInTheDocument();
    expect(screen.getByText(tenantContext.workspaceId)).toBeInTheDocument();
    expect(screen.getByText(tenantContext.userId)).toBeInTheDocument();
    expect(screen.getByText(tenantContext.roles.join(', '))).toBeInTheDocument();
  });

  it('renders "(none)" for roles when the roles array is empty', () => {
    render(<SettingsView tenantContext={{ ...tenantContext, roles: [] }} />);

    expect(screen.getByText('(none)')).toBeInTheDocument();
  });

  it('renders the integrations section as a clearly-labeled placeholder with no live controls', () => {
    render(<SettingsView tenantContext={tenantContext} />);

    const integrationsHeading = screen.getByRole('heading', { name: 'Integrations', level: 3 });
    const integrationsSection = integrationsHeading.closest('section') as HTMLElement;
    expect(integrationsSection).not.toBeNull();

    // Explicit placeholder copy.
    expect(
      within(integrationsSection).getByText(/backend does not yet expose an integrations api/i),
    ).toBeInTheDocument();
    expect(within(integrationsSection).getByText(/no integrations connected/i)).toBeInTheDocument();

    // The listed categories are marked as non-interactive / disabled and labeled "Coming soon".
    const categoryItems = within(integrationsSection).getAllByText(/coming soon/i);
    expect(categoryItems.length).toBeGreaterThan(0);
    for (const label of categoryItems) {
      const row = label.closest('li');
      expect(row).not.toBeNull();
      expect(row).toHaveAttribute('aria-disabled', 'true');
    }

    // No interactive control anywhere in the integrations section claims to actually
    // connect or toggle an integration. The component renders plain <li> rows marked
    // aria-disabled (asserted above) and no buttons/inputs/links at all — if any were
    // ever added, they would have to be disabled.
    const interactiveButtons = within(integrationsSection).queryAllByRole('button');
    for (const element of interactiveButtons) {
      expect(element).toBeDisabled();
    }
    expect(interactiveButtons).toHaveLength(0);
    expect(within(integrationsSection).queryAllByRole('checkbox')).toHaveLength(0);
    expect(within(integrationsSection).queryAllByRole('switch')).toHaveLength(0);
    expect(within(integrationsSection).queryAllByRole('link')).toHaveLength(0);
  });
});
