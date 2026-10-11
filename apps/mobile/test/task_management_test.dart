// Task management on Flutter (docs/tasks.md): server-side filters and keyset
// paging, archived tasks, retry with run history, rename, archive and restore,
// the run-log export, refusals, and layouts from 320 to 1440 wide at 200% text.
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/task_management.dart';
import 'package:anum_mobile/features/workspace/tasks_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

final _at = DateTime.utc(2026, 10, 10, 9);

WorkspaceTask _task(int n,
        {WorkStatus status = WorkStatus.completed,
        String? title,
        DateTime? archivedAt,
        WorkspaceRun? run}) =>
    WorkspaceTask(
        id: 'task_$n',
        title: title ?? 'Report $n',
        prompt: 'Write report $n',
        status: status,
        createdAt: _at.add(Duration(minutes: n)),
        updatedAt: _at.add(Duration(minutes: n)),
        createdBy: 'user_test',
        archivedAt: archivedAt,
        run: run);

WorkspaceRun _run(String id, WorkStatus status, String summary) => WorkspaceRun(
    id: id,
    status: status,
    createdAt: _at,
    steps: [
      RunStep(id: '${id}_s', type: 'final', summary: summary, createdAt: _at)
    ],
    result: status == WorkStatus.completed ? summary : null);

/// A workspace repository with task management, backed by a list.
class _Fake implements WorkspaceRepository, TaskManagementRepository {
  _Fake(this.tasks);
  final List<WorkspaceTask> tasks;
  final queries = <TaskListQuery>[];
  final cursors = <String?>[];
  final runs = <String, List<WorkspaceRun>>{};
  String? refuse;

