// Workspace memories on the phone (docs/memory.md#workspace-memories): the
// API shapes (a note with no task), a workspace memory with its retention,
// editing by the author or an owner, older pages with "Load older memories",
// refusals, and layouts from 320 to 1024 wide at 2x text in English and
// Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/resources_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'lists/lists_harness.dart';

final _at = DateTime.utc(2026, 11, 1, 9);

WorkspaceMemory _memory(int n,
        {String? taskId,
        String createdBy = 'user_me',
        String? owner,
        DateTime? expires}) =>
    WorkspaceMemory(
        id: 'memory_$n',
        taskId: taskId,
        content: 'Fact number $n',
        sourceType: 'mobile',
        createdAt: _at.subtract(Duration(minutes: n)),
        createdBy: createdBy,
        ownerUserId: owner,
        expiresAt: expires);

class _Fake implements WorkspaceRepository {
  _Fake(this.all);

  /// Newest first.
  List<WorkspaceMemory> all;
  int firstPage = memoryFirstPage;
  final created = <(String, int?)>[];
  final edits = <(String, String)>[];
  final pages = <(int, int)>[];
  Object? refuseEdit;
  Object? refuseCreate;

  List<WorkspaceMemory> _page(int offset, int limit) =>
      all.skip(offset).take(limit).toList().reversed.toList();

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
          tasks: [
            WorkspaceTask(
                id: 'task_1',
                title: 'Quarterly report',
                prompt: 'Write it',
                status: WorkStatus.completed,
                createdAt: _at,
                updatedAt: _at)
          ],
          approvals: const [],
          automations: const [],
          files: const [],
          memories: _page(0, firstPage));

  @override
  Future<List<WorkspaceMemory>> memoryPage(
      {required int offset, required int limit}) async {
    pages.add((offset, limit));
    return _page(offset, limit);
  }

  @override
  Future<WorkspaceMemory> createWorkspaceMemory(String content,
      {int? keepDays}) async {
    if (refuseCreate != null) throw refuseCreate!;
    created.add((content, keepDays));
    final m = WorkspaceMemory(
        id: 'memory_new',
        taskId: null,
        content: content,
        sourceType: 'mobile',
        createdAt: DateTime.now().toUtc(),
        createdBy: 'user_me');
    all = [m, ...all];
    return m;
  }

  @override
  Future<WorkspaceMemory> updateMemory(String memoryId, String content) async {
    if (refuseEdit != null) throw refuseEdit!;
    edits.add((memoryId, content));
    return all
        .firstWhere((m) => m.id == memoryId)
        .edited(content, by: 'user_me', at: _at);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<WorkspaceController> _controller(_Fake fake,
    {List<String> roles = const ['member']}) async {
  final c = WorkspaceController(fake)
    ..currentUserId = 'user_me'
    ..currentRoles = roles;
  await c.load();
  return c;
}

Future<void> _pump(WidgetTester tester, WorkspaceController c,
    {double width = 390,
    double height = 2400,
    double text = 1,
    String locale = 'en'}) async {
  await tester.pumpWidget(const SizedBox());
  await pumpList(
      tester, ResourcesScreen(controller: c, initialTab: ResourcesTab.memory),
      width: width, height: height, text: text, locale: locale);
}

void main() {
  group('API', () {
    test(
        'a note with no task parses; create, edit and pages send the API shape',
        () async {
      final transport = ScriptedTransport((r) {
        if (r.method == 'GET') {
          return const ApiResponse(statusCode: 200, body: {
            'data': [
              {
                'id': 'memory_ws',
                'tenant_id': 't',
                'workspace_id': 'w',
                'task_id': null,
                'content': 'Invoices go out on the 25th.',
                'provenance': {
                  'source_type': 'web',
                  'created_by_user_id': 'user_a',
                  'created_at': '2026-11-01T09:00:00Z',
                  'metadata': {'last_edited_by': 'user_b'}
                },
                'retention': {
                  'kind': 'expires_at',
                  'expires_at': '2026-12-01T09:00:00Z'
                },
                'created_at': '2026-11-01T09:00:00Z',
                'updated_at': '2026-11-02T09:00:00Z',
                'owner_user_id': null,
              }
            ]
          });
        }
        return ApiResponse(statusCode: 200, body: {
          'id': 'memory_ws',
          'task_id': null,
          'content': r.body!['content'],
          'provenance': {
            'source_type': 'mobile',
            'created_by_user_id': 'user_me',
            'created_at': '2026-11-01T09:00:00Z'
          },
          'retention': r.body!['retention'] ?? {'kind': 'indefinite'},
          'created_at': '2026-11-01T09:00:00Z',
        });
      });
      final repo = ApiWorkspaceRepository(await scriptedClient(transport),
          fileTransfer: _NoFiles());
      final page = await repo.memoryPage(offset: 200, limit: 51);
      expect(transport.requests.last.uri.toString(),
          'http://localhost:8000/api/v1/memories?limit=51&offset=200');
      final note = page.single;
      expect(note.taskId, isNull);
      expect(note.isWorkspaceNote, isTrue);
      expect(note.createdBy, 'user_a');
      expect(note.editedBy, 'user_b');
      expect(note.expiresAt, DateTime.utc(2026, 12, 1, 9));
      await repo.createWorkspaceMemory('Keep it short',
          keepDays: 30, now: DateTime.utc(2026, 11, 11));
      expect(transport.requests.last.body, {
        'content': 'Keep it short',
        'source_type': 'mobile',
        'retention': {
          'kind': 'expires_at',
          'expires_at': '2026-12-11T00:00:00.000Z'
        },
      });
      await repo.createWorkspaceMemory('Forever');
      expect(
          transport.requests.last.body!['retention'], {'kind': 'indefinite'});
      expect(transport.requests.last.body!.containsKey('task_id'), isFalse);
      await repo.updateMemory('memory_ws', 'New text');
      expect(transport.requests.last.method, 'PATCH');
      expect(transport.requests.last.uri.path, '/api/v1/memories/memory_ws');
      expect(transport.requests.last.body, {'content': 'New text'});
    });

    test('who may edit: the author, or an owner on a shared note', () {
      expect(
          canEditMemory(_memory(1), userId: 'user_me', owner: false), isTrue);
      expect(
          canEditMemory(_memory(1, createdBy: 'user_b'),
              userId: 'user_me', owner: false),
          isFalse);
      expect(
          canEditMemory(_memory(1, createdBy: 'user_b'),
              userId: 'user_me', owner: true),
          isTrue);
      expect(
          canEditMemory(_memory(1, createdBy: 'user_b', owner: 'user_b'),
              userId: 'user_me', owner: true),
          isFalse);
    });
  });

  group('controller', () {
    test('older pages: one extra asks whether another page follows', () async {
      final fake = _Fake([for (var i = 0; i < 260; i++) _memory(i)])
        ..firstPage = memoryFirstPage;
      final c = await _controller(fake);
      expect(c.memories, hasLength(200));
      expect(c.hasOlderMemories, isTrue);
      await c.loadOlderMemories();
      expect(fake.pages.last, (200, 51));
      expect(c.memories, hasLength(250));
      expect(c.hasOlderMemories, isTrue);
      await c.loadOlderMemories();
      expect(fake.pages.last, (250, 51));
      expect(c.memories, hasLength(260));
      expect(c.hasOlderMemories, isFalse);
      // Every memory exactly once.
      expect(c.memories.map((m) => m.id).toSet(), hasLength(260));
    });

    test('a workspace memory reloads; a refusal keeps the sentence', () async {
      final fake = _Fake([_memory(1)]);
      final c = await _controller(fake);
      expect(await c.addWorkspaceMemory('Office closes at 6', keepDays: 7),
          isNull);
      expect(fake.created.single, ('Office closes at 6', 7));
      expect(c.memories.map((m) => m.content), contains('Office closes at 6'));
      fake.refuseCreate = const ApiException(
          422, 'Memory governance does not allow the source type mobile.');
      expect(await c.addWorkspaceMemory('x'), contains('Memory governance'));
      final first = c.memories.firstWhere((m) => m.id == 'memory_1');
      expect(await c.editMemory(first, 'Changed'), isNull);
      expect(
          c.memories.firstWhere((m) => m.id == 'memory_1').content, 'Changed');
      fake.refuseEdit = const ApiException(403, 'Not yours');
      expect(await c.editMemory(first, 'Again'), 'denied');
    });
  });

  group('Memory tab', () {
    testWidgets('adds a workspace memory with a retention, edits and pages',
        (tester) async {
      final fake = _Fake([
        _memory(1, taskId: 'task_1'),
        _memory(2, createdBy: 'user_b'),
        _memory(3, owner: 'user_me'),
      ])
        ..firstPage = 2;
      final c = await _controller(fake);
      await _pump(tester, c);
      expect(find.text('Whole workspace (no task)'), findsOneWidget);
      expect(find.byKey(const Key('memory-keep')), findsOneWidget);
      // A member edits their own task note, not someone else's shared note.
      expect(find.byKey(const Key('memory-edit-memory_1')), findsOneWidget);
      expect(find.byKey(const Key('memory-edit-memory_2')), findsNothing);
      expect(find.text('From: Quarterly report'), findsOneWidget);
      expect(find.text('Workspace note'), findsOneWidget);

      await tester.tap(find.byKey(const Key('memory-keep')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('For 30 days').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('memory-content')),
          'Invoices go out on the 25th');
      await tester.pump();
      await tester.tap(find.byKey(const Key('memory-save')));
      await tester.pumpAndSettle();
      expect(fake.created.single, ('Invoices go out on the 25th', 30));
      expect(find.text('Memory saved.'), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('memory-edit-memory_1')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('memory-edit-memory_1')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('memory-edit-text')), ' ');
      await tester.tap(find.byKey(const Key('memory-edit-save')));
      await tester.pumpAndSettle();
      expect(find.text('A memory needs some text.'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('memory-edit-text')), 'Fact one, corrected');
      await tester.tap(find.byKey(const Key('memory-edit-save')));
      await tester.pumpAndSettle();
      expect(fake.edits.single, ('memory_1', 'Fact one, corrected'));
      expect(find.text('Fact one, corrected'), findsOneWidget);

      expect(find.text('Memories shown: 2'), findsOneWidget);
    });

    testWidgets('an owner edits shared notes; a refused edit says who can',
        (tester) async {
      final fake = _Fake([_memory(2, createdBy: 'user_b')])
        ..refuseEdit = const ApiException(403, 'No');
      final c = await _controller(fake, roles: const ['owner']);
      await _pump(tester, c);
      await tester.tap(find.byKey(const Key('memory-edit-memory_2')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('memory-edit-text')), 'x y');
      await tester.tap(find.byKey(const Key('memory-edit-save')));
      await tester.pumpAndSettle();
      expect(
          find.text(
              'Only the person who added this memory or a workspace owner can change it.'),
          findsOneWidget);
    });

    testWidgets('Load older memories appears for a full first page',
        (tester) async {
      final fake = _Fake([for (var i = 0; i < 205; i++) _memory(i)]);
      final c = await _controller(fake);
      await _pump(tester, c, height: 900);
      await tester.scrollUntilVisible(
          find.byKey(const Key('memory-older')), 2000,
          scrollable: find
              .descendant(
                  of: find.byType(ListView), matching: find.byType(Scrollable))
              .first);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('memory-older')));
      await tester.pumpAndSettle();
      expect(c.memories, hasLength(205));
      expect(find.byKey(const Key('memory-older')), findsNothing);
      expect(fake.pages.single, (200, 51));
    });

    testWidgets('an empty workspace says how to add the first one',
        (tester) async {
      final c = await _controller(_Fake([]));
      await _pump(tester, c);
      expect(find.text('No memories yet'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0]) {
      testWidgets('lays out at $width wide, 2x text, English and Arabic',
          (tester) async {
        final fake = _Fake([
          _memory(1, taskId: 'task_1', expires: _at),
          _memory(2),
          _memory(3, owner: 'user_me'),
        ]);
        for (final locale in ['en', 'ar']) {
          final c = await _controller(fake, roles: const ['owner']);
          await _pump(tester, c, width: width, text: 2, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale == 'ar') {
            expect(find.text('مساحة العمل كلها (بلا مهمة)'), findsOneWidget);
          }
        }
      });
    }
  });
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
