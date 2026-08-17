import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AgentRun, DomainEvent, Task, TenantContext } from '@anum/contracts';
import TasksView from '../TasksView';
import { ApiError, createAndRunTask, getTask, listEvents } from '../../lib/api';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    listEvents: vi.fn(),
    getTask: vi.fn(),
    createAndRunTask: vi.fn(),
    cancelTask: vi.fn(),
  };
});

const tenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task_1',
    title: 'Summarize Q3 report',
    prompt: 'Summarize the Q3 financial report',
    status: 'completed',
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    createdAt: '2026-08-10T09:00:00.000Z',
    updatedAt: '2026-08-10T09:05:00.000Z',
    ...overrides,
  };
}

function makeEvent(overrides: Partial<DomainEvent> = {}): DomainEvent {
  return {
    id: 'evt_1',
    type: 'task.created',
    version: 1,
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    subject: 'task_1',
    correlationId: 'corr_1',
    createdAt: '2026-08-10T09:00:00.000Z',
    payload: {},
    ...overrides,
  };
}

afterEach(() => {
  vi.resetAllMocks();
});

describe('TasksView', () => {
  it('shows a loading state while the initial events request is pending', async () => {
    let resolveEvents!: (events: DomainEvent[]) => void;
    const pending = new Promise<DomainEvent[]>((resolve) => {
      resolveEvents = resolve;
    });
    vi.mocked(listEvents).mockReturnValue(pending);

    render(<TasksView tenantContext={tenantContext} />);

    expect(screen.getByRole('button', { name: /refresh/i })).toBeDisabled();
    expect(screen.queryByText(/no tasks yet/i)).not.toBeInTheDocument();

    resolveEvents([]);
    await screen.findByText(/no tasks yet/i);
  });

  it('renders an empty state when there are no task.created events', async () => {
    vi.mocked(listEvents).mockResolvedValue([
      makeEvent({ id: 'evt_other', type: 'task.completed', subject: 'task_1' }),
    ]);

    render(<TasksView tenantContext={tenantContext} />);

    expect(await screen.findByText(/no tasks yet/i)).toBeInTheDocument();
  });

  it('renders task rows once events and their tasks resolve', async () => {
    const events = [
      makeEvent({ id: 'evt_1', subject: 'task_1', createdAt: '2026-08-10T09:00:00.000Z' }),
      makeEvent({ id: 'evt_2', subject: 'task_2', createdAt: '2026-08-10T10:00:00.000Z' }),
    ];
    const task1 = makeTask({ id: 'task_1', title: 'Summarize Q3 report', status: 'completed' });
    const task2 = makeTask({ id: 'task_2', title: 'Rotate API keys', status: 'running' });
    vi.mocked(listEvents).mockResolvedValue(events);
    vi.mocked(getTask).mockImplementation(async (id: string) => {
      if (id === 'task_1') return task1;
      if (id === 'task_2') return task2;
      throw new Error(`unexpected task id: ${id}`);
    });

    render(<TasksView tenantContext={tenantContext} />);

    expect(await screen.findByText('Summarize Q3 report')).toBeInTheDocument();
    expect(screen.getByText('Rotate API keys')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
  });

  it('submits the composer, calls createAndRunTask with the prompt, and shows the new task', async () => {
    vi.mocked(listEvents).mockResolvedValue([]);
    const newTask = makeTask({
      id: 'task_new',
      title: 'Web task',
      prompt: 'Investigate billing spike',
      status: 'queued',
    });
    const newRun: AgentRun = { id: 'run_1', taskId: 'task_new', status: 'queued', steps: [] };
    vi.mocked(createAndRunTask).mockResolvedValue({ task: newTask, run: newRun, approval: null });

    const user = userEvent.setup();
    render(<TasksView tenantContext={tenantContext} />);

    await screen.findByText(/no tasks yet/i);

    const textarea = screen.getByRole('textbox', { name: /prompt/i });
    await user.type(textarea, 'Investigate billing spike');
    await user.click(screen.getByRole('button', { name: /run task/i }));

    expect(createAndRunTask).toHaveBeenCalledWith('Investigate billing spike');
    expect(await screen.findByText('Web task')).toBeInTheDocument();
    expect(screen.getByText('Queued')).toBeInTheDocument();
  });

  it('disables Run task until the prompt has non-whitespace text', async () => {
    vi.mocked(listEvents).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<TasksView tenantContext={tenantContext} />);

    await screen.findByText(/no tasks yet/i);

    const runButton = screen.getByRole('button', { name: /run task/i });
    const textarea = screen.getByRole('textbox', { name: /prompt/i });

    expect(runButton).toBeDisabled();

    await user.type(textarea, '   ');
    expect(runButton).toBeDisabled();

    await user.type(textarea, 'Do something useful');
    expect(runButton).toBeEnabled();
  });

  it('shows the ApiError message when the initial events request fails', async () => {
    vi.mocked(listEvents).mockRejectedValue(
      new ApiError('Tenant header missing', 400, 'bad_request', 'corr-42'),
    );

    render(<TasksView tenantContext={tenantContext} />);

    expect(await screen.findByText('Tenant header missing')).toBeInTheDocument();
  });
});
