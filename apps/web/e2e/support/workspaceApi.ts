import type { Page } from '@playwright/test';

// One mocked workspace with something on every main view (Assistant, Tasks, Approvals, Memory,
// Settings, Notifications, Privacy), for the accessibility and localization specs. Every request the views make
// is answered; anything else is aborted so a new, unmocked call shows up as a failure.
const now = new Date().toISOString();
const later = new Date(Date.now() + 5 * 3600_000).toISOString();

const task = { id: 'task_release', title: 'Review the release', prompt: 'Review the release notes and flag risks', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
const doneTask = { ...task, id: 'task_done', title: 'Summarize feedback', prompt: 'Summarize the customer feedback', status: 'completed' };
const run = {
  id: 'run_done', task_id: 'task_done', status: 'completed', result: 'Customers want **faster** exports.',
  steps: [
    { id: 'step_1', type: 'retrieval', summary: 'Retrieved 1 source.', created_at: now, metadata: { status: 'ok', embedding_model: 'anum-local-hash-v1', sources: [{ chunk_id: 'chunk_1', source_type: 'memory', source_id: 'memory_task', chunk_index: 0, score: 0.82, truncated: false }] } },
    { id: 'step_2', type: 'model_call', summary: 'Planned the summary.', created_at: now },
    { id: 'step_3', type: 'final', summary: 'Answered.', created_at: now },
  ],
};
const pendingApproval = {
  id: 'approval_pending', task_id: 'task_release', action: 'external.action', risk_level: 'high', status: 'pending',
  reason: 'External or high-impact actions require explicit approval.', created_at: now, run_id: 'run_release', step_id: 'step_proposal',
  arguments: { action: 'Publish the release notes', channel: '#announcements' }, payload_hash: 'ab'.repeat(32), expires_at: later,
  decided_at: null, decided_by: null, required_approvals: 2, approvers: [],
};
const decidedApproval = { ...pendingApproval, id: 'approval_done', status: 'approved', decided_at: now, decided_by: 'user_owner', decision_reason: 'Checked the wording.', required_approvals: 1 };
const memories = [
  { id: 'memory_chat', task_id: null, owner_user_id: 'user_local', content: 'The user prefers short answers.', provenance: { source_type: 'conversation', source_id: 'conv_1', created_by_user_id: 'user_local', created_at: now, metadata: { conversation_title: 'Release planning' } }, retention: { kind: 'indefinite', expires_at: null }, created_at: now, updated_at: null },
  { id: 'memory_task', task_id: 'task_done', content: 'Exports must finish within a minute.', provenance: { source_type: 'user', source_id: null, created_by_user_id: 'user_local', created_at: now }, retention: { kind: 'task', expires_at: null }, created_at: now },
];
const counts = { conversations: 2, conversation_turns: 6, private_memories: 1, retrieval_index_entries: 1, voice_sessions: 0, voice_transcript_segments: 0, notification_preferences: 1, user_preferences: 1, push_devices: 1 };
// One phone registered for push (Settings › Notifications, docs/notifications.md).
const devices = [{ id: 'device_pixel', platform: 'android', label: 'Pixel 8', token_hint: '1a2b', created_at: now, updated_at: now, last_sent_at: now }];
const shared = { tasks_created: 2, approvals_decided: 1, shared_memories_created: 1, files_uploaded: 0 };
const privacy = {
  tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', private: counts, shared,
  retention: { conversation_days: 30, voice_transcript_days: 3, voice_transcript_choices: ['session', '30_days', 'permanent'], private_memories: 'Kept until you delete them.', task_memories: 'Kept with their task.', audit_records: 'Kept as the security record.' },
  model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: true },
  privacy_policy_url: 'https://example.com/privacy', delete_confirmation: 'DELETE MY DATA', export_max_bytes: 26214400,
  account_note: 'Your sign-in account is managed by your organization.', shared_note: 'Shared records belong to the workspace.',
};

