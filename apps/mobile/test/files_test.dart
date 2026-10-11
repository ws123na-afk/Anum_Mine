// Workspace files on mobile (docs/files.md#mobile): parsing and checks, the
// API calls, the controller's paging and uploads, and the Files tab and
// detail screen at every supported width and at 200% text.
import 'dart:async';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/files_controller.dart';
import 'package:anum_mobile/features/workspace/files_models.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:anum_mobile/features/workspace/files_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _time = DateTime.utc(2026, 10, 6, 9);

WorkspaceFile _file(String id,
        {String name = 'notes.md',
        String kind = 'markdown',
        bool previewable = true,
        bool canManage = true,
        String createdBy = 'user_me',
        String status = 'indexed',
        String? error}) =>
    WorkspaceFile(
        id: id,
        name: name,
        contentType: kind == 'image' ? 'image/png' : 'text/markdown',
        sizeBytes: 2048,
        createdAt: _time,
        createdBy: createdBy,
        kind: kind,
        previewable: previewable,
        canManage: canManage,
        index: FileIndexState(status: status, error: error, chunkCount: 2));

const _usage = FileUsage(
    usedBytes: 900,
    fileCount: 3,
    quotaBytes: 1000,
    maxUploadBytes: 500,
    accepted: 'text (.txt, .md), images (.png) and PDF',
    acceptedExtensions: ['.md', '.pdf', '.png', '.txt'],
    canUpload: true);

final _png = Uint8List.fromList(const [
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49,
  0x48, 0x44, 0x52, //
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00,
  0x1F, 0x15, 0xC4,
  0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41, 0x54, 0x78, 0xDA, 0x63, 0xFC,
  0xCF, 0xC0, 0x50,
  0x0F, 0x00, 0x04, 0x85, 0x01, 0x80, 0x84, 0xA9, 0x8C, 0x21, 0x00, 0x00, 0x00,
  0x00, 0x49, 0x45,
  0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
]);

class FakeFiles implements FilesRepository {
  FakeFiles({List<WorkspaceFile>? files, this.usageValue = _usage})
      : files = files ?? [_file('file_1')];
  List<WorkspaceFile> files;
  FileUsage usageValue;
  final calls = <String>[];
  Object? listError;
  Completer<void>? holdUpload;

  @override
  Future<List<WorkspaceFile>> list(
      {String query = '',
      String sort = 'created_at:desc',
      String? cursor,
      int limit = filesPageSize}) async {
    calls.add('list q=$query sort=$sort cursor=$cursor');
    if (listError != null) throw listError!;
    var rows = files.where((f) => f.name.contains(query)).toList();
    if (cursor != null) {
      rows = rows.sublist(rows.indexWhere((f) => f.id == cursor) + 1);
    }
    return rows.take(limit).toList();
  }

  @override
  Future<FileUsage> usage() async => usageValue;

  @override
  Future<WorkspaceFile> upload(String path,
      {required String name,
      required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation}) async {
    calls.add('upload $name');
    onProgress(5, 10);
    final hold = holdUpload;
    if (hold != null) {
      await Future.any([hold.future, cancellation.whenCancelled]);
      if (cancellation.isCancelled) throw const UploadCancelledException();
    }
    final created = _file('file_new', name: name);
    files = [created, ...files];
    return created;
  }

  @override
  Future<WorkspaceFile> rename(String id, String name) async {
    calls.add('rename $id $name');
    final renamed = _file(id, name: name);
    files = [for (final f in files) f.id == id ? renamed : f];
    return renamed;
  }

  @override
  Future<WorkspaceFile> reindex(String id) async {
    calls.add('reindex $id');
    return _file(id, status: 'indexed');
  }

  @override
  Future<void> delete(String id) async {
    calls.add('delete $id');
    files = files.where((f) => f.id != id).toList();
  }

  @override
  Future<FilePreviewText> previewText(String id) async =>
      const FilePreviewText(text: '# Notes\nShip on Friday.', truncated: true);

  @override
  Future<Uint8List> bytes(String id) async => _png;

  @override
  Future<void> download(WorkspaceFile file) async => calls.add('download');
}

