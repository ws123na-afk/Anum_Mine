// Tasks (mockup 10): tabs, priority pills, due labels, the create sheet with
// priority and due date, planning edits, and layouts from 320 to 1440 wide
// at 200% text, in English and Arabic (docs/tasks.md, docs/mobile.md).
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/task_management.dart';
import 'package:anum_mobile/features/workspace/tasks_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'lists_harness.dart';

final _now = DateTime.now();

WorkspaceTask _task(int n,
        {WorkStatus status = WorkStatus.created,
        TaskPriority priority = TaskPriority.medium,
        DateTime? due,
        String createdBy = 'user_me'}) =>
    WorkspaceTask(
        id: 'task_$n',
        title: 'Task $n',
        prompt: 'Do task $n',
        status: status,
        createdAt: _now.subtract(Duration(minutes: n)),
        updatedAt: _now.subtract(Duration(minutes: n)),
        createdBy: createdBy,
        priority: priority,
        dueAt: due);

class _Fake implements WorkspaceRepository, TaskManagementRepository {
  _Fake(this.tasks);
  final List<WorkspaceTask> tasks;
  final queries = <TaskListQuery>[];
  final drafts = <TaskDraft>[];
  final plans = <String>[];

  List<WorkspaceTask> _matching(TaskListQuery q) => tasks
      .where((t) =>
          (q.statuses.isEmpty || q.statuses.contains(t.status)) &&
          (!q.mine || t.createdBy == 'user_me') &&
          t.title.toLowerCase().contains(q.search.toLowerCase()))
      .toList();

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
      tasks: tasks,
      approvals: const [],
      automations: const [],
      files: const [],
      memories: const []);

  @override
  Future<List<WorkspaceTask>> listTasks(TaskListQuery query,
      {String? cursor}) async {
    queries.add(query);
    return _matching(query);
  }

  @override
  Future<TaskSummaryCounts> taskSummary(TaskListQuery query) async {
    final by = <WorkStatus, int>{};
    for (final t in _matching(query)) {
      by[t.status] = (by[t.status] ?? 0) + 1;
    }
    return TaskSummaryCounts(
        total: _matching(query).length, byStatus: by, archived: 0);
  }

  @override
  Future<WorkspaceTask> createPlannedTask(TaskDraft draft) async {
    drafts.add(draft);
    final task = WorkspaceTask(
        id: 'task_new',
        title: draft.effectiveTitle,
        prompt: draft.prompt,
        status: draft.runNow ? WorkStatus.running : WorkStatus.created,
        createdAt: _now,
        updatedAt: _now,
        createdBy: 'user_me',
        priority: draft.priority,
        dueAt: draft.dueAt);
    tasks.insert(0, task);
    return task;
  }

  @override
  Future<WorkspaceTask> planTask(String taskId,
      {TaskPriority? priority, DateTime? dueAt, bool clearDue = false}) async {
    plans.add('$taskId:${priority?.name}:${clearDue ? 'clear' : dueAt}');
    final i = tasks.indexWhere((t) => t.id == taskId);
    final t = tasks[i];
    return tasks[i] = WorkspaceTask(
        id: t.id,
        title: t.title,
        prompt: t.prompt,
        status: t.status,
        createdAt: t.createdAt,
        updatedAt: _now,
        createdBy: t.createdBy,
        priority: priority ?? t.priority,
        dueAt: clearDue ? null : dueAt ?? t.dueAt);
  }

  @override
  Future<WorkspaceTask> loadTask(String taskId) async =>
      tasks.firstWhere((t) => t.id == taskId);

  @override
  Future<List<WorkspaceRun>> listRuns(String taskId, {String? cursor}) async =>
      const [];

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<WorkspaceController> _controller(_Fake fake) async {
  final c = WorkspaceController(fake)..currentUserId = 'user_me';
  await c.load();
  return c;
}

