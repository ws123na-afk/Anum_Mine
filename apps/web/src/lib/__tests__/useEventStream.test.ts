import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import type { TenantContext } from '@anum/contracts';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    authHeaders: vi.fn(async () => ({
      'x-tenant-id': 'tenant_local',
      'x-workspace-id': 'workspace_foundation',
      'x-user-id': 'user_local',
      'x-user-roles': 'owner,member',
    })),
  };
});

const tenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

function sseBody(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < frames.length) {
        controller.enqueue(encoder.encode(frames[index]));
        index += 1;
      } else {
        controller.close();
      }
    },
  });
}

function sseFrame(id: string, type: string, payload: Record<string, unknown>): string {
  const data = JSON.stringify({
    id,
    type,
    version: 1,
    tenant_id: 'tenant_local',
    workspace_id: 'workspace_foundation',
    subject: 'task_1',
    correlation_id: 'task_1',
    created_at: '2026-08-17T10:00:00.000Z',
    payload,
  });
  return `id: ${id}\nevent: ${type}\ndata: ${data}\n\n`;
}

describe('useEventStream', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('parses SSE frames from the stream body into events and reports status open', async () => {
    const body = sseBody([
      sseFrame('event_1', 'task.created', { title: 'First' }),
      sseFrame('event_2', 'task.completed', {}),
    ]);
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(body, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const { useEventStream } = await import('../useEventStream');
    const { result, unmount } = renderHook(() => useEventStream(tenantContext));

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.status).toBe('open');
    expect(result.current.events[0]).toMatchObject({ id: 'event_1', type: 'task.created' });
    expect(result.current.events[1]).toMatchObject({ id: 'event_2', type: 'task.completed' });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://localhost:8000/api/v1/events/stream');
    expect((init?.headers as Record<string, string>)['x-tenant-id']).toBe('tenant_local');

    unmount();
  });

  it('reports status error when the request fails outright', async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error('network down');
    });
    vi.stubGlobal('fetch', fetchMock);

    const { useEventStream } = await import('../useEventStream');
    const { result, unmount } = renderHook(() => useEventStream(tenantContext));

    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.error).toContain('network down');

    unmount();
  });

  it('reports status error on a non-ok HTTP response', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);

    const { useEventStream } = await import('../useEventStream');
    const { result, unmount } = renderHook(() => useEventStream(tenantContext));

    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.error).toContain('500');

    unmount();
  });

  it('sets status closed on unmount and stops issuing further requests', async () => {
    const body = sseBody([sseFrame('event_1', 'task.created', {})]);
    const fetchMock = vi.fn(async () => new Response(body, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const { useEventStream } = await import('../useEventStream');
    const { result, unmount } = renderHook(() => useEventStream(tenantContext));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    const callsBeforeUnmount = fetchMock.mock.calls.length;
    unmount();

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(callsBeforeUnmount);
  });

  it('reconnects after the stream ends, using the last seen event id as the cursor', async () => {
    const { RECONNECT_DELAY_MS, useEventStream } = await import('../useEventStream');
    const firstBody = sseBody([sseFrame('event_1', 'task.created', {})]);
    const secondBody = sseBody([sseFrame('event_2', 'task.completed', {})]);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(firstBody, { status: 200 }))
      .mockResolvedValueOnce(new Response(secondBody, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useEventStream(tenantContext));

    await waitFor(() => expect(result.current.events).toHaveLength(1));

    await vi.advanceTimersByTimeAsync(RECONNECT_DELAY_MS + 100);
    await waitFor(() => expect(result.current.events).toHaveLength(2));

    expect(fetchMock.mock.calls.length).toBe(2);
    const [secondUrl] = fetchMock.mock.calls[1];
    expect(secondUrl).toBe('http://localhost:8000/api/v1/events/stream?cursor=event_1');

    unmount();
  });
});