class _Sessions implements SessionStore {
  @override
  Future<void> clear() async {}
  @override
  Future<LocalSession?> read() async => LocalSession(
      accessToken: 'example',
      tokenType: 'bearer',
      context: const TenantContext(
          tenantId: 'tenant_a',
          workspaceId: 'workspace_a',
          userId: 'user_me',
          roles: ['member']),
      expiresAt: DateTime.now().add(const Duration(hours: 1)));
  @override
  Future<void> write(LocalSession session) async {}
}

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    if (request.method == 'GET' && request.uri.path == '/api/v1/files') {
      return const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'id': 'file_1',
            'name': 'notes.md',
            'content_type': 'text/markdown',
            'size_bytes': 10,
            'sha256': 'a',
            'created_by': 'user_me',
            'created_at': '2026-10-06T09:00:00Z',
            'kind': 'markdown',
            'previewable': true,
            'can_manage': true,
            'index': {'status': 'failed', 'error': 'model_unavailable'},
          }
        ]
      });
    }
    if (request.method == 'PATCH') {
      return const ApiResponse(statusCode: 403, body: {
        'error': {'code': 'forbidden', 'message': 'Only the uploader.'}
      });
    }
    return const ApiResponse(statusCode: 204);
  }
}

class _NoBytes implements FileStreamTransfer {
  @override
  Future<Uint8List> bytes(String fileId) async => Uint8List(0);
  @override
  Future<void> saveCopy(WorkspaceFile file) async {}
  @override
  Future<JsonMap> uploadStream(String path,
          {required String name,
          required void Function(int, int) onProgress,
          required UploadCancellation cancellation}) async =>
      const {};
}

