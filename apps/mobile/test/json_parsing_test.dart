// Malformed API payloads fail with one clear JsonShapeException (a
// FormatException) naming the field and the expected type, and the screens'
// error handling still turns that into an error state (docs/mobile.md,
// Typed JSON parsing).
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/admin/admin_models.dart';
import 'package:anum_mobile/features/governance/api_governance_repository.dart';
import 'package:anum_mobile/features/governance/governance_controller.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:flutter_test/flutter_test.dart';

Matcher _shape(String path, String expected, String found) =>
    isA<JsonShapeException>()
        .having((e) => e.path, 'path', path)
        .having((e) => e.expected, 'expected', expected)
        .having((e) => e.found, 'found', found)
        .having((e) => e, 'is a FormatException', isA<FormatException>());

const _time = '2026-10-06T10:00:00Z';

JsonMap _session() => {
      'access_token': 'token',
      'token_type': 'bearer',
      'expires_at': _time,
      'context': {
        'tenant_id': 'tenant_test',
        'workspace_id': 'workspace_test',
        'user_id': 'user_test',
        'roles': ['owner'],
      },
    };

JsonMap _scope() => {
      'usage': {'calls': 1},
      'total_tokens': 10,
      'budget': {
        'monthly_cost_limit_usd': 5,
        'monthly_token_limit': null,
        'updated_at': _time,
        'updated_by': 'user_test',
      },
    };

class _Transport implements ApiTransport {
  _Transport(this.bodies);
  final Map<String, JsonMap> bodies;
  @override
  Future<ApiResponse> send(ApiRequest request) async => ApiResponse(
      statusCode: 200,
      body: bodies[request.uri.path] ?? const {'data': <Object?>[]});
}

Future<AnumApiClient> _client(Map<String, JsonMap> bodies) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson(_session()).copyWith(
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1))));
  return AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: _Transport(bodies),
      sessions: sessions);
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

class _NoExport implements AuditExporter {
  @override
  Future<void> export(String format) async {}
}

