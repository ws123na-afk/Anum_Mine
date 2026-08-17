import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { TenantContext } from '@anum/contracts';
import MemoryView from '../MemoryView';
import { ApiError, createMemory, deleteMemory, listMemories, type MemoryNote } from '../../lib/api';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return {
    ...actual,
    listMemories: vi.fn(),
    createMemory: vi.fn(),
    deleteMemory: vi.fn(),
  };
});

const mockedListMemories = vi.mocked(listMemories);
const mockedCreateMemory = vi.mocked(createMemory);
const mockedDeleteMemory = vi.mocked(deleteMemory);

const tenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

function makeNote(overrides: Partial<MemoryNote> = {}): MemoryNote {
  return {
    id: 'mem_1',
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    taskId: 'task_8f2a1c',
    content: 'Remember the user prefers dark mode.',
    provenance: {
      sourceType: 'user_note',
      sourceId: null,
      createdByUserId: 'user_local',
      createdAt: '2026-08-10T12:00:00.000Z',
      metadata: {},
    },
    retention: {
      kind: 'indefinite',
      expiresAt: null,
    },
    createdAt: '2026-08-10T12:00:00.000Z',
    ...overrides,
  };
}

describe('MemoryView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows a loading state while listMemories is pending', async () => {
    let resolvePromise: (notes: MemoryNote[]) => void = () => {};
    mockedListMemories.mockReturnValue(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }),
    );

    const { container } = render(<MemoryView tenantContext={tenantContext} />);

    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0);

    resolvePromise([]);
    await waitFor(() => expect(container.querySelectorAll('.skeleton').length).toBe(0));
  });

  it('shows an empty state when listMemories resolves to an empty list', async () => {
    mockedListMemories.mockResolvedValue([]);

    render(<MemoryView tenantContext={tenantContext} />);

    expect(
      await screen.findByText(/no memory notes have been recorded for this workspace yet/i),
    ).toBeInTheDocument();
  });

  it('renders notes with content, source type, and created-by info', async () => {
    const note = makeNote();
    mockedListMemories.mockResolvedValue([note]);

    render(<MemoryView tenantContext={tenantContext} />);

    expect(await screen.findByText(note.content)).toBeInTheDocument();
    expect(screen.getByText(note.provenance.sourceType)).toBeInTheDocument();
    expect(screen.getByText(`Created by: ${note.provenance.createdByUserId}`)).toBeInTheDocument();
    expect(screen.getByText(`Task: ${note.taskId}`)).toBeInTheDocument();
  });

  it('re-calls listMemories with updated filters after the debounce window', async () => {
    const user = userEvent.setup();
    mockedListMemories.mockResolvedValue([]);

    render(<MemoryView tenantContext={tenantContext} />);

    await waitFor(() => expect(mockedListMemories).toHaveBeenCalledTimes(1));
    expect(mockedListMemories).toHaveBeenLastCalledWith({});

    const taskIdField = screen.getByLabelText(/filter by task id/i);
    await user.type(taskIdField, 'task_8f2a1c');

    const queryField = screen.getByLabelText(/search content/i);
    await user.type(queryField, 'dark mode');

    const includeExpiredCheckbox = screen.getByLabelText(/include expired notes/i);
    await user.click(includeExpiredCheckbox);

    // The component debounces taskId/query changes by FILTER_DEBOUNCE_MS (300ms) before
    // re-fetching; give waitFor a generous timeout well beyond that window to avoid flakes.
    await waitFor(
      () =>
        expect(mockedListMemories).toHaveBeenLastCalledWith({
          taskId: 'task_8f2a1c',
          query: 'dark mode',
          includeExpired: true,
        }),
      { timeout: 3000 },
    );
  });

  it('submits the new memory note form and calls createMemory with the right shape', async () => {
    const user = userEvent.setup();
    mockedListMemories.mockResolvedValue([]);
    const created = makeNote({
      id: 'mem_new',
      taskId: 'task_new_1',
      content: 'A brand new note',
      provenance: {
        sourceType: 'user_note',
        sourceId: null,
        createdByUserId: 'user_local',
        createdAt: '2026-08-17T00:00:00.000Z',
        metadata: {},
      },
      createdAt: '2026-08-17T00:00:00.000Z',
    });
    mockedCreateMemory.mockResolvedValue(created);

    render(<MemoryView tenantContext={tenantContext} />);

    await screen.findByText(/no memory notes have been recorded/i);

    const createSection = screen.getByText('New memory note').closest('section') as HTMLElement;
    const taskIdInput = within(createSection).getByLabelText(/task id/i);
    const contentInput = within(createSection).getByLabelText(/content/i);
    const sourceTypeInput = within(createSection).getByLabelText(/source type/i);

    await user.type(taskIdInput, 'task_new_1');
    await user.type(contentInput, 'A brand new note');
    await user.clear(sourceTypeInput);
    await user.type(sourceTypeInput, 'user_note');

    const submitButton = screen.getByRole('button', { name: /add memory note/i });
    await user.click(submitButton);

    await waitFor(() =>
      expect(mockedCreateMemory).toHaveBeenCalledWith({
        taskId: 'task_new_1',
        content: 'A brand new note',
        sourceType: 'user_note',
      }),
    );

    expect(await screen.findByText('A brand new note')).toBeInTheDocument();
  });

  it('deletes a note via the two-step confirm interaction', async () => {
    const user = userEvent.setup();
    const note = makeNote();
    mockedListMemories.mockResolvedValue([note]);
    mockedDeleteMemory.mockResolvedValue(undefined);

    render(<MemoryView tenantContext={tenantContext} />);

    await screen.findByText(note.content);

    const deleteButton = screen.getByRole('button', { name: /^delete$/i });
    // First click arms the confirmation, does not call deleteMemory yet.
    await user.click(deleteButton);
    expect(mockedDeleteMemory).not.toHaveBeenCalled();

    const confirmButton = await screen.findByRole('button', { name: /confirm delete/i });
    await user.click(confirmButton);

    await waitFor(() => expect(mockedDeleteMemory).toHaveBeenCalledWith(note.id));
    await waitFor(() => expect(screen.queryByText(note.content)).not.toBeInTheDocument());
  });

  it('shows the error UI with the ApiError message when listMemories fails', async () => {
    mockedListMemories.mockRejectedValue(new ApiError('Backend is unreachable', 503));

    render(<MemoryView tenantContext={tenantContext} />);

    expect(await screen.findByText('Backend is unreachable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });
});