void main() {
  test('planning bodies, titles and due days', () {
    final due = DateTime(2026, 10, 20, 17);
    final draft = TaskDraft(
        title: '  ',
        prompt: 'Prepare the tyre cost comparison for September and more ' * 3,
        priority: TaskPriority.high,
        dueAt: due);
    final body = draft.body();
    expect(body['priority'], 'high');
    expect(body['due_at'], due.toUtc().toIso8601String());
    expect((body['title']! as String).length, 80);
    expect(taskPlanBody(clearDue: true), {'due_at': null});
    expect(taskPlanBody(priority: TaskPriority.low), {'priority': 'low'});
    final now = DateTime(2026, 10, 12, 10);
    expect(dueDay(DateTime(2026, 10, 12, 18), now: now), DueDay.today);
    expect(dueDay(DateTime(2026, 10, 12, 8), now: now), DueDay.overdue);
    expect(dueDay(DateTime(2026, 10, 13, 8), now: now), DueDay.tomorrow);
    expect(dueDay(DateTime(2026, 10, 20), now: now), DueDay.later);
    expect(taskPriorityFromWire(null), TaskPriority.medium);
    expect(taskTabQuery(TaskTab.mine).queryString(), 'created_by=me');
  });

  test('the API repository creates without running and plans with PUT',
      () async {
    final transport = ScriptedTransport((r) {
      if (r.method == 'POST' && r.uri.path == '/api/v1/tasks') {
        return ApiResponse(statusCode: 201, body: {
          'id': 'task_9',
          'title': r.body!['title'],
          'prompt': r.body!['prompt'],
          'status': 'created',
          'priority': r.body!['priority'],
          'due_at': r.body!['due_at'],
          'created_at': '2026-10-12T08:00:00Z',
          'updated_at': '2026-10-12T08:00:00Z',
        });
      }
      return ApiResponse(statusCode: 200, body: {
        'id': 'task_9',
        'title': 'T',
        'prompt': 'P',
        'status': 'created',
        'priority': 'low',
        'due_at': null,
        'created_at': '2026-10-12T08:00:00Z',
        'updated_at': '2026-10-12T08:00:00Z',
      });
    });
    final repo = ApiWorkspaceRepository(await scriptedClient(transport),
        fileTransfer: _NoFiles());
    final created = await repo.createPlannedTask(TaskDraft(
        title: 'Quote',
        prompt: 'Review the quote',
        priority: TaskPriority.high,
        dueAt: DateTime.utc(2026, 10, 20, 14),
        runNow: false));
    expect(transport.requests.single.uri.path, '/api/v1/tasks');
    expect(created.priority, TaskPriority.high);
    expect(created.dueAt!.toUtc(), DateTime.utc(2026, 10, 20, 14));
    final planned = await repo.planTask('task_9', clearDue: true);
    expect(transport.requests.last.method, 'PUT');
    expect(jsonEncode(transport.requests.last.body), '{"due_at":null}');
    expect(planned.priority, TaskPriority.low);
    expect(planned.dueAt, isNull);
  });

  testWidgets('tabs ask the server; rows show priority, status and due',
      (tester) async {
    final fake = _Fake([
      _task(1,
          priority: TaskPriority.high, due: _now.add(const Duration(hours: 1))),
      _task(2, status: WorkStatus.running, createdBy: 'user_other'),
      _task(3,
          status: WorkStatus.completed,
          priority: TaskPriority.low,
          due: _now.subtract(const Duration(days: 3))),
    ]);
    await pumpList(tester, TasksScreen(controller: await _controller(fake)));
    expect(find.text('High'), findsOneWidget);
    expect(find.text('Low'), findsOneWidget);
    expect(find.text('Running'), findsOneWidget);
    // A completed task past its date is not "Overdue".
    expect(find.text('Overdue'), findsNothing);
    await tester.tap(find.byKey(const Key('tasks-tab-agents')));
    await tester.pumpAndSettle();
    expect(find.text('Task 2'), findsOneWidget);
    expect(find.text('Task 1'), findsNothing);
    await tester.tap(find.byKey(const Key('tasks-tab-mine')));
    await tester.pumpAndSettle();
    expect(fake.queries.last.mine, isTrue);
    expect(find.text('Task 2'), findsNothing);
    await tester.tap(find.byKey(const Key('tasks-tab-completed')));
    await tester.pumpAndSettle();
    expect(find.text('Task 3'), findsOneWidget);
    await expectTapTargets(tester);
  });

  testWidgets('an empty tab says what appears there', (tester) async {
    final fake = _Fake([_task(1)]);
    await pumpList(tester, TasksScreen(controller: await _controller(fake)));
    await tester.tap(find.byKey(const Key('tasks-tab-agents')));
    await tester.pumpAndSettle();
    expect(find.text('No agent is working right now'), findsOneWidget);
    await tester.tap(find.text('Clear filters'));
    await tester.pumpAndSettle();
    expect(find.text('Task 1'), findsOneWidget);
  });

  testWidgets('the create sheet sends priority and saves without running',
      (tester) async {
    final fake = _Fake([]);
    await pumpList(tester, TasksScreen(controller: await _controller(fake)));
    expect(find.text('No tasks yet'), findsOneWidget);
    await tester.tap(find.byKey(const Key('tasks-fab')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('task-create-submit')));
    await tester.pumpAndSettle();
    expect(find.text('Say what the task should do.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('task-create-prompt')), 'Book flight tickets');
    await tester.tap(find.byKey(const Key('priority-high')));
    await tester.ensureVisible(find.byKey(const Key('task-create-run')));
    await tester.tap(find.byKey(const Key('task-create-run')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('task-create-submit')));
    await tester.tap(find.byKey(const Key('task-create-submit')));
    await tester.pumpAndSettle();
    expect(fake.drafts.single.priority, TaskPriority.high);
    expect(fake.drafts.single.runNow, isFalse);
    // The new task opens, with Start now and its planning controls.
    expect(find.byKey(const Key('task-run')), findsOneWidget);
    expect(find.text('No due date'), findsOneWidget);
    await tester.tap(find.byKey(const Key('task-priority')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('task-priority-low')));
    await tester.pumpAndSettle();
    expect(fake.plans.single, startsWith('task_new:low'));
    expect(find.text('Task updated'), findsOneWidget);
  });

  for (final width in listWidths) {
    testWidgets('lays out at $width wide, 200% text, English and Arabic',
        (tester) async {
      final fake = _Fake([
        _task(1,
            priority: TaskPriority.high,
            due: _now.add(const Duration(days: 4))),
        _task(2, status: WorkStatus.waitingApproval),
      ]);
      for (final locale in ['en', 'ar']) {
        await pumpList(tester, TasksScreen(controller: await _controller(fake)),
            width: width, text: 2, locale: locale);
        expect(tester.takeException(), isNull);
      }
      expect(find.text('عالية'), findsOneWidget);
      expect(Directionality.of(tester.element(find.text('عالية'))),
          TextDirection.rtl);
    });
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
