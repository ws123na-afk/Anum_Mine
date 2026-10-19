import 'fake_api.dart';

/// Realistic API answers for one owner in one workspace, in the shapes the
/// API returns (services/api schemas) and the web e2e fixtures use
/// (apps/web/e2e/support/workspaceApi.ts, homeApi.ts).
abstract final class Fx {
  static const tenantId = 'tenant_local';
  static const workspaceId = 'workspace_foundation';
  static const userId = 'user_owner';

  static final now = DateTime.now().toUtc();
  static String iso([Duration offset = Duration.zero]) =>
      now.add(offset).toIso8601String();

  static Map<String, Object?> session() => {
        'access_token': 'anum_local_example',
        'token_type': 'bearer',
        'expires_at': iso(const Duration(hours: 8)),
        'context': {
          'tenant_id': tenantId,
          'workspace_id': workspaceId,
          'user_id': userId,
          'roles': ['owner'],
        },
      };

  static Map<String, Object?> membership() => {
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'user_id': userId,
        'role': 'owner',
        'active': true,
      };

  static Map<String, Object?> onboarding() => {
        'complete': true,
        'model_configured': true,
        'tenant': {'id': tenantId, 'name': 'Foundation Trading'},
        'workspace': {
          'id': workspaceId,
          'tenant_id': tenantId,
          'name': 'Foundation'
        },
        'membership': membership(),
      };

  /// SHA-256 sized payload hash, as the runtime binds an approval to.
  static final payloadHash = 'ab' * 32;

  static Map<String, Object?> task(String id, String title, String status) => {
        'id': id,
        'title': title,
        'prompt': '$title and flag risks',
        'status': status,
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'created_by': userId,
        'priority': 'normal',
        'due_at': null,
        'archived_at': null,
        'created_at': iso(const Duration(minutes: -40)),
        'updated_at': iso(const Duration(minutes: -5)),
      };

  static Map<String, Object?> approval(
          {String status = 'pending', String? decidedBy}) =>
      {
        'id': 'approval_pending',
        'task_id': 'task_release',
        'action': 'external.action',
        'risk_level': 'high',
        'status': status,
        'reason': 'External or high-impact actions require explicit approval.',
        'created_at': iso(const Duration(minutes: -30)),
        'run_id': 'run_release',
        'step_id': 'step_proposal',
        'arguments': {
          'action': 'Publish the release notes',
          'channel': '#announcements'
        },
        'payload_hash': payloadHash,
        'expires_at': iso(const Duration(hours: 5)),
        'decided_at': decidedBy == null ? null : iso(),
        'decided_by': decidedBy,
        'decision_reason': null,
        'required_approvals': 1,
        'approvers': [
          if (decidedBy != null) {'user_id': decidedBy, 'approved_at': iso()}
        ],
      };

  static Map<String, Object?> run(String status) => {
        'id': 'run_release',
        'task_id': 'task_release',
        'status': status,
        'result': status == 'completed'
            ? 'Release notes published to #announcements.'
            : null,
        'created_at': iso(const Duration(minutes: -35)),
        'steps': [
          {
            'id': 'step_plan',
            'type': 'model_call',
            'summary': 'Planned the release announcement.',
            'created_at': iso(const Duration(minutes: -34)),
          },
          {
            'id': 'step_proposal',
            'type': 'tool_proposal',
            'summary': 'Proposed external.action for approval.',
            'created_at': iso(const Duration(minutes: -30)),
          },
        ],
      };

  static Map<String, Object?> homeSummary(
          {required List<Map<String, Object?>> focus}) =>
      {
        'generated_at': iso(),
        'profile': {'display_name': 'Sara Khan', 'updated_at': iso()},
        'focus': focus,
        'counts': {'approvals': 1, 'email': 0, 'tasks': 1, 'automations': 0},
        'progress': {'completed': 3, 'open': 2},
        'activity': [
          {
            'id': 'activity_1',
            'kind': 'task',
            'title': 'Summarize feedback',
            'status': 'completed',
            'at': iso(const Duration(minutes: -50)),
            'target': {'feature': 'tasks', 'id': null},
          }
        ],
        'sources': [
          {'name': 'approvals', 'status': 'ok'},
          {'name': 'tasks', 'status': 'ok'},
        ],
      };
}

/// The workspace behind Home, Tasks and Approvals: one task waiting for an
/// approval. Approving checks the payload hash like the API does
/// (`ApprovalDecisionRequest`: 64 hex characters, equal to the stored hash)
/// and moves the task on.
class WorkspaceState {
  bool approved = false;
  FakeRequest? decision;

  Map<String, Object?> get task => Fx.task('task_release', 'Review the release',
      approved ? 'completed' : 'waiting_approval');

  Map<String, Object?> get approval => approved
      ? Fx.approval(status: 'approved', decidedBy: Fx.userId)
      : Fx.approval();

