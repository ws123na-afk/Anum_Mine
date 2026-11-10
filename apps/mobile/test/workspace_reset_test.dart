// Sign-out forgets the workspace, so the next person loads their own
// (docs/mobile.md#per-user-state).
import 'dart:async';

import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:flutter_test/flutter_test.dart';

WorkspaceSnapshot snapshotWith(String title) => WorkspaceSnapshot(
      tasks: [
        WorkspaceTask(
            id: 'task_$title',
            title: title,
            prompt: title,
            status: WorkStatus.completed,
            createdAt: DateTime.utc(2026, 11, 1),
            updatedAt: DateTime.utc(2026, 11, 1)),
      ],
      approvals: const [],
      automations: const [],
      files: const [],
      memories: const [],
    );

class _Repository implements WorkspaceRepository {
  final pending = <Completer<WorkspaceSnapshot>>[];

  @override
  Future<WorkspaceSnapshot> loadWorkspace() {
    final next = Completer<WorkspaceSnapshot>();
    pending.add(next);
    return next.future;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  test('reset returns to the initial phase with nothing of the last person',
      () async {
    final repository = _Repository();
    final controller = WorkspaceController(repository)
      ..currentUserId = 'user_owner';
    final loading = controller.load();
    repository.pending.single.complete(snapshotWith('Renew the trade licence'));
    await loading;
    expect(controller.tasks.single.title, 'Renew the trade licence');
    controller.decisionRefusals['approval_1'] = 'Not allowed';

    controller.reset();

    expect(controller.phase, LoadPhase.initial);
    expect(controller.snapshot, isNull);
    expect(controller.tasks, isEmpty);
    expect(controller.currentUserId, isNull);
    expect(controller.decisionRefusals, isEmpty);
    expect(controller.message, isNull);
  });

  test('an answer on its way when the person signs out is not shown', () async {
    final repository = _Repository();
    final controller = WorkspaceController(repository);
    final first = controller.load();
    controller.reset();
    // The next person signs in and loads before the old answer arrives.
    final second = controller.load();
    repository.pending[0].complete(snapshotWith('Renew the trade licence'));
    await first;
    expect(controller.snapshot, isNull);
    repository.pending[1].complete(snapshotWith('Order the new tyres'));
    await second;
    expect(controller.tasks.single.title, 'Order the new tyres');
  });
}
