// Approval chain progress right after a run creates an approval: read from
// the run response's `approval` (`required_approvals`, `approvers`) when
// present, then replaced by the approvals list (docs/mobile.md).
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/tasks_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _time = '2026-10-06T10:00:00Z';
final _hash = 'c' * 64;

JsonMap _task(String status) => {
      'id': 'task_1',
      'title': 'Publish the update',
      'prompt': 'Publish the update',
      'status': status,
      'created_at': _time,
      'updated_at': _time,
    };

JsonMap _approval({Map<String, Object?> chain = const {}}) => {
      'id': 'approval_1',
      'task_id': 'task_1',
      'action': 'external.action',
      'risk_level': 'high',
      'status': 'pending',
      'reason': 'An organization approval rule requires approval.',
      'created_at': _time,
      'payload_hash': _hash,
      'expires_at': DateTime.now()
          .toUtc()
          .add(const Duration(hours: 5))
          .toIso8601String(),
      ...chain,
    };

class _RunTransport implements ApiTransport {
  _RunTransport(this.runBody);
  final JsonMap runBody;
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    if (path == '/api/v1/tasks' && request.method == 'POST') {
      return ApiResponse(statusCode: 201, body: _task('created'));
    }
    if (path.endsWith('/run')) {
      return ApiResponse(statusCode: 200, body: runBody);
    }
    return const ApiResponse(statusCode: 200, body: {'data': <Object?>[]});
  }
}

Future<ApiWorkspaceRepository> _repository(JsonMap runBody) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_test',
          roles: ['owner'])));
  return ApiWorkspaceRepository(
      AnumApiClient(
          baseUri: Uri.parse('http://localhost:8000'),
          transport: _RunTransport(runBody),
          sessions: sessions),
      fileTransfer: _NoFiles());
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

WorkspaceApproval _model(
        {int required = 1, List<String> approvers = const []}) =>
    WorkspaceApproval(
      id: 'approval_1',
      taskId: 'task_1',
      action: 'external.action',
      reason: 'Needs approval.',
      risk: 'high',
      status: 'pending',
      createdAt: DateTime.utc(2026, 10, 6, 10),
      payloadHash: _hash,
      expiresAt: DateTime.now().add(const Duration(hours: 5)),
      requiredApprovals: required,
      approvers: [
        for (final user in approvers)
          ApprovalApprover(userId: user, approvedAt: DateTime.utc(2026, 10, 6)),
      ],
    );

WorkspaceTask _waiting(WorkspaceApproval? approval) => WorkspaceTask(
    id: 'task_1',
    title: 'Publish the update',
    prompt: 'Publish the update',
    status: WorkStatus.waitingApproval,
    createdAt: DateTime.utc(2026, 10, 6, 10),
    updatedAt: DateTime.utc(2026, 10, 6, 10),
    approval: approval);

/// Runs return [runTask]; the list returns [listed] once [gate] completes.
class _Repository implements WorkspaceRepository {
  _Repository(this.runTask);
  final WorkspaceTask runTask;
  List<WorkspaceApproval> listed = const [];
  Completer<void>? gate;

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async {
    await gate?.future;
    return WorkspaceSnapshot(
        tasks: [runTask],
        approvals: listed,
        automations: const [],
        files: const [],
        memories: const []);
  }

  @override
  Future<WorkspaceTask> createAndRunTask(String prompt) async => runTask;

  @override
  Future<WorkspaceTask> loadTask(String taskId) async => runTask;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  group('run response approval', () {
    test('carries the chain progress when the API sends it', () async {
      final repository = await _repository({
        'task': _task('waiting_approval'),
        'run': {
          'id': 'run_1',
          'status': 'waiting_approval',
          'steps': <Object?>[]
        },
        'approval': _approval(chain: {
          'required_approvals': 2,
          'approvers': <Object?>[],
        }),
      });
      final task = await repository.createAndRunTask('Publish the update');
      expect(task.status, WorkStatus.waitingApproval);
      expect(task.approval!.requiredApprovals, 2);
      expect(task.approval!.progressLabel, '0 of 2 approvals');
    });

    test('is tolerated without the chain fields or without an approval',
        () async {
      final bare = await (await _repository({
        'task': _task('waiting_approval'),
        'run': {
          'id': 'run_1',
          'status': 'waiting_approval',
          'steps': <Object?>[]
        },
        'approval': _approval(),
      }))
          .createAndRunTask('Publish the update');
      expect(bare.approval!.requiredApprovals, 1);
      expect(bare.approval!.approvers, isEmpty);
      expect(bare.approval!.progressLabel, isNull);

      final none = await (await _repository({
        'task': _task('completed'),
        'run': {'id': 'run_1', 'status': 'completed', 'steps': <Object?>[]},
        'approval': null,
      }))
          .createAndRunTask('Publish the update');
      expect(none.approval, isNull);
    });
  });

  group('controller', () {
    test('shows the run approval before the approvals list reloads', () async {
      final repository =
          _Repository(_waiting(_model(required: 3, approvers: ['owner_b'])));
      final controller = WorkspaceController(repository);
      await controller.load();
      expect(controller.pendingApprovals, isEmpty);

      repository.gate = Completer<void>();
      final created = controller.createTask('Publish the update');
      await pumpEventQueue();
      // The list has not answered yet; the run response's progress shows.
      expect(
          controller.pendingApprovals.single.progressLabel, '1 of 3 approvals');

      // The list has the approval with newer progress: its entry wins.
      repository.listed = [
        _model(required: 3, approvers: ['owner_b', 'owner_c'])
      ];
      repository.gate!.complete();
      await created;
      expect(
          controller.pendingApprovals.single.progressLabel, '2 of 3 approvals');
    });

    test('keeps the run approval when the list does not return it', () async {
      final repository = _Repository(_waiting(_model(required: 2)));
      final controller = WorkspaceController(repository);
      await controller.load();
      await controller.createTask('Publish the update');
      expect(
          controller.pendingApprovals.single.progressLabel, '0 of 2 approvals');
      expect(
          controller.pendingApprovalFor(repository.runTask)!.id, 'approval_1');
    });
  });

  testWidgets('task detail shows the approval progress right after the run',
      (tester) async {
    final task = _waiting(_model(required: 2));
    final controller = WorkspaceController(_Repository(task));
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.light(),
      builder: (context, content) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: const TextScaler.linear(2)),
        child:
            Directionality(textDirection: TextDirection.rtl, child: content!),
      ),
      home: TaskDetailScreen(controller: controller, task: task),
    ));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('task-approval')), findsOneWidget);
    expect(find.text('Waiting for a decision'), findsOneWidget);
    expect(
        find.byKey(const Key('approval-progress-approval_1')), findsOneWidget);
    expect(find.text('0 of 2 approvals'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
