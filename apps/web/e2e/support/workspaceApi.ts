import type { Page } from '@playwright/test';

// One mocked workspace with something on every main view (Assistant, Tasks, Approvals, Memory,
// Files, Settings, Notifications, Privacy, Integrations, Skills, Agents, Enterprise, Activity, Autonomy), for the accessibility and localization specs. Every request the views make
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

const fileIndex = { status: 'indexed', error: null, chunk_count: 3, embedding_model: 'anum-local-hash-v1', indexed_at: now };
const files = [
  { id: 'file_notes', name: 'release-notes.md', content_type: 'text/markdown', kind: 'markdown', previewable: true, can_manage: true, size_bytes: 2048, sha256: 'cd'.repeat(32), created_by: 'user_local', created_at: now, updated_by: null, updated_at: null, index: fileIndex },
  { id: 'file_logo', name: 'logo.png', content_type: 'image/png', kind: 'image', previewable: true, can_manage: false, size_bytes: 512, sha256: 'ef'.repeat(32), created_by: 'user_owner', created_at: now, updated_by: null, updated_at: null, index: { ...fileIndex, status: 'skipped', error: 'not_text', chunk_count: 0 } },
];
const fileUsage = { used_bytes: 2560, file_count: 2, quota_bytes: 2147483648, max_upload_bytes: 26214400, link_ttl_seconds: 300, accepted: 'text, images, PDF, Office files and .zip', accepted_extensions: ['.md', '.pdf', '.png', '.txt'], can_upload: true, can_manage_all: true };
const health = (id: string, name: string, kind: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, name, kind, status, endpoint: `${kind}://${id}:1234`, latency_ms: status === 'disabled' ? null : 3,
  detail: status === 'degraded' ? 'Health check failed: ConnectionRefusedError' : status === 'disabled' ? 'Not used: events stay in the API process.' : 'Health check passed.',
  credentials: { configured: true, source: 'environment', scopes: [], expires_at: null }, enabled: true, available: status !== 'disabled', checked_at: now,
  remediation: status === 'degraded' ? 'Check that Valkey at ANUM_VALKEY_URL is running and reachable from the API.' : null, environment_variable: 'ANUM_VALKEY_URL',
  last_error: status === 'degraded' ? 'Health check failed: ConnectionRefusedError' : null, last_error_at: status === 'degraded' ? now : null,
  accepts_workspace_credential: false, workspace_credential: null, ...extra,
});
const integrations = [
  health('valkey', 'Valkey', 'cache', 'degraded'), health('postgresql', 'PostgreSQL + pgvector', 'database', 'connected'), health('nats', 'NATS JetStream', 'event_bus', 'disabled'),
  health('external-webhook', 'External REST webhook', 'tool', 'connected', { accepts_workspace_credential: true, workspace_credential: { integration_id: 'external-webhook', configured: true, hint: '…abcd', updated_by: 'user_local', updated_at: now } }),
];
const skillVersion = (version: string, tools: string[]) => ({ id: `sv_${version}`, skill_id: 'acme.release-notes', version, name: 'Release notes', description: 'Drafts release notes from merged work.', instructions: 'Summarize.', required_tools: tools, risk_level: 'medium', publisher_tenant_id: 'tenant_local', created_by: 'user_local', created_at: now });
const skillVersions = [skillVersion('1.0.0', ['anum.respond']), skillVersion('2.0.0', ['anum.respond', 'external.action']), { ...skillVersion('1.0.0', ['web_search']), id: 'sv_search', skill_id: 'acme.search', name: 'Web research' }];
const skillInstall = { id: 'si_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', skill_version_id: 'sv_1.0.0', skill_id: 'acme.release-notes', version: '1.0.0', approved_tools: ['anum.respond'], enabled: true, installed_by: 'user_local', installed_at: now };
const packages = [
  { id: 'acme.crm-sync', name: 'CRM sync', kind: 'integration', version: '1.2.0', publisher: 'Acme', verified: true, permissions: ['integration:read'], regions: ['eu-west'] },
  { id: 'beta.notes', name: 'Meeting notes', kind: 'skill', version: '0.3.0', publisher: 'Beta', verified: false, permissions: [], regions: [] },
];
const automation = {
  id: 'wf_digest', name: 'Weekly digest', description: 'Summarises the week', status: 'active', version: 2, overlap_policy: 'skip',
  created_at: now, updated_at: now, created_by: 'user_local', updated_by: null, last_run_at: now, last_run_status: 'failed', next_run_at: now,
  active_runs: 0, run_count: 1, schedule_count: 1,
  steps: [{ id: 'make', name: 'Create digest', action: 'task.create', input: { title: 'Digest', prompt: 'Summarise' }, max_attempts: 3 }],
};
const automationSchedule = {
  id: 'schedule_1', workflow_id: 'wf_digest', name: 'Weekday mornings', cron: '0 9 * * 1-5', cron_description: 'At 09:00, Monday through Friday', timezone: 'Asia/Riyadh',
  enabled: true, next_run_at: now, last_run_at: now, last_outcome: 'fired', created_by: 'user_local', created_at: now, updated_at: now,
};
const automationRun = {
  id: 'automation_run_1', workflow_id: 'wf_digest', workflow_name: 'Weekly digest', workflow_version: 2, status: 'failed', trigger: 'schedule', idempotency_key: null,
  retry_of: null, schedule_id: 'schedule_1', current_step: 0, created_at: now, updated_at: now, created_by: 'user_local', error: 'Upstream down',
  steps: [{ id: 'make', name: 'Create digest', action: 'task.create', status: 'failed', attempt: 1, input: {}, output: {}, error: 'Upstream down' }],
};
const soon = new Date(Date.now() + 10 * 60_000).toISOString();
const autonomyAction = {
  id: 'auto_1', task_id: 'task_done', tool: 'email.archive', category: 'email', action_kind: 'archive', level: 'act_and_notify', pattern_key: 'email.archive',
  template: null, summary: 'Archived the supplier newsletter', target: null, status: 'done', requested_by: 'user_local', created_at: now, reversible: true,
  undo_available: true, undo_expires_at: soon, undo_token: 'undo_example', can_undo: true, undone_by: null, undone_at: null, undo_error: null, pattern_level: null,
};
const autonomyOverview = {
  settings: { category_levels: {}, amount_threshold: 0, recipients_threshold: 5, price_change_percent_threshold: 0, daily_caps: {}, quiet_hours: null, blocked_recipients: [], blocked_domains: [], approved_templates: ['order_status'], proposal_after: 3, updated_by: null, updated_at: null },
  guardrails: { spending_limit: null, max_auto_amount: null, max_auto_recipients: null, max_auto_price_change_percent: null, category_ceilings: {}, daily_caps: {}, quiet_hours: null, blocked_recipients: [], blocked_domains: [] },
  categories: ['email', 'whatsapp', 'commerce', 'developer', 'files', 'tasks', 'general'].map((category) => ({ category, level: 'act_silently', ceiling: null, effective: 'act_silently' })),
  recommendations: [{ kind: 'price_change', recommended: 'ask_always', protected: true, note: '' }, { kind: 'delete', recommended: 'ask_always', protected: true, note: '' }],
  pending_proposals: 1, can_manage_workspace: true, can_manage_guardrails: false, can_tighten: true,
};
const autonomyProposal = { id: 'aprop_1', pattern_key: 'whatsapp.send_template|template=shipping', tool: 'whatsapp.send_template', template: 'shipping', category: 'whatsapp', action_kind: 'send', proposed_level: 'act_and_notify', approvals: 3, status: 'pending', created_at: now };

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
    if (path === '/api/v1/files/usage') return json(fileUsage);
    if (path === '/api/v1/files' && method === 'GET') return json(files);
    if (path === '/api/v1/files/file_notes') return json(files[0]);
    if (path === '/api/v1/files/file_notes/preview') return json({ kind: 'markdown', text: '# Release notes\n\n- Faster exports', truncated: false, size_bytes: 2048 });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    // Automations (docs/automation.md): one automation with a schedule and a failed run.
    if (path === '/api/v1/automation/workflows' && method === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'X-Total-Count': '1', 'Access-Control-Expose-Headers': 'X-Total-Count' }, body: JSON.stringify([automation]) });
    if (path === '/api/v1/automation/workflows/wf_digest') return json(automation);
    if (path === '/api/v1/automation/schedules') return json([automationSchedule]);
    if (path === '/api/v1/automation/runs') return json([automationRun]);
    if (path === '/api/v1/autonomy/actions') return json({ items: [autonomyAction], next_cursor: null });
    if (path === '/api/v1/autonomy/settings') return json(autonomyOverview);
    if (path === '/api/v1/autonomy/proposals') return json([autonomyProposal]);
    if (path === '/api/v1/autonomy/rules') return json([]);
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'owner', active: true }]);
    // Workspace lifecycle and the audit log (docs/identity.md#workspace-lifecycle, docs/governance-and-scale.md#audit-log).
    if (path === '/api/v1/workspaces/current' && method === 'GET') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: now, updated_at: now, archived_at: null, archived_by: null });
    if (path === '/api/v1/workspaces/current/permissions') return json({ workspace_id: 'workspace_foundation', role: 'owner', organization_admin: true, can_manage_workspace: true, can_create_workspace: true, can_manage_members: true, can_read_audit: true, can_export_audit: true });
    if (path === '/api/v1/audit-records/actions') return json({ actions: ['policy_pack.created', 'workspace_member.role_change'] });
    if (path === '/api/v1/audit-records') return json({ items: [{ id: 'audit_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', actor: 'user_local', action: 'workspace_member.role_change', target: 'user_member', outcome: 'success', correlation_id: 'corr_1', created_at: now, metadata: { role: 'viewer' } }], next_cursor: null, retention: 'Audit records are kept for as long as the workspace exists, including after it is archived. They are append-only: nobody can edit or delete them, owners included.' });
    // Integrations, Skills, Agents and Enterprise (docs/tools-and-integrations.md, docs/skills.md).
    if (path === '/api/v1/integrations') return json(integrations);
    if (path === '/api/v1/skills/versions') return json(skillVersions);
    if (path === '/api/v1/skills/installations') return json([skillInstall]);
    if (path === '/api/v1/skills/acme.release-notes') {
      return json({
        skill_id: 'acme.release-notes', name: 'Release notes', description: 'Drafts release notes from merged work.', latest_version: '2.0.0',
        versions: [skillVersions[1], skillVersions[0]], installation: skillInstall, tools_version: '1.0.0',
        tools: [{ name: 'anum.respond', registered: true, description: 'Create an internal response.', risk_level: 'low', approval: 'rule', approval_text: 'Runs without approval unless an organization approval rule or policy pack asks for one.', target: null, granted: true }],
        history: [{ id: 'audit_1', action: 'skill.installed', actor: 'user_local', outcome: 'success', created_at: now, metadata: { version: '1.0.0' } }],
        can_manage: true, can_rollback: true,
      });
    }
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: 0, memory_governance_configured: false, can_manage_organization_policy: true });
    if (path === '/api/v1/enterprise/operations') return json({ tenant_id: 'tenant_local', active_regions: 1, healthy_targets: 1, degraded_targets: 0, installed_packages: 1, failover_ready: false, generated_at: now });
    if (path === '/api/v1/routing/targets') return json([{ id: 'configured-model', region: 'local', provider: 'anthropic', model: 'claude', status: 'unverified', modalities: ['text'], sensitivity: ['standard'], cost_per_1k_tokens: 0, latency_ms: 1 }]);
    if (path === '/api/v1/marketplace/packages') return json(packages);
    if (path === '/api/v1/marketplace/installs') return json([{ package_id: 'acme.crm-sync', version: '1.2.0', enabled: true, installed_by: 'user_local', installed_at: now }]);
    return route.abort();
  });
}

/** Start the page in `locale` the way a person's saved choice does (this browser's storage). */
export async function useSavedLocale(page: Page, locale: 'en' | 'ar'): Promise<void> {
  await page.addInitScript((value) => { try { localStorage.setItem('anum.locale', value); } catch { /* storage blocked */ } }, locale);
}
