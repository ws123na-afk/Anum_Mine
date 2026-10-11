// "Sources used" on a run (docs/memory.md, At run time; docs/mobile.md):
// read only from the run's `retrieval` step, never invented.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/retrieval_sources.dart';
import 'package:anum_mobile/features/workspace/tasks_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/depth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
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
  _Repository(this.task, {Future<WorkspaceTask> Function()? loadTask})
      : _loadTask = loadTask;
  final WorkspaceTask task;
  final Future<WorkspaceTask> Function()? _loadTask;
  int taskLoads = 0;

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
          WorkspaceMemory(
              id: 'memory_other',
              taskId: 'task_1',
              content: 'An unrelated note',
              sourceType: 'mobile',
              createdAt: _time.add(const Duration(hours: 1))),
        ],
      );

  @override
  Future<WorkspaceTask> loadTask(String taskId) {
    taskLoads++;
    return _loadTask?.call() ?? Future.value(task);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// The list's summary: no run until the detail fetches the latest one.
WorkspaceTask _summary() => _task(null);

Future<_Repository> _pump(WidgetTester tester, WorkspaceTask task,
    {TextDirection direction = TextDirection.ltr,
    double textScale = 1,
    Size size = const Size(360, 800),
    Locale? locale,
    Future<WorkspaceTask> Function()? loadTask,
    bool settle = true}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final repository = _Repository(task, loadTask: loadTask);
  final controller = WorkspaceController(repository);
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.light(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: locale == null
        ? null
        : const [
            AnumLocalizations.delegate,
            ...GlobalMaterialLocalizations.delegates
          ],
    builder: (context, content) => MediaQuery(
      data: MediaQuery.of(context)
          .copyWith(textScaler: TextScaler.linear(textScale)),
      child: locale != null
          ? content!
          : Directionality(textDirection: direction, child: content!),
    ),
    home: TaskDetailScreen(controller: controller, task: _summary()),
  ));
  if (settle) await tester.pumpAndSettle();
  return repository;
}

Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(finder, 200,
      scrollable: find.byType(Scrollable).first);
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

  test('every Sources used string has an Arabic translation', () {
    const english = AnumLocalizations(Locale('en'));
    const arabic = AnumLocalizations(Locale('ar'));
    for (final key in [
      'sourcesEyebrow',
      'sourcesTitle',
      'sourcesNone',
      'sourcesOne',
      'sourcesMany',
      'sourcesNoneSpoken',
      'sourcesCountSpoken',
      'sourcesMemory',
      'sourcesFile',
      'sourcesPassage',
      'sourcesShortened',
      'sourcesOpensFile',
      'sourcesOpensMemory',
      'sourcesTruncated',
      'sourcesNoResults',
      'sourcesSkipped',
      'sourcesSkippedReason',
      'sourcesUnavailable',
      'sourcesUntrusted',
      'sourcesLoading',
      'sourcesErrorTitle',
      'sourcesErrorHint',
      'sourcesDeniedHint',
      'retry',
      'memoryUsedByRun',
    ]) {
      expect(english.t(key), isNot(key), reason: key);
      expect(arabic.t(key), isNot(english.t(key)), reason: key);
    }
    expect(sourcesCountLabel(0), 'none');
    expect(sourcesCountLabel(1), '1 source');
    expect(sourcesCountLabel(4), '4 sources');
    expect(sourcesCountLabel(4, arabic), 'المصادر: 4');
    const passage = RetrievedSourceUsed(
        chunkId: 'c',
        sourceType: 'file',
        sourceId: 'f',
        chunkIndex: 1,
        score: 0.5,
        truncated: true);
    expect(passageLabel(passage, arabic), 'المقطع 2، تطابق 50٪، مختصر');
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
            'passage 1, 87% match; passage 4, 40% match, opens it in Memory'),
        findsOneWidget);
    // A source the workspace no longer lists is not a dead button.
    expect(find.bySemanticsLabel('File file_gone, passage 1, 30% match'),
        findsOneWidget);
    expect(
        find.text('Some retrieved text was shortened to fit the prompt limit.'),
        findsOneWidget);

    await _scrollTo(tester, find.text('incidents.md'));
    await tester.tap(find.text('incidents.md'));
    await tester.pumpAndSettle();
    expect(find.text('Download'), findsOneWidget);
  });

  testWidgets('a memory source opens in the Memory tab, marked',
      (tester) async {
    await _pump(
        tester, _task(_run([_step('retrieval', _okMetadata), _step('final')])));
    final memory = find.descendant(
        of: find.byKey(const Key('sources-used')),
        matching: find.text('Incidents are summarised every Friday'));
    await _scrollTo(tester, memory);
    await tester.tap(memory);
    await tester.pumpAndSettle();

    expect(find.widgetWithText(AppBar, 'Memory'), findsOneWidget);
    expect(find.text('Teach your agents'), findsOneWidget);
    expect(find.text('An unrelated note'), findsOneWidget);
    final used = find.textContaining('Used by the run you came from');
    expect(used, findsOneWidget);
    // The mark sits on the memory the run used, not on the other one.
    expect(
        find.ancestor(
            of: used,
            matching: find.ancestor(
                of: find.text('Incidents are summarised every Friday'),
                matching: find.byType(AnumRow))),
        findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('Sources used'), findsOneWidget);
  });

  testWidgets('retrieval that found nothing says so', (tester) async {
    await _pump(
        tester,
        _task(_run([
          _step('retrieval', {'status': 'no_results', 'sources': <Object?>[]}),
          _step('final'),
        ])));
    await _scrollTo(tester, find.byKey(const Key('sources-used')));
    expect(find.text('none'), findsOneWidget);
    expect(find.bySemanticsLabel('No sources used'), findsOneWidget);
    expect(find.text('Try again'), findsNothing);
    expect(find.text('No indexed memory or file matched this task.'),
        findsOneWidget);
    expect(
        find.descendant(
            of: find.byKey(const Key('sources-used')),
            matching: find.byType(AnumRow)),
        findsNothing);
  });

  testWidgets('a run without a retrieval step shows no sources section',
      (tester) async {
    await _pump(tester, _task(_run([_step('model_call'), _step('final')])));
    expect(find.text('Sources used'), findsNothing);
    expect(find.byKey(const Key('sources-used')), findsNothing);
  });

  testWidgets('the panel says so while the latest run loads', (tester) async {
    final pending = Completer<WorkspaceTask>();
    await _pump(tester, _task(null),
        loadTask: () => pending.future, settle: false);
    await tester.pump();
    expect(find.byKey(const Key('sources-used')), findsOneWidget);
    expect(find.text("Loading the run's sources…"), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // The run arrives without a retrieval step: the panel goes away.
    pending.complete(_task(_run([_step('model_call'), _step('final')])));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('sources-used')), findsNothing);
  });

  testWidgets('a failed run load explains itself and retries', (tester) async {
    var fail = true;
    final repository = await _pump(tester, _task(null),
        loadTask: () async => fail
            ? throw const ApiException(503, 'Service unavailable')
            : _task(_run([_step('retrieval', _okMetadata), _step('final')])));
    await _scrollTo(tester, find.byKey(const Key('sources-used')));
    expect(find.text("Couldn't load the sources used"), findsOneWidget);
    expect(
        find.text('Service unavailable. Check your connection, then try '
            'again.'),
        findsOneWidget);
    expect(repository.taskLoads, 1);

    fail = false;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(repository.taskLoads, 2);
    expect(find.text("Couldn't load the sources used"), findsNothing);
    expect(find.text('3 sources'), findsOneWidget);
  });

  testWidgets('a refused run load says who can see the sources',
      (tester) async {
    await _pump(tester, _task(null),
        loadTask: () async =>
            throw const ApiException(403, 'You cannot read this task'));
    await _scrollTo(tester, find.byKey(const Key('sources-used')));
    expect(find.text("Couldn't load the sources used"), findsOneWidget);
    expect(
        find.text('You cannot read this task. Members with task access can '
            'see them; ask a workspace owner for access.'),
        findsOneWidget);
    // Retrying cannot grant access.
    expect(find.text('Try again'), findsNothing);
  });

  testWidgets('sources fit a phone in Arabic RTL at 200 percent text',
      (tester) async {
    await _pump(
        tester, _task(_run([_step('retrieval', _okMetadata), _step('final')])),
        locale: const Locale('ar'), textScale: 2);
    await _scrollTo(tester, find.text('incidents.md'));
    expect(find.text('incidents.md'), findsOneWidget);
    expect(find.text('المصادر المستخدمة'), findsOneWidget);
    expect(find.text('المصادر: 3'), findsOneWidget);
    expect(find.text('ملف \u2068file_gone\u2069'), findsOneWidget);
    expect(
        Directionality.of(
            tester.element(find.byKey(const Key('sources-used')))),
        TextDirection.rtl);
    expect(tester.takeException(), isNull);
  });

  group('layout', () {
    const sizes = {
      'phone small 320x568': Size(320, 568),
      'phone 390x844': Size(390, 844),
      'tablet 768x1024': Size(768, 1024),
      'desktop 1440x900': Size(1440, 900),
    };
    for (final entry in sizes.entries) {
      for (final scale in [1.0, 2.0]) {
        for (final direction in TextDirection.values) {
          testWidgets(
              '${entry.key}, ${scale}x text, ${direction.name}: fits '
              'without overflow', (tester) async {
            await _pump(tester,
                _task(_run([_step('retrieval', _okMetadata), _step('final')])),
                size: entry.value, textScale: scale, direction: direction);
            final panel = find.byKey(const Key('sources-used'));
            await _scrollTo(tester, panel);
            final rect = tester.getRect(panel);
            // Inside the page gutter on every side.
            expect(rect.left, greaterThanOrEqualTo(AnumSpacing.md));
            expect(rect.right,
                lessThanOrEqualTo(entry.value.width - AnumSpacing.md));
            // The count pill never squeezes the title into a sliver.
            // (The test font draws every glyph one em wide, so even the
            // narrowest phone at 200 percent may break the title; a squeezed
            // title would stack a glyph per line.)
            final title = tester.getRect(find.descendant(
                of: panel, matching: find.text('Sources used')));
            expect(title.height, lessThanOrEqualTo(19 * scale * 1.15 * 4 + 1));
            // Content-driven height: at normal text on a tablet or desktop
            // the three sources fit well inside one screen.
            if (scale == 1 && entry.value.width >= 768) {
              expect(rect.height, lessThan(entry.value.height / 2));
            }
            for (final row in tester.widgetList<AnumRow>(
                find.descendant(of: panel, matching: find.byType(AnumRow)))) {
              final box = tester.getRect(find.byWidget(row));
              expect(box.right, lessThanOrEqualTo(rect.right + 0.5));
            }
            expect(tester.takeException(), isNull);
          });
        }
      }
    }
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
