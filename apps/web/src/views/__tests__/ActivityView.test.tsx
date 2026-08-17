import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AgentRun, DomainEvent, TenantContext } from '@anum/contracts';
import ActivityView from '../ActivityView';
import { ApiError, getAgentRun, listEvents } from '../../lib/api';
import { useEventStream } from '../../lib/useEventStream';
import type { UseEventStreamResult } from '../../lib/useEventStream';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    listEvents: vi.fn(),
    getAgentRun: vi.fn(),
  };
});

// The live SSE hook is exercised on its own in
// lib/__tests__/useEventStream.test.ts - here it's mocked so ActivityView's
// tests stay deterministic and don't make a real network request.
vi.mock('../../lib/useEventStream', () => ({
  useEventStream: vi.fn(),
}));

const mockedListEvents = vi.mocked(listEvents);
const mockedGetAgentRun = vi.mocked(getAgentRun);
const mockedUseEventStream = vi.mocked(useEventStream);

const tenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

let eventCounter = 0;
function makeEvent(overrides: Partial<DomainEvent> = {}): DomainEvent {
  eventCounter += 1;
  return {
    id: `evt_${eventCounter}`,
    type: 'task.created',
    version: 1,
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    subject: `task_${eventCounter}`,
    correlationId: `corr_${eventCounter}`,
    createdAt: '2026-08-17T10:00:00.000Z',
    payload: {},
    ...overrides,
  };
}

function makeRun(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run_1',
    taskId: 'task_1',
    status: 'completed',
    steps: [
      { id: 'step_1', type: 'model_call', summary: 'Called the model', createdAt: '2026-08-17T10:00:01.000Z' },
    ],
    ...overrides,
  };
}

function liveResult(overrides: Partial<UseEventStreamResult> = {}): UseEventStreamResult {
  return { status: 'connecting', events: [], error: null, ...overrides };
}

