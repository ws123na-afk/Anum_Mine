// File versions and the storage screen on mobile (docs/files.md#versions):
// parsing and checks, the API calls, the controller, the versions section and
// the storage screen at every supported width, at 200% text and in Arabic.

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/files/file_strings.dart';
import 'package:anum_mobile/features/files/file_versions_controller.dart';
import 'package:anum_mobile/features/files/file_versions_models.dart';
import 'package:anum_mobile/features/files/file_versions_repository.dart';
import 'package:anum_mobile/features/files/file_versions_section.dart';
import 'package:anum_mobile/features/files/storage_screen.dart';
import 'package:anum_mobile/features/workspace/files_models.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _time = DateTime.utc(2026, 10, 6, 9);

WorkspaceFile _file({int version = 3, bool canManage = true}) => WorkspaceFile(
    id: 'file_plan',
    name: 'plan.txt',
    contentType: 'text/plain',
    sizeBytes: 300,
    createdAt: _time,
    createdBy: 'user_me',
    kind: 'text',
    previewable: true,
    canManage: canManage,
    version: version);

FileVersion _version(int n, {bool downloadable = true, String? signature}) =>
    FileVersion(
        id: 'ver_$n',
        fileId: 'file_plan',
        version: n,
        contentType: 'text/plain',
        sizeBytes: 100 + n,
        createdBy: n.isOdd ? 'user_me' : 'user_other',
        createdAt: _time,
        replacedBy: 'user_me',
        replacedAt: _time,
        downloadable: downloadable,
        scanSignature: signature);

const _usage = FileUsage(
    usedBytes: 900,
    fileCount: 2,
    quotaBytes: 1000,
    maxUploadBytes: 500,
    accepted: 'text (.txt) and images (.png)',
    acceptedExtensions: ['.png', '.txt'],
    canUpload: true,
    versionBytes: 200,
    versionCount: 2);

class FakeVersions implements FileVersionsRepository {
  FakeVersions({List<FileVersion>? older, WorkspaceFile? file})
      : older = older ?? [_version(2), _version(1)],
        file = file ?? _file();
  List<FileVersion> older;
  WorkspaceFile file;
  final calls = <String>[];
  Object? versionsError, uploadError, storageError;
  StorageReport? report;

  @override
  Future<FileVersionPage> versions(String fileId, {int offset = 0}) async {
    calls.add('versions offset=$offset');
    if (versionsError != null) throw versionsError!;
    return FileVersionPage(
        file: file,
        items: older.skip(offset).take(versionsPageSize).toList(),
        total: older.length);
  }

  @override
  Future<WorkspaceFile> uploadVersion(String fileId, String path,
      {required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation}) async {
    calls.add('upload $path');
    if (uploadError != null) throw uploadError!;
    onProgress(5, 10);
    older = [_version(file.version), ...older];
    file = _file(version: file.version + 1);
    return file;
  }

  @override
  Future<WorkspaceFile> restore(String fileId, String versionId) async {
    calls.add('restore $versionId');
    older = [_version(file.version), ...older];
    file = _file(version: file.version + 1);
    return file;
  }

  @override
  Future<void> delete(String fileId, String versionId) async {
    calls.add('delete $versionId');
    older = older.where((v) => v.id != versionId).toList();
  }

  @override
  Future<void> download(WorkspaceFile file, FileVersion version) async =>
      calls.add('download ${version.id}');

  @override
  Future<StorageReport> storage() async {
    calls.add('storage');
    if (storageError != null) throw storageError!;
    return report ?? _report();
  }
}