export async function mockWorkspaceApi(page: Page): Promise<void> {
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' && method === 'GET') return json([task, doneTask]);
    if (path === '/api/v1/tasks/task_done/latest-run') return json(run);
    if (path === '/api/v1/tasks/task_release/latest-run') return json({ detail: 'Agent run not found' }, 404);
    if (path === '/api/v1/approvals') return json([pendingApproval, decidedApproval]);
    if (path === '/api/v1/retrieval/sources') return json({ sources: [{ source_type: 'memory', source_id: 'memory_task', found: true, title: 'Exports must finish within a minute.', kind: 'task' }] });
    if (path === '/api/v1/conversations' && method === 'GET') return json([{ id: 'conv_1', title: 'Release planning', created_at: now, updated_at: now }]);
    if (path === '/api/v1/conversations/conv_1') {
      return json({ id: 'conv_1', title: 'Release planning', created_at: now, updated_at: now, turns: [
        { id: 't1', conversation_id: 'conv_1', role: 'user', content: 'What is left before the release?', status: 'complete', created_at: now },
        { id: 't2', conversation_id: 'conv_1', role: 'assistant', content: 'Two items:\n\n- Approve the announcement\n- Update the changelog', status: 'complete', created_at: now },
      ] });
    }
    if (path === '/api/v1/memories' && method === 'GET') {
      const onlyChats = url.searchParams.get('source_type') === 'conversation';
      return json(memories.filter((m) => !onlyChats || m.provenance.source_type === 'conversation'));
    }
    if (path === '/api/v1/retrieval/status') {
      return json({ embedding_model: 'anum-local-hash-v1', indexed: 2, failed: 0, skipped: 0, stale: 0, chunks: 2, sources: memories.map((m) => ({ source_type: 'memory', source_id: m.id, name: null, status: 'indexed', error: null, chunk_count: 1, embedding_model: 'anum-local-hash-v1', indexed_at: now })) });
    }
    if (path === '/api/v1/me/preferences') return json({ remember_conversations: true, updated_at: now, conversation_memory: 'on' });
    if (path === '/api/v1/onboarding') return json({ complete: true, tenant: { id: 'tenant_local', name: 'ANUM' }, workspace: { id: 'workspace_foundation', name: 'Foundation' }, membership: { role: 'owner' }, model_configured: false });
    if (path === '/api/v1/notification-preferences') {
      return json(method === 'PUT' ? request.postDataJSON() : { task_completed: true, approval_required: true, run_failed: true, automation_failed: false, email_enabled: false, desktop_enabled: true, push_enabled: true });
    }
    if (path === '/api/v1/push/status') return json({ available: true, provider: 'fcm', project_id: 'anum-example', message: 'Phone push is on.' });
    if (path === '/api/v1/me/devices' && method === 'GET') return json(devices);
    if (path === '/api/v1/model-config') return json({ detail: 'No workspace model configuration' }, 404);
    if (path === '/api/v1/privacy') return json(privacy);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'owner', active: true }]);
    // Workspace lifecycle and the audit log (docs/identity.md#workspace-lifecycle, docs/governance-and-scale.md#audit-log).
    if (path === '/api/v1/workspaces/current' && method === 'GET') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: now, updated_at: now, archived_at: null, archived_by: null });
    if (path === '/api/v1/workspaces/current/permissions') return json({ workspace_id: 'workspace_foundation', role: 'owner', organization_admin: true, can_manage_workspace: true, can_create_workspace: true, can_manage_members: true, can_read_audit: true, can_export_audit: true });
    if (path === '/api/v1/audit-records/actions') return json({ actions: ['policy_pack.created', 'workspace_member.role_change'] });
    if (path === '/api/v1/audit-records') return json({ items: [{ id: 'audit_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', actor: 'user_local', action: 'workspace_member.role_change', target: 'user_member', outcome: 'success', correlation_id: 'corr_1', created_at: now, metadata: { role: 'viewer' } }], next_cursor: null, retention: 'Audit records are kept for as long as the workspace exists, including after it is archived. They are append-only: nobody can edit or delete them, owners included.' });
    return route.abort();
  });
}

/** Start the page in `locale` the way a person's saved choice does (this browser's storage). */
export async function useSavedLocale(page: Page, locale: 'en' | 'ar'): Promise<void> {
  await page.addInitScript((value) => { try { localStorage.setItem('anum.locale', value); } catch { /* storage blocked */ } }, locale);
}
