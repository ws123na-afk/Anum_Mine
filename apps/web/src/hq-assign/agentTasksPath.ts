/** One page of the tasks run as an agent (`GET /api/v1/tasks?agent_id=`, the Tasks list's own route); `cursor` is the last task id shown. */
export const AGENT_TASKS_PAGE = 15;
export function agentTasksPath(agentId: string, cursor: string | null, limit = AGENT_TASKS_PAGE): string {
  const q = new URLSearchParams({ agent_id: agentId, limit: String(limit) });
  if (cursor) q.set('cursor', cursor);
  return `/api/v1/tasks?${q}`;
}