describe('ActivityView', () => {
  beforeEach(() => {
    eventCounter = 0;
    vi.clearAllMocks();
    mockedUseEventStream.mockReturnValue(liveResult());
  });

  it('shows a loading state while listEvents is pending', async () => {
    let resolvePromise: (events: DomainEvent[]) => void = () => {};
    mockedListEvents.mockReturnValue(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }),
    );

    const { container } = render(<ActivityView tenantContext={tenantContext} />);

    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0);

    resolvePromise([]);
    await waitFor(() => expect(container.querySelectorAll('.skeleton').length).toBe(0));
  });

  it('shows an empty state when listEvents resolves to an empty list', async () => {
    mockedListEvents.mockResolvedValue([]);

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByText(/no activity yet/i)).toBeInTheDocument();
  });

  it('renders events with a human-readable description derived from type/payload', async () => {
    const createdEvent = makeEvent({
      type: 'task.created',
      payload: { title: 'Ship the release notes' },
    });
    const failedEvent = makeEvent({
      type: 'agent_run.failed',
      payload: { reason: 'tool timed out' },
    });
    mockedListEvents.mockResolvedValue([createdEvent, failedEvent]);

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByText('Task created: Ship the release notes')).toBeInTheDocument();
    expect(screen.getByText('Agent run failed: tool timed out')).toBeInTheDocument();
  });

  it('narrows visible events to the selected category filter', async () => {
    const user = userEvent.setup();
    const taskEvent = makeEvent({ type: 'task.created', payload: { title: 'Task alpha' } });
    const approvalEvent = makeEvent({ type: 'approval.requested', payload: { action: 'delete_file' } });
    mockedListEvents.mockResolvedValue([taskEvent, approvalEvent]);

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByText('Task created: Task alpha')).toBeInTheDocument();
    expect(screen.getByText('Approval requested for delete_file')).toBeInTheDocument();

    const filterGroup = screen.getByRole('group', { name: /filter activity by category/i });
    const approvalsFilterButton = within(filterGroup).getByRole('button', { name: /approvals/i });
    await user.click(approvalsFilterButton);

    expect(screen.queryByText('Task created: Task alpha')).not.toBeInTheDocument();
    expect(screen.getByText('Approval requested for delete_file')).toBeInTheDocument();
  });

  it('drills into an agent_run event with a derivable run id and renders its steps', async () => {
    const user = userEvent.setup();
    const runEvent = makeEvent({
      type: 'agent_run.completed',
      subject: 'run_from_subject',
      payload: {},
    });
    mockedListEvents.mockResolvedValue([runEvent]);
    mockedGetAgentRun.mockResolvedValue(makeRun({ id: 'run_from_subject' }));

    render(<ActivityView tenantContext={tenantContext} />);

    const drillButton = await screen.findByRole('button', { name: /view run details/i });
    await user.click(drillButton);

    await waitFor(() => expect(mockedGetAgentRun).toHaveBeenCalledWith('run_from_subject'));
    expect(await screen.findByText('Called the model')).toBeInTheDocument();
  });

  it('renders no drill-down control for an event with no derivable run id', async () => {
    const runEventWithoutId = makeEvent({
      type: 'agent_run.started',
      subject: '',
      payload: {},
    });
    mockedListEvents.mockResolvedValue([runEventWithoutId]);

    render(<ActivityView tenantContext={tenantContext} />);

    await screen.findByText('Agent run started');
    expect(screen.queryByRole('button', { name: /view run details/i })).not.toBeInTheDocument();
    expect(mockedGetAgentRun).not.toHaveBeenCalled();
  });

  it('shows the error UI with the ApiError message when listEvents fails', async () => {
    mockedListEvents.mockRejectedValue(new ApiError('Events service unavailable', 500));

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByText('Events service unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('shows a "Live" indicator when the SSE stream is open', async () => {
    mockedListEvents.mockResolvedValue([]);
    mockedUseEventStream.mockReturnValue(liveResult({ status: 'open' }));

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByRole('status', { name: /realtime stream status: live/i })).toHaveTextContent('Live');
  });

  it('shows a "Reconnecting" indicator while the SSE stream is (re)connecting', async () => {
    mockedListEvents.mockResolvedValue([]);
    mockedUseEventStream.mockReturnValue(liveResult({ status: 'connecting' }));

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByRole('status')).toHaveTextContent(/reconnecting/i);
  });

  it('shows an "unavailable" indicator when the SSE stream errors, without affecting the REST-loaded feed', async () => {
    const restEvent = makeEvent({ type: 'task.created', payload: { title: 'Loaded via REST' } });
    mockedListEvents.mockResolvedValue([restEvent]);
    mockedUseEventStream.mockReturnValue(liveResult({ status: 'error', error: 'stream failed' }));

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByRole('status')).toHaveTextContent(/unavailable/i);
    expect(screen.getByText('Task created: Loaded via REST')).toBeInTheDocument();
  });

  it('merges a new event pushed over the live stream into the feed without duplicating REST-loaded events', async () => {
    const restEvent = makeEvent({ id: 'evt_rest', type: 'task.created', payload: { title: 'From REST' } });
    const duplicateOfRestEvent = makeEvent({ ...restEvent }); // same id as restEvent - must not duplicate
    const liveOnlyEvent = makeEvent({ id: 'evt_live', type: 'task.completed', payload: {} });
    mockedListEvents.mockResolvedValue([restEvent]);
    mockedUseEventStream.mockReturnValue(
      liveResult({ status: 'open', events: [duplicateOfRestEvent, liveOnlyEvent] }),
    );

    render(<ActivityView tenantContext={tenantContext} />);

    expect(await screen.findByText('Task created: From REST')).toBeInTheDocument();
    expect(screen.getByText('Task completed')).toBeInTheDocument();
    expect(screen.getAllByText('Task created: From REST')).toHaveLength(1);
  });
});
