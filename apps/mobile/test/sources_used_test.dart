// "Sources used" on a run (docs/memory.md, At run time; docs/mobile.md):
// read only from the run's `retrieval` step, never invented.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/retrieval_sources.dart';
import 'package:anum_mobile/features/workspace/tasks_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _time = DateTime.utc(2026, 10, 6, 10);

RunStep _step(String type, [Map<String, Object?> metadata = const {}]) =>
    RunStep(
        id: 'step_$type',
        type: type,
        summary: '$type summary',
        createdAt: _time,
        metadata: metadata);

const _okMetadata = <String, Object?>{
  'status': 'ok',
  'embedding_model': 'anum-local-hash-v1',
  'truncated': true,
  'max_chars': 6000,
  'sources': [
    {
      'chunk_id': 'chunk_1',
      'source_type': 'memory',
      'source_id': 'memory_1',
      'chunk_index': 0,
      'score': 0.874,
      'truncated': false,
    },
    {
      'chunk_id': 'chunk_2',
      'source_type': 'file',
      'source_id': 'file_1',
      'chunk_index': 2,
      'score': 0.5,
      'truncated': true,
    },
    {
      'chunk_id': 'chunk_3',
      'source_type': 'memory',
      'source_id': 'memory_1',
      'chunk_index': 3,
      'score': 0.4,
      'truncated': false,
    },
    {
      'chunk_id': 'chunk_4',
      'source_type': 'file',
      'source_id': 'file_gone',
      'chunk_index': 0,
      'score': 0.3,
    },
    // Malformed entries are dropped, not shown with made-up values.
    {'chunk_id': 'chunk_5', 'source_type': 'web', 'source_id': 'x'},
    {'source_type': 'file', 'source_id': 'file_1'},
    'not a map',
  ],
};

WorkspaceRun _run(List<RunStep> steps) => WorkspaceRun(
    id: 'run_1', status: WorkStatus.completed, steps: steps, result: 'Done');

WorkspaceTask _task(WorkspaceRun? run) => WorkspaceTask(
    id: 'task_1',
    title: 'Summarise incidents',
    prompt: 'Summarise incidents',
    status: WorkStatus.completed,
    createdAt: _time,
    updatedAt: _time,
    run: run);