StorageReport _report({FileUsage usage = _usage}) =>
    StorageReport(usage: usage, byKind: const [
      StorageKindRow(kind: 'text', fileCount: 1, bytes: 300),
      StorageKindRow(kind: 'image', fileCount: 1, bytes: 400),
    ], largest: [
      StorageFileRow(
          file: _file(), versionCount: 2, versionBytes: 200, totalBytes: 500),
      StorageFileRow(
          file: WorkspaceFile(
              id: 'file_photo',
              name: 'photo.png',
              contentType: 'image/png',
              sizeBytes: 400,
              createdAt: _time),
          versionCount: 0,
          versionBytes: 0,
          totalBytes: 400),
    ]);

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
    final path = request.uri.path;
    final file = {
      'id': 'file_plan',
      'name': 'plan.txt',
      'content_type': 'text/plain',
      'size_bytes': 300,
      'created_at': '2026-10-06T09:00:00Z',
      'version': 3,
      'can_manage': true
    };
    if (path.endsWith('/versions') && request.method == 'GET') {
      return ApiResponse(statusCode: 200, body: {
        'file': file,
        'items': [
          {
            'id': 'ver_2',
            'file_id': 'file_plan',
            'version': 2,
            'content_type': 'text/plain',
            'size_bytes': 102,
            'created_by': 'user_me',
            'created_at': '2026-10-06T09:00:00Z',
            'replaced_by': 'user_me',
            'replaced_at': '2026-10-06T10:00:00Z',
            'downloadable': false,
            'scan_signature': 'Test.Sig'
          }
        ],
        'total': 11,
        'limit': 10,
        'offset': 0
      });
    }
    if (path.endsWith('/restore')) {
      return ApiResponse(statusCode: 200, body: {...file, 'version': 4});
    }
    if (path.endsWith('/storage')) {
      return ApiResponse(statusCode: 200, body: {
        'usage': {
          'used_bytes': 900,
          'file_count': 2,
          'quota_bytes': 1000,
          'max_upload_bytes': 500,
          'accepted': 'text',
          'accepted_extensions': ['.txt'],
          'can_upload': true,
          'version_bytes': 200,
          'version_count': 2
        },
        'by_kind': [
          {'kind': 'text', 'file_count': 1, 'bytes': 300}
        ],
        'largest': [
          {
            'file': file,
            'version_count': 2,
            'version_bytes': 200,
            'total_bytes': 500
          }
        ]
      });
    }
    if (request.method == 'DELETE') {
      return const ApiResponse(statusCode: 403, body: {
        'error': {'code': 'forbidden', 'message': 'Only the uploader.'}
      });
    }
    return const ApiResponse(statusCode: 204);
  }
}

class _NoTransfer implements FileVersionTransfer {
  final uploads = <String>[];
  @override
  Future<JsonMap> uploadVersion(String fileId, String path,
      {required void Function(int, int) onProgress,
      required UploadCancellation cancellation}) async {
    uploads.add('$fileId $path');
    return {
      'id': fileId,
      'name': 'plan.txt',
      'content_type': 'text/plain',
      'size_bytes': 12,
      'created_at': '2026-10-06T09:00:00Z',
      'version': 4
    };
  }

  @override
  Future<void> saveVersion(WorkspaceFile file, FileVersion version) async {}
}

Future<void> _pump(WidgetTester tester, Widget child,
    {Size size = const Size(390, 844),
    double textScale = 1,
    bool arabic = false}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: arabic ? const Locale('ar') : null,
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    supportedLocales: const [Locale('en'), Locale('ar')],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: Scaffold(body: SingleChildScrollView(child: child)),
  ));
  await tester.pumpAndSettle();
}

Future<List<(String, String, int)>> _pick() async =>
    [('/tmp/plan.txt', 'plan.txt', 12)];

FileVersionsController _controller(FakeVersions repo,
        {WorkspaceFile? file,
        FileUsage? Function()? usageOf,
        void Function(WorkspaceFile)? onFile,
        Future<void> Function()? onUsage}) =>
    FileVersionsController(repo, file ?? repo.file,
        usageOf: usageOf ?? () => _usage,
        onFileChanged: onFile,
        onUsageChanged: onUsage);