  void install(FakeAnumApi api) {
    api.get('/api/v1/tasks', (_) => FakeResponse.json([task]));
    api.get(
        '/api/v1/tasks/{id}',
        (r) => r.params['id'] == 'task_release'
            ? FakeResponse.json(task)
            : FakeResponse.error(404, 'not_found', 'Task not found'));
    // After tasks/{id}: later routes win, and "summary" is not a task id.
    api.get(
        '/api/v1/tasks/summary',
        (_) => FakeResponse.json({
              'total': 1,
              'by_status': {approved ? 'completed' : 'waiting_approval': 1},
              'archived': 0,
              'creators': [Fx.userId],
            }));
    api.get('/api/v1/tasks/{id}/latest-run',
        (_) => FakeResponse.json(Fx.run(approved ? 'completed' : 'paused')));
    api.get('/api/v1/tasks/{id}/runs',
        (_) => FakeResponse.json([Fx.run(approved ? 'completed' : 'paused')]));
    api.get('/api/v1/approvals', (_) => FakeResponse.json([approval]));
    api.post('/api/v1/approvals/{id}/approve', (r) {
      final hash = r.body['payload_hash'];
      if (hash is! String || !RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        return FakeResponse.error(
            422, 'validation_error', 'payload_hash is required.');
      }
      if (hash != Fx.payloadHash) {
        return FakeResponse.error(409, 'approval_payload_mismatch',
            'The action changed since it was shown. Review it again.');
      }
      decision = r;
      approved = true;
      return FakeResponse.json(
          {'approval': approval, 'task': task, 'run': Fx.run('completed')});
    });
    api.get(
        '/api/v1/home/summary',
        (_) => FakeResponse.json(Fx.homeSummary(
              focus: [
                if (!approved)
                  {
                    'id': 'tasks:task_release',
                    'source': 'tasks',
                    'kind': 'task_waiting',
                    'title': 'Review the release',
                    'detail': 'Waiting for approval',
                    'severity': 'high',
                    'at': Fx.iso(const Duration(minutes: -5)),
                    'target': {'feature': 'tasks', 'id': 'task_release'},
                    'params': {},
                  }
              ],
            )));
  }
}

/// The answers every journey needs: signing in, the first workspace load
/// and the shared lists behind Home. Journeys add their own routes on top.
void seedBaseline(FakeAnumApi api) {
  api.post('/api/v1/auth/local/session',
      (_) => FakeResponse.json(Fx.session(), status: 201));
  api.delete('/api/v1/auth/local/session', (_) => const FakeResponse.empty());
  api.get('/api/v1/onboarding', (_) => FakeResponse.json(Fx.onboarding()));
  api.get('/api/v1/workspace-memberships/current',
      (_) => FakeResponse.json(Fx.membership()));
  api.get(
      '/api/v1/me/workspace-memberships',
      (_) => FakeResponse.json({
            'data': [
              {
                'tenant_id': Fx.tenantId,
                'workspace_id': Fx.workspaceId,
                'role': 'owner',
                'workspace_name': 'Foundation',
              }
            ]
          }));
  for (final path in const [
    '/api/v1/tasks',
    '/api/v1/approvals',
    '/api/v1/automation/runs',
    '/api/v1/files',
    '/api/v1/memories',
    '/api/v1/automation/workflows',
    '/api/v1/automation/schedules',
    '/api/v1/skills/versions',
    '/api/v1/skills/installations',
    '/api/v1/integrations',
  ]) {
    api.get(path, (_) => const FakeResponse.json(<Object?>[]));
  }
  api.get(
      '/api/v1/tasks/summary',
      (_) => const FakeResponse.json({
            'total': 0,
            'by_status': <String, int>{},
            'archived': 0,
            'creators': <String>[],
          }));
  api.get(
      '/api/v1/files/usage',
      (_) => const FakeResponse.json({
            'used_bytes': 0,
            'file_count': 0,
            'quota_bytes': 2147483648,
            'max_upload_bytes': 26214400,
            'link_ttl_seconds': 300,
            'accepted': 'text, images, PDF, Office files and .zip',
            'accepted_extensions': ['.md', '.pdf', '.png', '.txt'],
            'can_upload': true,
            'can_manage_all': true,
          }));
  api.get('/api/v1/home/summary',
      (_) => FakeResponse.json(Fx.homeSummary(focus: const [])));
  api.get('/api/v1/home/pins', (_) => const FakeResponse.json(<Object?>[]));
  api.get(
      '/api/v1/home/commands',
      (_) => const FakeResponse.json({
            'items': [
              {
                'id': 'builtin_briefing',
                'mode': 'work',
                'label': 'Morning briefing',
                'prompt': "Summarize my unread email and today's tasks",
                'key': 'briefing',
                'built_in': true,
                'position': 0,
              }
            ],
            'can_edit': true,
          }));
  api.get(
      '/api/v1/home/insights',
      (_) => const FakeResponse.json(
          {'items': <Object?>[], 'hidden': 0, 'sources': <Object?>[]}));
  api.get('/api/v1/favorites',
      (_) => const FakeResponse.json({'items': <Object?>[], 'total': 0}));
  api.get(
      '/api/v1/app-calls/config',
      (_) => const FakeResponse.json({
            'enabled': false,
            'mode': 'off',
            'reason': 'Calls are off in this workspace.',
            'video_enabled': false,
            'ring_timeout_seconds': 30,
          }));
  // Live streams stay open and quiet.
  api.get('/api/v1/app-calls/stream', (_) => FakeResponse.openStream());
  api.get('/api/v1/events/stream', (_) => FakeResponse.openStream());
}