Future<void> _pump(WidgetTester tester, Widget child,
    {Size size = const Size(390, 844), double textScale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: Scaffold(body: child),
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('helpers', () {
    test('the list path carries search, sort and the cursor', () {
      expect(
          filesListPath(), '/api/v1/files?limit=50&sort=created_at&order=desc');
      expect(
          filesListPath(query: ' q3 plan ', sort: 'name:asc', cursor: 'file_9'),
          '/api/v1/files?limit=50&sort=name&order=asc&q=q3+plan&cursor=file_9');
    });

    test('uploads the API would refuse are refused first, with a reason', () {
      expect(uploadProblem('a.md', 0, _usage), contains('is empty'));
      expect(uploadProblem('big.md', 600, _usage), contains('at most 500 B'));
      expect(uploadProblem('tool.exe', 10, _usage),
          contains('this workspace accepts text'));
      expect(uploadProblem('b.png', 200, _usage),
          contains('does not fit: 900 B of 1000 B is used'));
      expect(uploadProblem('ok.MD', 50, _usage), isNull);
      expect(uploadProblem('tool.exe', 10, null), isNull);
    });

    test('a rename keeps the extension', () {
      expect(renameProblem('a.txt', 'b.txt'), isNull);
      expect(renameProblem('a.txt', 'b.pdf'), contains('Keep the .txt'));
      expect(renameProblem('a.txt', 'x/y.txt'), contains('cannot contain'));
      expect(renameProblem('a.txt', ' '), 'Enter a name.');
    });

    test('index states read as sentences', () {
      expect(
          indexDescription(
                  const FileIndexState(status: 'skipped', error: 'not_text'))
              .$3,
          'Not indexed: only text files are indexed.');
      expect(
          indexDescription(const FileIndexState(
                  status: 'failed', error: 'model_unavailable'))
              .$3,
          'Indexing failed (model unavailable). Re-index to try again.');
      expect(canReindex(const FileIndexState(status: 'indexed')), isFalse);
      expect(canReindex(const FileIndexState(status: 'stale')), isTrue);
    });
  });

  test('the repository reads pages and passes the API refusal on', () async {
    final transport = _Transport();
    final api = AnumApiClient(
        baseUri: Uri.parse('https://api.example.test'),
        transport: transport,
        sessions: _Sessions());
    final repo = ApiFilesRepository(api, transfer: _NoBytes());
    final page = await repo.list(query: 'notes', sort: 'size:asc');
    expect(transport.requests.single.uri.toString(),
        'https://api.example.test/api/v1/files?limit=50&sort=size&order=asc&q=notes');
    expect(page.single.index?.status, 'failed');
    expect(page.single.canManage, isTrue);
    await expectLater(
        repo.rename('file_1', 'x.md'),
        throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'status', 403)));
    expect(transport.requests.last.body, {'name': 'x.md'});
  });

  group('controller', () {
    test('pages with the last id and stops at a short page', () async {
      final repo = FakeFiles(files: [
        for (var i = 0; i < 55; i++) _file('file_$i', name: 'n$i.md')
      ]);
      final c = FilesController(repo);
      await c.load();
      expect(c.items, hasLength(50));
      expect(c.hasMore, isTrue);
      await c.loadMore();
      expect(c.items, hasLength(55));
      expect(c.hasMore, isFalse);
      expect(
          repo.calls, contains('list q= sort=created_at:desc cursor=file_49'));
    });

    test('a 403 list is a permission state, not an error', () async {
      final repo = FakeFiles()..listError = const ApiException(403, 'no');
      final c = FilesController(repo);
      await c.load();
      expect(c.phase, FilesPhase.denied);
    });

    test('uploads report progress, can be cancelled, and refusals never send',
        () async {
      final repo = FakeFiles()..holdUpload = Completer<void>();
      var refreshed = 0;
      final c = FilesController(repo, onChanged: () async => refreshed++);
      await c.load();
      await c.upload(path: '/tmp/x.exe', name: 'x.exe', size: 10);
      expect(c.uploads.single.state, UploadState.failed);
      expect(repo.calls.where((x) => x.startsWith('upload')), isEmpty);
      c.dismissUpload(c.uploads.single);

      final pending = c.upload(path: '/tmp/a.md', name: 'a.md', size: 10);
      await Future<void>.delayed(Duration.zero);
      expect(c.uploads.single.fraction, .5);
      c.cancelUpload(c.uploads.single);
      await pending;
      expect(c.uploads.single.state, UploadState.cancelled);
      expect(c.uploads.single.error, 'Upload cancelled; nothing was stored.');

      repo.holdUpload = null;
      c.dismissUpload(c.uploads.single);
      final created = await c.upload(path: '/tmp/b.md', name: 'b.md', size: 10);
      expect(created?.name, 'b.md');
      expect(c.items.first.name, 'b.md');
      expect(c.notice, startsWith('Uploaded b.md.'));
      expect(refreshed, 1);
    });
  });

  group('screens', () {
    testWidgets('the tab lists files with storage, searches and uploads',
        (tester) async {
      final repo = FakeFiles(files: [
        _file('file_1'),
        _file('file_2',
            name: 'logo.png',
            kind: 'image',
            createdBy: 'user_other',
            canManage: false,
            status: 'skipped',
            error: 'not_text'),
      ]);
      final c = FilesController(repo);
      await _pump(
          tester,
          FilesTab(
              controller: c,
              currentUserId: 'user_me',
              pick: () async => [('/tmp/c.md', 'c.md', 10)]));
      expect(find.text('notes.md'), findsOneWidget);
      expect(find.textContaining('Markdown · 2.0 KB · You'), findsOneWidget);
      expect(find.textContaining('PNG image · 2.0 KB · user_other'),
          findsOneWidget);
      expect(find.text('900 B of 1000 B used · 3 files'), findsOneWidget);

      await tester.enterText(find.byType(TextField).first, 'logo');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(repo.calls.last, 'list q=logo sort=created_at:desc cursor=null');
      expect(find.text('notes.md'), findsNothing);

      await tester.tap(find.byTooltip('Clear search'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Upload').first);
      await tester.pumpAndSettle();
      expect(repo.calls, contains('upload c.md'));
      expect(find.textContaining('Uploaded c.md.'), findsOneWidget);
    });

    testWidgets('a viewer cannot upload and sees why', (tester) async {
      final repo = FakeFiles(
          usageValue: const FileUsage(
              usedBytes: 0,
              fileCount: 0,
              quotaBytes: 1000,
              maxUploadBytes: 500,
              accepted: 'text',
              acceptedExtensions: ['.md'],
              canUpload: false),
          files: []);
      await _pump(tester, FilesTab(controller: FilesController(repo)));
      final upload = tester.widget<FilledButton>(find.ancestor(
          of: find.text('Upload'),
          matching: find.byWidgetPredicate((w) => w is FilledButton)));
      expect(upload.onPressed, isNull);
      expect(find.textContaining('Uploading needs a member or owner role'),
          findsOneWidget);
      expect(find.text('Nobody has uploaded a file to this workspace yet.'),
          findsOneWidget);
    });

    testWidgets('a failed list says what failed and retries', (tester) async {
      final repo = FakeFiles()
        ..listError = const ApiException(500, 'Storage unavailable');
      await _pump(tester, FilesTab(controller: FilesController(repo)));
      expect(find.textContaining('Storage unavailable'), findsOneWidget);
      repo.listError = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('notes.md'), findsOneWidget);
    });

    testWidgets('the detail previews text, renames and deletes after confirm',
        (tester) async {
      final repo = FakeFiles();
      final c = FilesController(repo);
      await c.load();
      await _pump(
          tester,
          FileDetailScreen(
              controller: c, file: c.items.single, currentUserId: 'user_me'));
      expect(find.text('# Notes\nShip on Friday.'), findsOneWidget);
      expect(find.textContaining('Showing the first 256 KB'), findsOneWidget);
      expect(find.text('You'), findsOneWidget);

      await tester.tap(find.text('Rename'));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.widgetWithText(TextField, 'notes.md'), 'notes.pdf');
      await tester.pump();
      expect(find.textContaining('Keep the .md extension'), findsOneWidget);
      await tester.enterText(find.byType(TextField).last, 'final.md');
      await tester.pump();
      await tester.tap(find.text('Save name'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('rename file_1 final.md'));
      expect(find.text('Renamed to final.md.'), findsOneWidget);

      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      expect(find.text('Delete this file?'), findsOneWidget);
      await tester.tap(find.text('Keep'));
      await tester.pumpAndSettle();
      expect(repo.calls.where((x) => x.startsWith('delete')), isEmpty);
    });

    testWidgets('an image previews from its bytes; others say download',
        (tester) async {
      final repo = FakeFiles();
      final c = FilesController(repo);
      await _pump(
          tester,
          FileDetailScreen(
              controller: c,
              file: _file('file_2',
                  name: 'logo.png', kind: 'image', canManage: false)));
      expect(find.bySemanticsLabel('Preview of logo.png'), findsOneWidget);
      expect(find.textContaining('Only the person who uploaded this file'),
          findsOneWidget);
      expect(find.text('Rename'), findsNothing);
      await _pump(
          tester,
          FileDetailScreen(
              key: const ValueKey('file_3'),
              controller: c,
              file: _file('file_3',
                  name: 'deck.pptx', kind: 'document', previewable: false)));
      expect(find.textContaining('have no preview in the app'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in [1.0, 2.0]) {
        testWidgets('fits at ${width.toInt()} wide and ${scale}x text',
            (tester) async {
          final repo = FakeFiles(files: [
            _file('file_1',
                name:
                    'quarterly-launch-readiness-review-with-every-region-signed-off-final-v12.md'),
            _file('file_2', name: 'logo.png', kind: 'image', canManage: false),
          ])
            ..holdUpload = Completer<void>();
          final c = FilesController(repo);
          await _pump(tester, FilesTab(controller: c, currentUserId: 'user_me'),
              size: Size(width, 900), textScale: scale);
          unawaited(
              c.upload(path: '/tmp/slow.md', name: 'slow-upload.md', size: 10));
          await tester.pump();
          expect(tester.takeException(), isNull);
          c.cancelUpload(c.uploads.single);
          await tester.pumpAndSettle();
          await _pump(
              tester, FileDetailScreen(controller: c, file: c.items.first),
              size: Size(width, 900), textScale: scale);
          expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