void main() {
  const s = FileStrings.english;

  group('models', () {
    test('a versions page parses the file, the versions and the total', () {
      final page = FileVersionPage.fromJson(JsonReader({
        'file': {
          'id': 'f',
          'name': 'a.txt',
          'content_type': 'text/plain',
          'size_bytes': 1,
          'created_at': '2026-10-06T09:00:00Z',
          'version': 4
        },
        'items': [
          {
            'id': 'v1',
            'file_id': 'f',
            'version': 1,
            'content_type': 'text/plain',
            'size_bytes': 5,
            'created_by': 'u',
            'created_at': '2026-10-06T09:00:00Z',
            'replaced_by': 'u2',
            'replaced_at': '2026-10-06T10:00:00Z',
            'downloadable': false,
            'scan_signature': 'Sig'
          }
        ],
        'total': 1
      }));
      expect(page.file.version, 4);
      expect(page.items.single.downloadable, isFalse);
      expect(page.items.single.scanSignature, 'Sig');
      expect(page.total, 1);
    });

    test('the versions path pages by offset and encodes the id', () {
      expect(versionsPath('file 1', 0),
          '/api/v1/files/file%201/versions?limit=10&offset=0');
      expect(versionsPath('f', -5, limit: 20),
          '/api/v1/files/f/versions?limit=20&offset=0');
    });

    test('merging keeps the first copy of a version', () {
      final merged = mergeVersions([_version(2), _version(1)], [_version(1)]);
      expect(merged.map((v) => v.id), ['ver_2', 'ver_1']);
    });

    test('a new version is checked before it is sent', () {
      final file = _file();
      expect(versionProblem('plan.txt', 50, file, _usage, s), isNull);
      expect(versionProblem('plan.txt', 50, file, null, s), isNull);
      expect(
          versionProblem('plan.txt', 0, file, _usage, s), contains('is empty'));
      expect(versionProblem('plan.txt', 600, file, _usage, s),
          contains('at most'));
      expect(versionProblem('plan.png', 5, file, _usage, s),
          contains('keeps the file type (.txt)'));
      expect(versionProblem('plan.txt', 200, file, _usage, s),
          contains('every version counts'));
      expect(bytesLeft(_usage), 100);
    });

    test('the quota meter turns amber, then red, then full', () {
      FileUsage at(int used) => FileUsage(
          usedBytes: used,
          fileCount: 1,
          quotaBytes: 1000,
          maxUploadBytes: 1,
          accepted: '',
          acceptedExtensions: const [],
          canUpload: true);
      expect(quotaLevel(at(700)), QuotaLevel.ok);
      expect(quotaLevel(at(800)), QuotaLevel.warn);
      expect(quotaLevel(at(950)), QuotaLevel.nearFull);
      expect(quotaLevel(at(1000)), QuotaLevel.full);
    });

    test('English and Arabic hold the same keys', () {
      expect(
          FileStrings.ar.keys.toSet(),
          FileStrings.en.keys.toSet().difference({
            'stor.versions.one',
            'stor.versions.other',
            'stor.versionsCell.one',
            'stor.versionsCell.other',
            'stor.files.one',
            'stor.files.other',
          }).union({
            for (final base in [
              'stor.versions',
              'stor.versionsCell',
              'stor.files'
            ])
              for (final form in ['one', 'two', 'few', 'many', 'other'])
                '$base.$form'
          }));
    });

    test('Arabic plural messages cover one, two, few, many and other', () {
      for (final base in ['stor.versions', 'stor.versionsCell', 'stor.files']) {
        for (final form in ['one', 'two', 'few', 'many', 'other']) {
          expect(FileStrings.ar.containsKey('$base.$form'), isTrue,
              reason: '$base.$form');
        }
      }
      const ar = FileStrings(true);
      expect(ar.count('stor.files', 2), 'ملفان');
      expect(ar.count('stor.files', 5), '5 ملفات');
      expect(s.count('stor.files', 1), '1 file');
      expect(s.count('stor.files', 3), '3 files');
    });
  });

  group('repository', () {
    test('reads pages, restores, uploads and passes the API refusal on',
        () async {
      final transport = _Transport();
      final api = AnumApiClient(
          baseUri: Uri.parse('https://api.example.test'),
          transport: transport,
          sessions: _Sessions());
      final transfer = _NoTransfer();
      final repo = ApiFileVersionsRepository(api, transfer: transfer);
      final page = await repo.versions('file_plan', offset: 10);
      expect(transport.requests.single.uri.toString(),
          'https://api.example.test/api/v1/files/file_plan/versions?limit=10&offset=10');
      expect(page.total, 11);
      expect(page.items.single.downloadable, isFalse);
      expect((await repo.restore('file_plan', 'ver_2')).version, 4);
      expect(transport.requests.last.uri.path,
          '/api/v1/files/file_plan/versions/ver_2/restore');
      expect(
          (await repo.uploadVersion('file_plan', '/tmp/plan.txt',
                  onProgress: (_, __) {}, cancellation: UploadCancellation()))
              .version,
          4);
      expect(transfer.uploads, ['file_plan /tmp/plan.txt']);
      expect((await repo.storage()).largest.single.totalBytes, 500);
      await expectLater(
          repo.delete('file_plan', 'ver_2'),
          throwsA(
              isA<ApiException>().having((e) => e.statusCode, 'status', 403)));
    });
  });

  group('controller', () {
    test('loads older versions and pages them', () async {
      final repo =
          FakeVersions(older: [for (var i = 12; i >= 1; i--) _version(i)]);
      final c = _controller(repo);
      await c.load(s);
      expect(c.phase, VersionsPhase.ready);
      expect(c.items, hasLength(10));
      await c.loadMore(s);
      expect(c.items, hasLength(12));
      expect(repo.calls, contains('versions offset=10'));
    });

    test('a new version updates the file, the usage and the list', () async {
      final repo = FakeVersions();
      var usageRefreshed = 0;
      WorkspaceFile? changed;
      final c = _controller(repo,
          onFile: (f) => changed = f, onUsage: () async => usageRefreshed++);
      await c.load(s);
      expect(await c.uploadVersion('/tmp/plan.txt', 'plan.txt', 12, s), isTrue);
      expect(c.file.version, 4);
      expect(c.items, hasLength(3));
      expect(changed?.version, 4);
      expect(usageRefreshed, 1);
      expect(c.note, 'Version 4 of plan.txt is now current.');
    });

    test('a refused new version says why and stores nothing', () async {
      final repo = FakeVersions()
        ..uploadError = const ApiException(413,
            'This workspace has used 9.8 KiB of its 9.8 KiB storage quota.');
      final c = _controller(repo);
      await c.load(s);
      expect(
          await c.uploadVersion('/tmp/plan.txt', 'plan.txt', 12, s), isFalse);
      expect(c.error, contains('The new version of plan.txt was not stored'));
      expect(c.error, contains('storage quota'));
      expect(c.file.version, 3);
      expect(await c.uploadVersion('/tmp/plan.png', 'plan.png', 5, s), isFalse);
      expect(c.error, contains('keeps the file type'));
      expect(
          repo.calls.where((call) => call.startsWith('upload')), hasLength(1));
    });

    test('the quota check reads the usage as it is now, not as it was',
        () async {
      var usage = _usage;
      final repo = FakeVersions();
      final c = _controller(repo, usageOf: () => usage);
      await c.load(s);
      expect(await c.uploadVersion('/tmp/plan.txt', 'plan.txt', 50, s), isTrue);
      // The Files tab refreshed its usage (other uploads filled the workspace).
      usage = const FileUsage(
          usedBytes: 990,
          fileCount: 3,
          quotaBytes: 1000,
          maxUploadBytes: 500,
          accepted: '',
          acceptedExtensions: [],
          canUpload: true);
      expect(
          await c.uploadVersion('/tmp/plan.txt', 'plan.txt', 50, s), isFalse);
      expect(c.error, contains('every version counts'));
    });

    test('restore, delete and download report and reload', () async {
      final repo = FakeVersions();
      final c = _controller(repo);
      await c.load(s);
      await c.restore(c.items.last, s);
      expect(c.note, contains('Version 1 is current again, as version 4'));
      await c.delete(c.items.first, s);
      expect(c.note, startsWith('Version'));
      await c.download(c.items.first, s);
      expect(repo.calls.last, startsWith('download'));
    });

    test('a failed load and a denied load are told apart', () async {
      final repo = FakeVersions()
        ..versionsError = const ApiException(500, 'Metadata store unavailable');
      final c = _controller(repo);
      await c.load(s);
      expect(c.phase, VersionsPhase.error);
      expect(c.loadError, contains('Metadata store unavailable'));
      repo.versionsError = const ApiException(403, 'No.', code: 'forbidden');
      await c.load(s);
      expect(c.phase, VersionsPhase.denied);
    });
  });

  group('versions section', () {
    testWidgets('lists, downloads, restores and deletes after confirm',
        (tester) async {
      final repo = FakeVersions();
      final c = _controller(repo);
      await _pump(
          tester,
          FileVersionsSection(
              controller: c, currentUserId: 'user_me', pick: _pick));
      expect(find.text('Current: version 3'), findsOneWidget);
      expect(find.text('Version 2'), findsOneWidget);
      expect(find.text('Version 1'), findsOneWidget);
      await tester.tap(find.byTooltip('Download version 1'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('download ver_1'));
      await tester.tap(find.bySemanticsLabel('Restore version 1'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('restore ver_1'));
      expect(find.text('Current: version 4'), findsOneWidget);
      await tester.tap(find.byTooltip('Delete version 2'));
      await tester.pumpAndSettle();
      expect(find.text('Delete version 2?'), findsOneWidget);
      await tester.tap(find.text('Keep'));
      await tester.pumpAndSettle();
      expect(repo.calls.where((call) => call.startsWith('delete')), isEmpty);
      await tester.tap(find.byTooltip('Delete version 2'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('delete ver_2'));
      expect(find.text('Version 2 deleted.'), findsOneWidget);
    });

    testWidgets('upload picks a file and shows the new version',
        (tester) async {
      final repo = FakeVersions();
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick));
      await tester.tap(find.text('Upload new version'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('upload /tmp/plan.txt'));
      expect(
          find.text('Version 4 of plan.txt is now current.'), findsOneWidget);
    });

    testWidgets('a viewer may download but not change', (tester) async {
      final repo = FakeVersions(file: _file(canManage: false));
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick));
      expect(find.text('Upload new version'), findsNothing);
      expect(find.text('Restore'), findsNothing);
      expect(find.byTooltip('Delete version 1'), findsNothing);
      expect(find.byTooltip('Download version 1'), findsOneWidget);
      expect(
          find.textContaining('Only the person who uploaded'), findsOneWidget);
    });

    testWidgets('a quarantined version cannot be downloaded or restored',
        (tester) async {
      final repo = FakeVersions(
          older: [_version(1, downloadable: false, signature: 'Test.Sig')]);
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick));
      expect(find.textContaining('found Test.Sig'), findsOneWidget);
      expect(find.byTooltip('Download version 1'), findsNothing);
      expect(find.text('Restore'), findsNothing);
      expect(find.byTooltip('Delete version 1'), findsOneWidget);
    });

    testWidgets('empty, failed and denied states each say so', (tester) async {
      final repo = FakeVersions(older: [])
        ..versionsError = const ApiException(500, 'Metadata store unavailable');
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick));
      expect(
          find.textContaining('Could not load the versions'), findsOneWidget);
      repo.versionsError = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('No earlier versions'), findsOneWidget);
      expect(
          find.textContaining('becomes version 3 in the list'), findsOneWidget);

      final denied = FakeVersions()
        ..versionsError = const ApiException(403, 'No.', code: 'forbidden');
      await _pump(tester,
          FileVersionsSection(controller: _controller(denied), pick: _pick));
      expect(find.textContaining('Seeing versions needs a viewer'),
          findsOneWidget);
    });

    testWidgets('long lists page with Show more', (tester) async {
      final repo =
          FakeVersions(older: [for (var i = 12; i >= 1; i--) _version(i)]);
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick));
      expect(find.text('Show more versions (10 of 12)'), findsOneWidget);
      await tester.ensureVisible(find.text('Show more versions (10 of 12)'));
      await tester.tap(find.text('Show more versions (10 of 12)'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Show more versions'), findsNothing);
      expect(find.text('Version 1'), findsOneWidget);
    });

    for (final size in const [
      Size(320, 640),
      Size(390, 844),
      Size(768, 1024),
      Size(1024, 768),
      Size(1440, 900)
    ]) {
      for (final scale in const [1.0, 2.0]) {
        testWidgets(
            'fits at ${size.width.toInt()}x${size.height.toInt()} '
            'and ${scale}x text', (tester) async {
          final repo =
              FakeVersions(older: [for (var i = 4; i >= 1; i--) _version(i)]);
          await _pump(
              tester,
              FileVersionsSection(
                  controller: _controller(repo),
                  currentUserId: 'user_me',
                  pick: _pick),
              size: size,
              textScale: scale);
          expect(tester.takeException(), isNull);
          expect(find.text('Version 4'), findsOneWidget);
        });
      }
    }

    testWidgets('reads in Arabic', (tester) async {
      final repo = FakeVersions();
      await _pump(tester,
          FileVersionsSection(controller: _controller(repo), pick: _pick),
          arabic: true);
      expect(find.text('الإصدارات'), findsOneWidget);
      expect(find.text('الحالي: الإصدار 3'), findsOneWidget);
      expect(find.text('ارفع إصدارًا جديدًا'), findsOneWidget);
    });
  });

  group('storage screen', () {
    Future<StorageController> open(WidgetTester tester, FakeVersions repo,
        {Size size = const Size(390, 844),
        double textScale = 1,
        bool arabic = false,
        void Function(String)? onOpen}) async {
      final c = StorageController(repo);
      await _pump(
          tester,
          SizedBox(
              height: size.height,
              child:
                  StorageScreen(controller: c, onOpenFile: onOpen ?? (_) {})),
          size: size,
          textScale: textScale,
          arabic: arabic);
      return c;
    }

    testWidgets('shows usage, versions, kinds and the largest files',
        (tester) async {
      String? opened;
      await open(tester, FakeVersions(), onOpen: (id) => opened = id);
      expect(find.text('900 B of 1000 B used'), findsOneWidget);
      expect(find.text('100 B left'), findsOneWidget);
      expect(find.textContaining('200 B of it is 2 older versions'),
          findsOneWidget);
      expect(find.text('Text'), findsOneWidget);
      expect(find.text('Images'), findsOneWidget);
      expect(find.text('plan.txt'), findsOneWidget);
      expect(find.text('2 older versions (200 B)'), findsOneWidget);
      expect(find.text('No older versions'), findsOneWidget);
      await tester.tap(find.text('plan.txt'));
      expect(opened, 'file_plan');
    });

    testWidgets('warns when nearly full and when full', (tester) async {
      final repo = FakeVersions()
        ..report = _report(
            usage: const FileUsage(
                usedBytes: 960,
                fileCount: 2,
                quotaBytes: 1000,
                maxUploadBytes: 500,
                accepted: '',
                acceptedExtensions: [],
                canUpload: true));
      await open(tester, repo);
      expect(find.textContaining('almost full'), findsOneWidget);
      repo.report = _report(
          usage: const FileUsage(
              usedBytes: 1000,
              fileCount: 2,
              quotaBytes: 1000,
              maxUploadBytes: 500,
              accepted: '',
              acceptedExtensions: [],
              canUpload: true));
      final c = await open(tester, repo);
      await c.load();
      await tester.pumpAndSettle();
      expect(find.textContaining('Storage is full'), findsOneWidget);
    });

    testWidgets('empty, failed and denied states each say so', (tester) async {
      final repo = FakeVersions()
        ..storageError = const ApiException(500, 'Metadata store unavailable');
      final c = await open(tester, repo);
      expect(
          find.textContaining('Storage could not be loaded'), findsOneWidget);
      repo
        ..storageError = null
        ..report = _report(
            usage: const FileUsage(
                usedBytes: 0,
                fileCount: 0,
                quotaBytes: 1000,
                maxUploadBytes: 500,
                accepted: '',
                acceptedExtensions: [],
                canUpload: true));
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Nothing is stored yet'), findsOneWidget);
      repo.storageError = const ApiException(403, 'No.', code: 'forbidden');
      await c.load();
      await tester.pumpAndSettle();
      expect(
          find.textContaining('Seeing storage needs a viewer'), findsOneWidget);
    });

    for (final size in const [
      Size(320, 640),
      Size(390, 844),
      Size(768, 1024),
      Size(1440, 900)
    ]) {
      for (final scale in const [1.0, 2.0]) {
        testWidgets(
            'fits at ${size.width.toInt()}x${size.height.toInt()} '
            'and ${scale}x text', (tester) async {
          await open(tester, FakeVersions(), size: size, textScale: scale);
          expect(tester.takeException(), isNull);
          expect(find.text('900 B of 1000 B used'), findsOneWidget);
        });
      }
    }

    testWidgets('reads in Arabic', (tester) async {
      await open(tester, FakeVersions(), arabic: true);
      expect(find.text('أكبر الملفات'), findsOneWidget);
      expect(find.text('حسب النوع'), findsOneWidget);
      expect(find.text('صور'), findsOneWidget);
    });
  });
}
