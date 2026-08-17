import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Approval, Task, TenantContext } from '@anum/contracts';
import ApprovalsView from '../ApprovalsView';
import { ApiError, decideApproval, listApprovals, type ApprovalDecisionResult } from '../../lib/api';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    listApprovals: vi.fn(),
    decideApproval: vi.fn(),
  };
});

const tenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: 'appr_1',
    taskId: 'task_1',
    action: 'Send email to customer',
    riskLevel: 'medium',
    status: 'pending',
    reason: 'Contains PII',
    createdAt: '2026-08-10T09:00:00.000Z',
    ...overrides,
  };
}

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task_1',
    title: 'Notify customer',
    prompt: 'Notify the customer about their refund',
    status: 'waiting_approval',
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    createdAt: '2026-08-10T09:00:00.000Z',
    updatedAt: '2026-08-10T09:00:00.000Z',
    ...overrides,
  };
}

function getSectionByHeading(name: RegExp): HTMLElement {
  const heading = screen.getByRole('heading', { name });
  const section = heading.closest('section');
  if (!section) throw new Error(`No <section> ancestor for heading matching ${name}`);
  return section as HTMLElement;
}

afterEach(() => {
  vi.resetAllMocks();
});

describe('ApprovalsView', () => {
  it('shows a loading state while approvals are being fetched', async () => {
    let resolveApprovals!: (approvals: Approval[]) => void;
    const pending = new Promise<Approval[]>((resolve) => {
      resolveApprovals = resolve;
    });
    vi.mocked(listApprovals).mockReturnValue(pending);

    render(<ApprovalsView tenantContext={tenantContext} />);

    expect(screen.getByRole('button', { name: /refresh/i })).toBeDisabled();
    expect(screen.queryByText(/nothing is waiting/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /needs your decision/i })).not.toBeInTheDocument();

    resolveApprovals([]);
    await screen.findByText(/nothing is waiting/i);
  });

  it('renders an empty state when there are no approvals', async () => {
    vi.mocked(listApprovals).mockResolvedValue([]);

    render(<ApprovalsView tenantContext={tenantContext} />);

    expect(await screen.findByText(/nothing is waiting on your review/i)).toBeInTheDocument();
  });

  it('separates pending approvals (with actions) from decided ones (without)', async () => {
    const pendingApproval = makeApproval({ id: 'appr_pending', action: 'Deploy to prod', status: 'pending' });
    const approvedApproval = makeApproval({ id: 'appr_approved', action: 'Refund customer', status: 'approved' });
    const rejectedApproval = makeApproval({ id: 'appr_rejected', action: 'Delete account', status: 'rejected' });
    vi.mocked(listApprovals).mockResolvedValue([pendingApproval, approvedApproval, rejectedApproval]);

    render(<ApprovalsView tenantContext={tenantContext} />);

    await screen.findByRole('heading', { name: /needs your decision/i });
    const pendingSection = getSectionByHeading(/needs your decision/i);
    const historySection = getSectionByHeading(/decision history/i);

    expect(within(pendingSection).getByText('Deploy to prod')).toBeInTheDocument();
    expect(within(pendingSection).getByRole('button', { name: /^approve$/i })).toBeInTheDocument();
    expect(within(pendingSection).getByRole('button', { name: /^reject$/i })).toBeInTheDocument();

    expect(within(historySection).getByText('Refund customer')).toBeInTheDocument();
    expect(within(historySection).getByText('Delete account')).toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });

  it('calls decideApproval with approve and moves the row into decision history', async () => {
    const pendingApproval = makeApproval({ id: 'appr_1', status: 'pending' });
    vi.mocked(listApprovals).mockResolvedValue([pendingApproval]);
    vi.mocked(decideApproval).mockResolvedValue({
      task: makeTask(),
      run: null,
      approval: { ...pendingApproval, status: 'approved' },
    });

    const user = userEvent.setup();
    render(<ApprovalsView tenantContext={tenantContext} />);

    const approveButton = await screen.findByRole('button', { name: /^approve$/i });
    await user.click(approveButton);

    expect(decideApproval).toHaveBeenCalledWith('appr_1', 'approve');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /decision history/i })).toBeInTheDocument();
    });
    const historySection = getSectionByHeading(/decision history/i);
    expect(within(historySection).getByText(/approved/i)).toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
  });

  it('calls decideApproval with reject and moves the row into decision history', async () => {
    const pendingApproval = makeApproval({ id: 'appr_2', status: 'pending' });
    vi.mocked(listApprovals).mockResolvedValue([pendingApproval]);
    vi.mocked(decideApproval).mockResolvedValue({
      task: makeTask(),
      run: null,
      approval: { ...pendingApproval, status: 'rejected' },
    });

    const user = userEvent.setup();
    render(<ApprovalsView tenantContext={tenantContext} />);

    const rejectButton = await screen.findByRole('button', { name: /^reject$/i });
    await user.click(rejectButton);

    expect(decideApproval).toHaveBeenCalledWith('appr_2', 'reject');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /decision history/i })).toBeInTheDocument();
    });
    const historySection = getSectionByHeading(/decision history/i);
    expect(within(historySection).getByText(/rejected/i)).toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });

  it('disables both action buttons for a row while its decision request is in flight', async () => {
    const pendingApproval = makeApproval({ id: 'appr_1', status: 'pending' });
    vi.mocked(listApprovals).mockResolvedValue([pendingApproval]);
    let resolveDecision!: (result: ApprovalDecisionResult) => void;
    const pendingDecision = new Promise<ApprovalDecisionResult>((resolve) => {
      resolveDecision = resolve;
    });
    vi.mocked(decideApproval).mockReturnValue(pendingDecision);

    const user = userEvent.setup();
    render(<ApprovalsView tenantContext={tenantContext} />);

    const approveButton = await screen.findByRole('button', { name: /^approve$/i });
    await user.click(approveButton);

    const busyApprove = await screen.findByRole('button', { name: /approving/i });
    const busyReject = screen.getByRole('button', { name: /rejecting/i });
    expect(busyApprove).toBeDisabled();
    expect(busyReject).toBeDisabled();

    resolveDecision({
      task: makeTask(),
      run: null,
      approval: { ...pendingApproval, status: 'approved' },
    });

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /approving/i })).not.toBeInTheDocument();
    });
  });

  it('shows an inline conflict notice and refreshes when decideApproval returns a 409', async () => {
    const pendingApproval = makeApproval({ id: 'appr_1', status: 'pending' });
    const refreshedApproval = makeApproval({ id: 'appr_1', status: 'rejected' });
    vi.mocked(listApprovals)
      .mockResolvedValueOnce([pendingApproval])
      .mockResolvedValueOnce([refreshedApproval]);
    vi.mocked(decideApproval).mockRejectedValueOnce(
      new ApiError('Approval already decided', 409, 'conflict', 'corr-1'),
    );

    const user = userEvent.setup();
    render(<ApprovalsView tenantContext={tenantContext} />);

    const approveButton = await screen.findByRole('button', { name: /^approve$/i });
    await user.click(approveButton);

    expect(
      await screen.findByText(/someone else already decided this request/i),
    ).toBeInTheDocument();
    await waitFor(() => expect(listApprovals).toHaveBeenCalledTimes(2));

    // The row should reflect the refreshed (decided) state, not a stale enabled/disabled button.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /decision history/i })).toBeInTheDocument();
    });
    const historySection = getSectionByHeading(/decision history/i);
    expect(within(historySection).getByText(/rejected/i)).toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
    expect(within(historySection).queryByRole('button', { name: /reject/i })).not.toBeInTheDocument();
  });
});
