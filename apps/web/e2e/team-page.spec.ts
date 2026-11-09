import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The Team page tabs from the owner's mockups (docs/team-work.md#team-page): Directory with the member
// panel, Departments, Roles and access, Invitations (owners invite, resend and revoke) and Activity.
const tab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Team sections' }).getByRole('tab', { name: new RegExp(`^${name}`) });
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('the directory lists people with their role and work, filters, and opens a member', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await expect(tab(page, 'Directory')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('region', { name: 'Team at a glance' }).or(page.locator('.finKpis'))).toContainText('Members3');
  const table = page.getByRole('table', { name: 'Directory' });
  await expect(table.getByRole('row', { name: /Ahmed Saleh/ })).toContainText('Sales');
  await page.locator('.tpFilters').getByRole('combobox').nth(1).selectOption('viewer');
  await expect(table.getByRole('row')).toHaveCount(2);
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.getByRole('searchbox', { name: 'Search name, email or job title' }).fill('sales lead');
  await expect(table.getByRole('button', { name: 'Call Ahmed Saleh', exact: true })).toBeVisible();
  await table.getByRole('button', { name: /^Ahmed Saleh/ }).click();
  const panel = page.getByRole('complementary', { name: 'Ahmed Saleh' });
  await expect(panel.getByRole('heading', { name: /What a Member can do/ })).toBeVisible();
  await expect(panel).toContainText('Can change: Calendar, Finance, Tasks.');
  await panel.getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('dialog', { name: 'Edit Ahmed Saleh' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('departments count people and open work; roles show the access matrix', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await tab(page, 'Departments').click();
  const table = page.getByRole('table', { name: 'Departments' });
  await expect(table.getByRole('row', { name: /Operations and warehouse/ })).toContainText('Omar Owner');
  await table.getByRole('button', { name: /Operations and warehouse/ }).click();
  await expect(page.getByRole('complementary', { name: 'Operations and warehouse' })).toContainText('Omar Owner');
  await tab(page, 'Roles and access').click();
  const matrix = page.getByRole('table', { name: 'Access by module and role' });
  await expect(matrix.getByRole('row', { name: /Members and invitations/ })).toContainText('FullNoneNone');
  await expect(matrix.getByRole('row', { name: /Tasks/ })).toContainText('FullEditRead');
});

test('an owner invites, resends and revokes from Invitations', async ({ page }) => {
  await mockWorkspaceApi(page);
  const revoked: string[] = [];
  await page.route('http://localhost:8000/api/v1/workspace-invitations/*/revoke', async (route) => {
    revoked.push(route.request().url());
    await route.fulfill({ json: { id: 'invitation_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'viewer', invitee_user_id: 'user_pending', invitee_email: null, status: 'revoked', created_by_user_id: 'user_local', expires_at: new Date().toISOString(), accepted_by_user_id: null, accepted_at: null, revoked_at: new Date().toISOString(), created_at: new Date().toISOString() } });
  });
  await page.goto('/#team');
  await page.getByRole('button', { name: 'Invite member' }).first().click();
  await expect(tab(page, 'Invitations')).toHaveAttribute('aria-selected', 'true');
  const table = page.getByRole('table', { name: 'Invitations' });
  await expect(table.getByRole('row', { name: /user_pending/ })).toContainText('Pending');
  await table.getByRole('button', { name: 'Revoke' }).click();
  await page.getByRole('alertdialog', { name: 'Revoke this invitation?' }).getByRole('button', { name: 'Revoke' }).click();
  await expect(page.getByText('Revoked the invitation for user_pending.')).toBeVisible();
  expect(revoked).toHaveLength(1);
  await page.getByRole('region', { name: 'Invitations' }).getByRole('button', { name: 'Invite member' }).click();
  await expect(page.getByRole('dialog', { name: 'Invite member' }).getByRole('form', { name: 'Invite someone' }).or(page.getByRole('dialog', { name: 'Invite member' }).locator('form'))).toBeVisible();
});

test('a member without member management is told who sends invitations', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.route('http://localhost:8000/api/v1/workspaces/current/permissions', (route) => route.fulfill({ json: { workspace_id: 'workspace_foundation', role: 'member', organization_admin: false, can_manage_workspace: false, can_create_workspace: false, can_manage_members: false, can_read_audit: false, can_export_audit: false } }));
  await page.route('http://localhost:8000/api/v1/workspace-invitations*', (route) => route.fulfill({ status: 403, json: { detail: 'Only workspace owners can manage invitations.' } }));
  await page.goto('/#team');
  await expect(page.getByRole('button', { name: 'Invite member' })).toHaveCount(0);
  await tab(page, 'Invitations').click();
  await expect(page.getByText('Only workspace owners see and send invitations.').first()).toBeVisible();
  await tab(page, 'Activity').click();
  await expect(page.getByText(/full audit log of access and role changes is shown to workspace owners/)).toBeVisible();
});

test('the Team page reads right to left in Arabic at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await useSavedLocale(page, 'ar');
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('table', { name: 'الدليل' })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('Activity keeps its content after a work item was opened, at 1024 wide', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await mockWorkspaceApi(page);
  await page.goto('/#team');
  await tab(page, 'My work').click();
  await page.getByRole('button', { name: /Count winter tyre stock/ }).click();
  await expect(page.getByRole('complementary', { name: 'Count winter tyre stock' })).toBeVisible();
  await tab(page, 'Activity').click();
  await expect(page.getByText(/Ahmed Saleh created "Count winter tyre stock"/)).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Count winter tyre stock' })).toHaveCount(0);
  await tab(page, 'My work').click();
  await expect(page.getByRole('complementary', { name: 'Count winter tyre stock' })).toBeVisible();
});