  List<WorkspaceTask> _matching(TaskListQuery q) => tasks
      .where((t) =>
          (t.archivedAt != null) == q.archived &&
          (q.statuses.isEmpty || q.statuses.contains(t.status)) &&
          (q.search.isEmpty ||
              t.title.toLowerCase().contains(q.search.toLowerCase())))
      .toList()
    ..sort((a, b) => b.createdAt.compareTo(a.createdAt));

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
      tasks: _matching(const TaskListQuery()).take(taskPageSize).toList(),
      approvals: const [],
      automations: const [],
      files: const [],
      memories: const []);

  @override
  Future<List<WorkspaceTask>> listTasks(TaskListQuery query,
      {String? cursor}) async {
    queries.add(query);
    cursors.add(cursor);
    var rows = _matching(query);
    if (cursor != null) {
      rows = rows.sublist(rows.indexWhere((t) => t.id == cursor) + 1);
    }
    return rows.take(taskPageSize).toList();
  }

  @override
  Future<TaskSummaryCounts> taskSummary(TaskListQuery query) async {
    final rows = _matching(TaskListQuery(search: query.search));
    final by = <WorkStatus, int>{};
    for (final t in rows) {
      by[t.status] = (by[t.status] ?? 0) + 1;
    }
    return TaskSummaryCounts(
        total: rows.length,
        byStatus: by,
        archived: tasks.where((t) => t.archivedAt != null).length);
  }

  WorkspaceTask _replace(String id, WorkspaceTask Function(WorkspaceTask) f) {
    final index = tasks.indexWhere((t) => t.id == id);
    return tasks[index] = f(tasks[index]);
  }

  Future<WorkspaceTask> _guard(Future<WorkspaceTask> Function() f) async {
    if (refuse case final message?) throw ApiException(403, message);
    return f();
  }

  @override
  Future<WorkspaceTask> loadTask(String taskId) async =>
      tasks.firstWhere((t) => t.id == taskId);

  @override
  Future<List<WorkspaceRun>> listRuns(String taskId, {String? cursor}) async =>
      runs[taskId] ?? const [];

  @override
  Future<WorkspaceTask> retryTask(String taskId) => _guard(() async {
        final run = _run('run_${taskId}_2', WorkStatus.completed, 'Second try');
        runs[taskId] = [run, ...?runs[taskId]];
        return _replace(
            taskId,
            (t) => _task(int.parse(taskId.split('_').last),
                title: t.title, status: WorkStatus.completed, run: run));
      });

  @override
  Future<WorkspaceTask> renameTask(String taskId, String title) =>
      _guard(() async => _replace(
          taskId,
          (t) => _task(int.parse(taskId.split('_').last),
              title: title, status: t.status, run: t.run)));

  @override
  Future<WorkspaceTask> archiveTask(String taskId) =>
      _guard(() async => _replace(
          taskId,
          (t) => _task(int.parse(taskId.split('_').last),
              title: t.title, status: t.status, archivedAt: _at, run: t.run)));

  @override
  Future<WorkspaceTask> unarchiveTask(String taskId) => _guard(() async =>
      _replace(
          taskId,
          (t) => _task(int.parse(taskId.split('_').last),
              title: t.title, status: t.status, run: t.run)));

  @override
  Future<String> exportRunLog(String taskId) async =>
      '{"format": "anum.task-run-log/v1", "task": "$taskId"}';

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<WorkspaceController> _pump(WidgetTester tester, _Fake fake,
    {Widget Function(WorkspaceController)? home,
    double width = 390,
    double height = 2400,
    double text = 1}) async {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  final controller = WorkspaceController(fake);
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.light(),
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(text)),
        child: child!),
    home: Scaffold(
        body: home?.call(controller) ?? TasksScreen(controller: controller)),
  ));
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  test('the query string names statuses, search, archive and the cursor', () {
    const query = TaskListQuery(
        statuses: {WorkStatus.waitingApproval},
        search: '  launch plan ',
        archived: true);
    expect(query.queryString(cursor: 'task_9', limit: 50),
        'limit=50&cursor=task_9&status=waiting_approval&q=launch+plan&archived=true');
    expect(query.queryString(withStatus: false), 'q=launch+plan&archived=true');
    expect(const TaskListQuery().isDefault, isTrue);
    expect(const TaskListQuery(search: 'x') == const TaskListQuery(search: 'x'),
        isTrue);
    expect(runLabel(0, 3), 'Run 3 (latest)');
    expect(runLabel(2, 3), 'Run 1');
  });

  test('actions follow the task state', () {
    final failed = TaskActionSet.of(_task(1, status: WorkStatus.failed));
    expect([failed.retry, failed.archive, failed.cancel], [true, true, false]);
    final running = TaskActionSet.of(_task(1, status: WorkStatus.running));
    expect(
        [running.retry, running.archive, running.cancel], [false, false, true]);
    final archived = TaskActionSet.of(_task(1, archivedAt: _at));
    expect([archived.unarchive, archived.rename, archived.archive],
        [true, false, false]);
  });

  test('the API repository maps runs, archive fields and the export', () async {
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession(
        accessToken: 'token',
        tokenType: 'bearer',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
        context: const TenantContext(
            tenantId: 't', workspaceId: 'w', userId: 'u', roles: ['owner'])));
    final transport = _Transport();
    final repository = ApiWorkspaceRepository(
        AnumApiClient(
            baseUri: Uri.parse('http://localhost:8000'),
            transport: transport,
            sessions: sessions),
        fileTransfer: _NoFiles());
    final page = await repository.listTasks(
        const TaskListQuery(statuses: {WorkStatus.failed}),
        cursor: 'task_0');
    expect(transport.paths.last,
        '/api/v1/tasks?limit=50&cursor=task_0&status=failed');
    expect(page.single.archivedAt, isNotNull);
    expect(page.single.createdBy, 'user_a');
    final summary = await repository.taskSummary(const TaskListQuery());
    expect(summary.count({WorkStatus.failed}), 2);
    expect(summary.archived, 1);
    final runs = await repository.listRuns('task_1');
    expect(runs.single.createdAt, isNotNull);
    expect(transport.paths.last, '/api/v1/tasks/task_1/runs?limit=20');
    final retried = await repository.retryTask('task_1');
    expect(retried.run!.id, 'run_2');
    final log = await repository.exportRunLog('task_1');
    expect(jsonDecode(log)['format'], 'anum.task-run-log/v1');
    expect(transport.paths.last, '/api/v1/tasks/task_1/export?format=json');
  });

  testWidgets('filters and paging run on the server', (tester) async {
    final fake = _Fake([
      for (var i = 1; i <= 55; i++)
        _task(i,
            status: i == 3 ? WorkStatus.failed : WorkStatus.completed,
            title: i == 3 ? 'Invoice summary' : null),
    ]);
    await _pump(tester, fake);
    expect(find.text('55 in this workspace'), findsOneWidget);
    await tester.dragUntilVisible(find.byKey(const Key('tasks-load-more')),
        find.byType(ListView).first, const Offset(0, -600));
    await tester.ensureVisible(find.byKey(const Key('tasks-load-more')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('tasks-load-more')));
    await tester.pumpAndSettle();
    expect(fake.cursors.last, 'task_6');

    await tester.drag(find.byType(ListView).first, const Offset(0, 20000));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'invoice');
    await tester.pump(const Duration(milliseconds: 350));
    await tester.pumpAndSettle();
    expect(fake.queries.last.search, 'invoice');
    expect(find.text('Invoice summary'), findsOneWidget);
    expect(find.text('Report 1'), findsNothing);

    await tester.ensureVisible(find.textContaining('Failed or cancelled'));
    await tester.tap(find.textContaining('Failed or cancelled'));
    await tester.pumpAndSettle();
    expect(
        fake.queries.last.statuses, {WorkStatus.failed, WorkStatus.cancelled});
    expect(tester.takeException(), isNull);
  });

  testWidgets('archived tasks have their own list and an empty state',
      (tester) async {
    final fake = _Fake([_task(1), _task(2, archivedAt: _at)]);
    await _pump(tester, fake);
    expect(find.text('Report 2'), findsNothing);
    await tester.ensureVisible(find.byKey(const Key('tasks-archived')));
    await tester.tap(find.byKey(const Key('tasks-archived')));
    await tester.pumpAndSettle();
    expect(find.text('Archived tasks'), findsOneWidget);
    expect(find.text('Report 2'), findsOneWidget);
    expect(fake.queries.last.archived, isTrue);
    await tester.enterText(find.byType(TextField).last, 'nothing');
    await tester.pump(const Duration(milliseconds: 350));
    await tester.pumpAndSettle();
    expect(find.text('No archived tasks'), findsOneWidget);
    await tester.tap(find.text('Clear filters'));
    await tester.pumpAndSettle();
    expect(find.text('Report 1'), findsOneWidget);
  });

  testWidgets('a failed list load says what failed and retries',
      (tester) async {
    final fake = _FailingOnce([_task(1)]);
    await _pump(tester, fake);
    expect(find.byKey(const Key('tasks-error')), findsOneWidget);
    expect(find.text('Database unavailable'), findsOneWidget);
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('tasks-error')), findsNothing);
    expect(find.text('Report 1'), findsOneWidget);
  });

  testWidgets('retry adds a run and the history switches between runs',
      (tester) async {
    final failedRun = _run('run_task_1_1', WorkStatus.failed, 'First failure');
    final fake = _Fake([
      _task(1, status: WorkStatus.failed, title: 'Invoices', run: failedRun)
    ])
      ..runs['task_1'] = [failedRun];
    await _pump(tester, fake,
        home: (c) => TaskDetailScreen(controller: c, task: fake.tasks.first));
    expect(find.text('First failure'), findsOneWidget);
    await tester.tap(find.byKey(const Key('task-retry')));
    await tester.pumpAndSettle();
    expect(find.text('Retry started'), findsOneWidget);
    expect(find.byKey(const Key('task-run-run_task_1_2')), findsOneWidget);
    expect(find.text('Run 2 (latest) · Completed'), findsOneWidget);
    expect(find.text('Second try'), findsWidgets);
    await tester.ensureVisible(find.byKey(const Key('task-run-run_task_1_1')));
    await tester.tap(find.byKey(const Key('task-run-run_task_1_1')));
    await tester.pumpAndSettle();
    expect(find.text('First failure'), findsOneWidget);
    expect(find.byKey(const Key('task-retry')), findsNothing);
  });

  testWidgets('rename, archive, restore and copy the run log', (tester) async {
    final fake = _Fake([_task(1, title: 'Old title')]);
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String;
      }
      return null;
    });
    await _pump(tester, fake,
        home: (c) => TaskDetailScreen(controller: c, task: fake.tasks.first));
    await tester.tap(find.byKey(const Key('task-rename')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'New title');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(find.text('New title'), findsOneWidget);

    await tester.tap(find.byKey(const Key('task-archive')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('task-unarchive')), findsOneWidget);
    expect(find.byKey(const Key('task-rename')), findsNothing);
    await tester.tap(find.byKey(const Key('task-unarchive')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('task-archive')), findsOneWidget);

    await tester.tap(find.byKey(const Key('task-export')));
    await tester.pumpAndSettle();
    expect(copied, contains('anum.task-run-log/v1'));
    expect(find.text('Run log copied as JSON'), findsOneWidget);
  });

  testWidgets('a refusal names who may act and keeps the screen usable',
      (tester) async {
    final fake = _Fake([_task(1)])
      ..refuse =
          "Only the task's creator or a workspace owner can change this task.";
    final controller = await _pump(tester, fake,
        home: (c) => TaskDetailScreen(controller: c, task: fake.tasks.first));
    await tester.tap(find.byKey(const Key('task-archive')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('task-refusal')), findsOneWidget);
    expect(controller.phase, isNot(LoadPhase.error));
    expect(find.byKey(const Key('task-archive')), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('list and detail lay out at $width wide with 200% text',
        (tester) async {
      final run = _run('run_task_1_2', WorkStatus.completed, 'Answer ' * 40);
      final fake = _Fake([
        _task(1,
            status: WorkStatus.failed,
            title: 'A long task title that has to wrap on a narrow phone',
            run: run),
        _task(2, archivedAt: _at),
        _task(3, status: WorkStatus.waitingApproval),
      ])
        ..runs['task_1'] = [run, _run('run_task_1_1', WorkStatus.failed, 'x')];
      await _pump(tester, fake, width: width, text: 2);
      expect(tester.takeException(), isNull);
      await _pump(tester, fake,
          width: width,
          text: 2,
          home: (c) => TaskDetailScreen(controller: c, task: fake.tasks.first));
      expect(find.text('Run 2 (latest) · Completed'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}

class _FailingOnce extends _Fake {
  _FailingOnce(super.tasks);
  bool failed = false;
  @override
  Future<List<WorkspaceTask>> listTasks(TaskListQuery query,
      {String? cursor}) async {
    if (!failed) {
      failed = true;
      throw const ApiException(503, 'Database unavailable');
    }
    return super.listTasks(query, cursor: cursor);
  }
}

class _Transport implements ApiTransport {
  final paths = <String>[];
  static const _time = '2026-10-10T09:00:00Z';
  static const _runJson = {
    'id': 'run_2',
    'task_id': 'task_1',
    'status': 'completed',
    'steps': <Object?>[],
    'created_at': _time,
  };
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    paths.add(request.uri.toString().replaceFirst('http://localhost:8000', ''));
    final path = request.uri.path;
    if (path == '/api/v1/tasks') {
      return const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'id': 'task_1',
            'title': 'T',
            'prompt': 'P',
            'status': 'failed',
            'created_at': _time,
            'updated_at': _time,
            'created_by': 'user_a',
            'archived_at': _time,
          }
        ]
      });
    }
    if (path == '/api/v1/tasks/summary') {
      return const ApiResponse(statusCode: 200, body: {
        'total': 3,
        'by_status': {'failed': 2, 'completed': 1},
        'archived': 1,
        'creators': ['user_a'],
      });
    }
    if (path.endsWith('/runs')) {
      return const ApiResponse(statusCode: 200, body: {
        'data': [_runJson]
      });
    }
    if (path.endsWith('/retry')) {
      return const ApiResponse(statusCode: 200, body: {
        'task': {
          'id': 'task_1',
          'title': 'T',
          'prompt': 'P',
          'status': 'completed',
          'created_at': _time,
          'updated_at': _time,
        },
        'run': _runJson,
        'approval': null,
      });
    }
    if (path.endsWith('/export')) {
      return const ApiResponse(
          statusCode: 200, body: {'format': 'anum.task-run-log/v1'});
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'missing'});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}