void main() {
  group('JsonReader', () {
    const reader = JsonReader({
      'name': 'Ops',
      'count': 3,
      'ratio': 0.5,
      'when': 'not a date',
      'tags': ['a', 7],
      'items': [
        {'id': 'x'},
        'oops'
      ],
      'nothing': null,
    }, 'root');

    test('reads valid values like the casts it replaces', () {
      expect(reader.string('name'), 'Ops');
      expect(reader.integer('count'), 3);
      expect(reader.integer('ratio'), 0);
      expect(reader.number('count'), 3.0);
      expect(reader.optString('nothing'), isNull);
      expect(reader.optString('absent'), isNull);
      expect(reader.optObjects('absent', (x) => x), isEmpty);
      expect(reader.optStrings('absent'), isNull);
    });

    test('names a missing field', () {
      expect(() => reader.string('title'),
          throwsA(_shape('root.title', 'a string', 'nothing')));
      expect(() => reader.boolean('nothing'),
          throwsA(_shape('root.nothing', 'a boolean', 'null')));
    });

    test('names a field of the wrong type', () {
      expect(() => reader.string('count'),
          throwsA(_shape('root.count', 'a string', 'a number')));
      expect(() => reader.optInt('name'),
          throwsA(_shape('root.name', 'a number', 'a string')));
      expect(() => reader.object('tags'),
          throwsA(_shape('root.tags', 'an object', 'a list')));
      expect(() => reader.date('when'),
          throwsA(_shape('root.when', 'an ISO 8601 date-time', 'a string')));
    });

    test('names the wrong list element', () {
      expect(() => reader.strings('tags'),
          throwsA(_shape('root.tags[1]', 'a string', 'a number')));
      expect(() => reader.objects('items', (x) => x.string('id')),
          throwsA(_shape('root.items[1]', 'an object', 'a string')));
    });

    test('the message reads as one sentence', () {
      expect(
          () => reader.string('count'),
          throwsA(predicate((Object e) =>
              e.toString() ==
              'Unexpected response: "root.count" should be a string, '
                  'found a number.')));
    });

    test('a response body that is not an object is refused', () {
      expect(() => jsonObject(<Object?>[]),
          throwsA(_shape('response', 'an object', 'a list')));
      expect(JsonReader.of(<String, Object?>{'a': 1}).path, isEmpty);
    });
  });

  group('session and onboarding models', () {
    test('a stored session with a missing field names it', () {
      final json = _session()..remove('access_token');
      expect(() => LocalSession.fromJson(json),
          throwsA(_shape('access_token', 'a string', 'nothing')));
    });

    test('nested context fields carry their path', () {
      final json = _session();
      (json['context']! as JsonMap)['roles'] = ['owner', 3];
      expect(() => LocalSession.fromJson(json),
          throwsA(_shape('context.roles[1]', 'a string', 'a number')));
      (json['context']! as JsonMap)['user_id'] = true;
      expect(() => LocalSession.fromJson(json),
          throwsA(_shape('context.user_id', 'a string', 'a boolean')));
    });

    test('valid sessions round-trip unchanged', () {
      final session = LocalSession.fromJson(_session());
      expect(
          LocalSession.fromJson(session.toJson()).toJson(), session.toJson());
    });

    test('onboarding names the nested membership field', () {
      expect(
          () => OnboardingStatus.fromJson({
                'complete': true,
                'model_configured': false,
                'membership': {
                  'tenant_id': 't',
                  'workspace_id': 'w',
                  'user_id': 'u',
                  'role': 'owner',
                  'active': 'yes',
                },
              }),
          throwsA(_shape('membership.active', 'a boolean', 'a string')));
    });
  });

  group('admin and voice models', () {
    test('budget overview names the nested scope field', () {
      final workspace = _scope();
      (workspace['budget']! as JsonMap)['updated_at'] = 42;
      expect(
          () => BudgetOverview.fromJson({
                'period_start': '2026-10-01',
                'resets_on': '2026-11-01',
                'tenant': _scope(),
                'workspace': workspace,
              }),
          throwsA(
              _shape('workspace.budget.updated_at', 'a string', 'a number')));
      expect(
          () => BudgetOverview.fromJson({
                'period_start': '2026-10-01',
                'resets_on': '2026-11-01',
                'tenant': _scope(),
              }),
          throwsA(_shape('workspace', 'an object', 'nothing')));
    });

    test('a member with a wrong type names the field', () {
      expect(
          () => WorkspaceMember.fromJson({
                'user_id': 'u',
                'workspace_id': 'w',
                'role': 7,
                'created_at': _time,
                'updated_at': _time,
              }),
          throwsA(_shape('role', 'a string', 'a number')));
    });

    test('voice answers keep their defaults and name a wrong type', () {
      final result = VoiceAskResult.fromJson({'reply': 'Hi'});
      expect(result.intent, 'question');
      expect(result.workspace.tasksTotal, 0);
      expect(
          () => VoiceAskResult.fromJson({
                'workspace': {'tasks_total': 'many'}
              }),
          throwsA(_shape('workspace.tasks_total', 'a number', 'a string')));
    });
  });

  group('repositories and controllers', () {
    test('a malformed task list names the element and field', () async {
      final repository = ApiWorkspaceRepository(
          await _client({
            '/api/v1/tasks': {
              'data': [
                {
                  'id': 'task_1',
                  'title': 'One',
                  'prompt': 'One',
                  'created_at': _time,
                  'updated_at': _time
                },
                {'id': 'task_2', 'prompt': 'Two'},
              ]
            },
          }),
          fileTransfer: _NoFiles());
      await expectLater(repository.loadWorkspace(),
          throwsA(_shape('data[1].title', 'a string', 'nothing')));
    });

    test('a wrong approver element names its index', () async {
      final repository = ApiWorkspaceRepository(
          await _client({
            '/api/v1/approvals': {
              'data': [
                {
                  'id': 'approval_1',
                  'task_id': 'task_1',
                  'action': 'external.action',
                  'reason': 'Needs approval',
                  'risk_level': 'high',
                  'status': 'pending',
                  'created_at': _time,
                  'approvers': ['user_a'],
                }
              ]
            },
          }),
          fileTransfer: _NoFiles());
      await expectLater(repository.loadWorkspace(),
          throwsA(_shape('data[0].approvers[0]', 'an object', 'a string')));
    });

    test('the workspace controller shows the field in its error state',
        () async {
      final controller = WorkspaceController(ApiWorkspaceRepository(
          await _client({
            '/api/v1/files': {
              'data': [
                {
                  'id': 'file_1',
                  'name': 'a.txt',
                  'content_type': 'text/plain',
                  'size_bytes': '12',
                  'created_at': _time
                }
              ]
            },
          }),
          fileTransfer: _NoFiles()));
      await controller.load();
      expect(controller.phase, LoadPhase.error);
      expect(controller.message,
          'Unexpected response: "data[0].size_bytes" should be a number, found a string.');
    });

    test('the governance controller shows the field in its error state',
        () async {
      final controller = GovernanceController(ApiGovernanceRepository(
          await _client({
            '/api/v1/organization/governance': {
              'policy_packs': 0,
              'active_policy_packs': 0,
              'role_templates': 0,
            },
          }),
          auditExporter: _NoExport()));
      await controller.load();
      expect(controller.phase, GovernancePhase.error);
      expect(controller.message, contains('"approval_rules"'));
    });
  });
}