class _Repository implements WorkspaceRepository {
  _Repository(this.task);
  final WorkspaceTask task;

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
        tasks: [task],
        approvals: const [],
        automations: const [],
        files: [
          WorkspaceFile(
              id: 'file_1',
              name: 'incidents.md',
              contentType: 'text/markdown',
              sizeBytes: 2048,
              createdAt: _time),
        ],
        memories: [
          WorkspaceMemory(
              id: 'memory_1',
              taskId: 'task_1',
              content: 'Incidents are summarised every Friday',
              sourceType: 'mobile',
              createdAt: _time),
        ],
      );

  @override
  Future<WorkspaceTask> loadTask(String taskId) async => task;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<void> _pump(WidgetTester tester, WorkspaceTask task,
    {TextDirection direction = TextDirection.ltr, double textScale = 1}) async {
  tester.view.physicalSize = const Size(360, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final controller = WorkspaceController(_Repository(task));
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.light(),
    builder: (context, content) => MediaQuery(
      data: MediaQuery.of(context)
          .copyWith(textScaler: TextScaler.linear(textScale)),
      child: Directionality(textDirection: direction, child: content!),
    ),
    home: TaskDetailScreen(controller: controller, task: task),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('a run without a retrieval step has no sources record', () {
    expect(runSources(null), isNull);
    expect(runSources(_run([_step('model_call'), _step('final')])), isNull);
  });

  test('the retrieval step maps to grouped passages', () {
    final record =
        runSources(_run([_step('retrieval', _okMetadata), _step('final')]))!;
    expect(record.state, RetrievalState.ok);
    expect(record.truncated, isTrue);
    expect(record.embeddingModel, 'anum-local-hash-v1');
    expect(record.sources.map((s) => s.chunkId),
        ['chunk_1', 'chunk_2', 'chunk_3', 'chunk_4']);
    final groups = groupSources(record.sources);
    expect(groups.map((g) => '${g.sourceType}:${g.sourceId}'),
        ['memory:memory_1', 'file:file_1', 'file:file_gone']);
    expect(groups.first.passages.map(passageLabel),
        ['passage 1, 87% match', 'passage 4, 40% match']);
    expect(passageLabel(groups[1].passages.single),
        'passage 3, 50% match, shortened');
    expect(sourcesNote(record),
        'Some retrieved text was shortened to fit the prompt limit.');
  });

  test('empty and degraded retrieval explain themselves', () {
    RunSources of(Map<String, Object?> metadata) =>
        runSources(_run([_step('retrieval', metadata)]))!;
    expect(sourcesNote(of({'status': 'no_results', 'sources': <Object?>[]})),
        'No indexed memory or file matched this task.');
    expect(sourcesNote(of({'status': 'skipped', 'reason': 'no memory access'})),
        'Retrieval skipped: no memory access.');
    final unknown = of({'status': 'something_new'});
    expect(unknown.state, RetrievalState.unavailable);
    expect(unknown.sources, isEmpty);
    expect(scoreLabel(1.7), '100% match');
    expect(scoreLabel(-1), '0% match');
  });

  test('the repository keeps step metadata from the API', () async {
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
    final repository = ApiWorkspaceRepository(
        AnumApiClient(
            baseUri: Uri.parse('http://localhost:8000'),
            transport: _TaskTransport(),
            sessions: sessions),
        fileTransfer: _NoFiles());
    final task = await repository.loadTask('task_1');
    final record = runSources(task.run)!;
    expect(record.sources.single.sourceId, 'memory_1');
    expect(task.run!.steps.last.metadata, isEmpty);
  });

  testWidgets('task detail lists the sources a run used', (tester) async {
    await _pump(
        tester, _task(_run([_step('retrieval', _okMetadata), _step('final')])));
    expect(find.text('Sources used'), findsOneWidget);
    expect(find.text('3 sources'), findsOneWidget);
    // Labels come from the loaded workspace; an unlisted source shows its id.
    expect(find.text('Incidents are summarised every Friday'), findsOneWidget);
    expect(find.text('incidents.md'), findsOneWidget);
    expect(find.text('File \u2068file_gone\u2069'), findsOneWidget);
    expect(find.text('passage 1, 87% match; passage 4, 40% match'),
        findsOneWidget);
    expect(
        find.bySemanticsLabel(
            'File incidents.md, passage 3, 50% match, shortened, '
            'opens file details'),
        findsOneWidget);
    expect(
        find.bySemanticsLabel('Memory Incidents are summarised every Friday, '
            'passage 1, 87% match; passage 4, 40% match'),
        findsOneWidget);
    expect(
        find.text('Some retrieved text was shortened to fit the prompt limit.'),
        findsOneWidget);

    await tester.ensureVisible(find.text('incidents.md'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('incidents.md'));
    await tester.pumpAndSettle();
    expect(find.text('Download'), findsOneWidget);
  });

  testWidgets('a run without a retrieval step shows no sources section',
      (tester) async {
    await _pump(tester, _task(_run([_step('model_call'), _step('final')])));
    expect(find.text('Sources used'), findsNothing);
    expect(find.byKey(const Key('sources-used')), findsNothing);
  });

  testWidgets('sources fit a phone in Arabic RTL at 200 percent text',
      (tester) async {
    await _pump(
        tester, _task(_run([_step('retrieval', _okMetadata), _step('final')])),
        direction: TextDirection.rtl, textScale: 2);
    await tester.scrollUntilVisible(find.text('incidents.md'), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.text('incidents.md'), findsOneWidget);
    expect(
        Directionality.of(
            tester.element(find.byKey(const Key('sources-used')))),
        TextDirection.rtl);
    expect(tester.takeException(), isNull);
  });
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

class _TaskTransport implements ApiTransport {
  static const _time = '2026-10-06T10:00:00Z';
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    if (request.uri.path.endsWith('/latest-run')) {
      return const ApiResponse(statusCode: 200, body: {
        'id': 'run_1',
        'status': 'completed',
        'result': 'Done',
        'steps': [
          {
            'id': 'step_1',
            'type': 'retrieval',
            'summary': 'Used 1 passages from 1 workspace source.',
            'created_at': _time,
            'metadata': {
              'status': 'ok',
              'sources': [
                {
                  'chunk_id': 'chunk_1',
                  'source_type': 'memory',
                  'source_id': 'memory_1',
                  'chunk_index': 0,
                  'score': 0.9,
                  'truncated': false,
                }
              ],
            },
          },
          {
            'id': 'step_2',
            'type': 'final',
            'summary': 'Done',
            'created_at': _time,
          },
        ],
      });
    }
    return const ApiResponse(statusCode: 200, body: {
      'id': 'task_1',
      'title': 'Summarise incidents',
      'prompt': 'Summarise incidents',
      'status': 'completed',
      'created_at': _time,
      'updated_at': _time,
    });
  }
}
