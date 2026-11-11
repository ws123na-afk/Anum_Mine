// Folders, tags and the selection on the Files tab (docs/files.md#folders,
// #tags, #bulk-actions, #mobile): the list path, the tree helpers, the
// controller's folder and tag state and bulk calls, and the screen with the
// folder trail, tag chips, selection bar and the move and delete flows, at
// phone and tablet widths and in Arabic.
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/workspace/files_controller.dart';
import 'package:anum_mobile/features/workspace/files_models.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:anum_mobile/features/workspace/files_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _time = DateTime.utc(2026, 11, 10, 9);
const _usage = FileUsage(
    usedBytes: 900,
    fileCount: 3,
    quotaBytes: 10000,
    maxUploadBytes: 4096,
    accepted: 'text',
    acceptedExtensions: ['.txt'],
    canUpload: true);

WorkspaceFile _file(String id, String name,
        {String? folderId,
        String? folderPath,
        List<FileTag> tags = const [],
        bool canManage = true}) =>
    WorkspaceFile(
        id: id,
        name: name,
        contentType: 'text/plain',
        sizeBytes: 40,
        createdAt: _time,
        createdBy: 'user_me',
        kind: 'text',
        previewable: true,
        canManage: canManage,
        folderId: folderId,
        folderPath: folderPath,
        tags: tags);

const _urgent = FileTag(id: 'tag_urgent', name: 'Urgent', fileCount: 1);
const _contracts = FileFolder(
    id: 'folder_contracts', name: 'Contracts', path: 'Contracts', fileCount: 1);
const _year = FileFolder(
    id: 'folder_2026',
    name: '2026',
    path: 'Contracts / 2026',
    parentId: 'folder_contracts',
    depth: 2);

class _Repo implements FilesRepository {
  _Repo();
  List<WorkspaceFile> files = [
    _file('file_a', 'readme.txt', tags: const [_urgent]),
    _file('file_b', 'notes.txt'),
    _file('file_c', 'theirs.txt', canManage: false),
    _file('file_d', 'lease.txt',
        folderId: 'folder_contracts', folderPath: 'Contracts'),
  ];
  List<FileFolder> folderRows = [_contracts, _year];
  List<FileTag> tagRows = [_urgent];
  final calls = <String>[];
  Object? bulkError;

  @override
  Future<List<WorkspaceFile>> list(
      {String query = '',
      String sort = 'created_at:desc',
      String? cursor,
      int limit = filesPageSize,
      String? folder = everyFolder,
      String? tag}) async {
    calls.add('list q=$query folder=$folder tag=$tag');
    var rows = files.where((f) => f.name.contains(query)).toList();
    if (folder != everyFolder) {
      rows = rows.where((f) => f.folderId == folder).toList();
    }
    if (tag != null) {
      rows = rows.where((f) => f.tags.any((t) => t.id == tag)).toList();
    }
    return rows;
  }

  @override
  Future<FileUsage> usage() async => _usage;
  @override
  Future<List<FileFolder>> folders() async => [
        for (final f in folderRows)
          FileFolder(
              id: f.id,
              name: f.name,
              path: f.path,
              parentId: f.parentId,
              depth: f.depth,
              fileCount: files.where((x) => x.folderId == f.id).length,
              canManage: f.canManage)
      ];
  @override
  Future<List<FileTag>> tags() async => tagRows;
  @override
  Future<FileFolder> createFolder(String name, String? parentId) async {
    calls.add('createFolder $name in $parentId');
    final created = FileFolder(
        id: 'folder_new', name: name, path: name, parentId: parentId);
    folderRows = [...folderRows, created];
    return created;
  }

  @override
  Future<int> bulkMove(List<String> fileIds, String? folderId) async {
    calls.add('bulkMove ${fileIds.join(',')} to $folderId');
    if (bulkError != null) throw bulkError!;
    files = [
      for (final f in files)
        fileIds.contains(f.id)
            ? _file(f.id, f.name, folderId: folderId, canManage: f.canManage)
            : f
    ];
    return fileIds.length;
  }

  @override
  Future<int> bulkDelete(List<String> fileIds) async {
    calls.add('bulkDelete ${fileIds.join(',')}');
    if (bulkError != null) throw bulkError!;
    files = files.where((f) => !fileIds.contains(f.id)).toList();
    return fileIds.length;
  }

  @override
  Future<WorkspaceFile> tagFile(String id, List<String> tagIds) async {
    calls.add('tagFile $id ${tagIds.join(',')}');
    final found = files.firstWhere((f) => f.id == id);
    final tagged = _file(id, found.name, tags: [
      ...found.tags,
      for (final tagId in tagIds)
        FileTag(id: tagId, name: tagId == 'tag_new' ? 'Legal' : 'Urgent')
    ]);
    files = [for (final f in files) f.id == id ? tagged : f];
    return tagged;
  }

  @override
  Future<WorkspaceFile> untagFile(String id, String tagId) async {
    calls.add('untagFile $id $tagId');
    final found = files.firstWhere((f) => f.id == id);
    final untagged = _file(id, found.name,
        tags: found.tags.where((t) => t.id != tagId).toList());
    files = [for (final f in files) f.id == id ? untagged : f];
    return untagged;
  }

  @override
  Future<FileTag> createTag(String name) async {
    calls.add('createTag $name');
    return FileTag(id: 'tag_new', name: name, canManage: true);
  }

  @override
  Future<WorkspaceFile> upload(String path,
          {required String name,
          required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation,
          String? folderId}) async =>
      throw UnimplementedError();
  @override
  Future<WorkspaceFile> rename(String id, String name) async =>
      throw UnimplementedError();
  @override
  Future<WorkspaceFile> reindex(String id) async => throw UnimplementedError();
  @override
  Future<void> delete(String id) async {}
  @override
  Future<FilePreviewText> previewText(String id) async =>
      const FilePreviewText(text: 'line one', truncated: false);
  @override
  Future<Uint8List> bytes(String id) async => Uint8List(0);
  @override
  Future<void> download(WorkspaceFile file) async {}
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
    home: Scaffold(body: child),
  ));
  await tester.pumpAndSettle();
}

/// Scrolls the tab's list so [finder] is on screen (the rows are built at
/// once, so the finder already matches; only the scroll offset is missing).
Future<void> _show(WidgetTester tester, Finder finder,
    {double delta = 120}) async {
  await tester.ensureVisible(finder.first);
  await tester.pumpAndSettle();
}

Future<void> _tapTile(WidgetTester tester, int index) async {
  final finder = find.byType(CheckboxListTile).at(index);
  await _show(tester, finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> _tapText(WidgetTester tester, String text) async {
  await _show(tester, find.text(text));
  await tester.tap(find.text(text));
  await tester.pumpAndSettle();
}

void main() {
  group('helpers', () {
    test('the list path names the folder scope and the tag', () {
      expect(
          filesListPath(), '/api/v1/files?limit=50&sort=created_at&order=desc');
      expect(filesListPath(folder: null),
          '/api/v1/files?limit=50&sort=created_at&order=desc&folder=root');
      expect(filesListPath(folder: 'folder_1', tag: 'tag_1'),
          '/api/v1/files?limit=50&sort=created_at&order=desc&folder=folder_1&tag=tag_1');
    });

    test('the tree helpers walk parents, children and a depth-first list', () {
      final tree = [_contracts, _year];
      expect(folderChildren(tree, null).map((f) => f.id), ['folder_contracts']);
      expect(folderChildren(tree, 'folder_contracts').map((f) => f.id),
          ['folder_2026']);
      expect(folderTrail(tree, 'folder_2026').map((f) => f.name),
          ['Contracts', '2026']);
      expect(folderTrail(tree, null), isEmpty);
      expect(folderTrail(tree, 'missing'), isEmpty);
      expect(folderChoices(tree).map((f) => f.id),
          ['folder_contracts', 'folder_2026']);
    });

    test('folder names follow the file-name rules and are unique', () {
      expect(folderNameProblem('Invoices', [_contracts]), isNull);
      expect(folderNameProblem('  ', []), 'Enter a folder name.');
      expect(folderNameProblem('a/b', []),
          'Folder names cannot contain / or \\ or control characters.');
      expect(folderNameProblem('contracts', [_contracts]),
          'A folder named contracts already exists here.');
    });
  });

  group('controller', () {
    test('opens folders, filters by tag and keeps a search in every folder',
        () async {
      final repo = _Repo();
      final c = FilesController(repo);
      await c.load();
      expect(c.items.map((f) => f.name),
          ['readme.txt', 'notes.txt', 'theirs.txt']);
      expect(repo.calls.first, 'list q= folder=null tag=null');
      expect(c.subfolders.map((f) => f.name), ['Contracts']);
      await c.openFolder('folder_contracts');
      expect(c.items.map((f) => f.name), ['lease.txt']);
      expect(c.trail.map((f) => f.name), ['Contracts']);
      expect(c.subfolders.map((f) => f.name), ['2026']);
      await c.search('txt');
      expect(repo.calls.last, 'list q=txt folder=all tag=null');
      expect(c.items, hasLength(4));
      await c.openFolder(null);
      await c.setTagFilter('tag_urgent');
      expect(c.items.map((f) => f.name), ['readme.txt']);
      expect(repo.calls.last, 'list q= folder=null tag=tag_urgent');
    });

    test('a deleted folder sends the list back to the root with a note',
        () async {
      final repo = _Repo();
      final c = FilesController(repo);
      await c.load();
      c.folder = 'folder_gone';
      repo.files = [];
      final failing = _FailingOnce(repo);
      final again = FilesController(failing);
      again.folder = 'folder_gone';
      await again.load();
      expect(again.folder, isNull);
      expect(
          again.notice, 'This folder no longer exists. Showing all documents.');
      expect(again.phase, FilesPhase.ready);
    });

    test('the selection moves and deletes all or nothing and clears itself',
        () async {
      final repo = _Repo();
      final c = FilesController(repo);
      await c.load();
      c.toggleSelecting();
      c.toggleSelected(c.items[0]);
      c.toggleSelected(c.items[2]);
      expect(c.selected, {'file_a', 'file_c'});
      expect(c.selectionManageable, isFalse, reason: 'theirs.txt is not mine');
      c.toggleSelected(c.items[2]);
      c.toggleSelected(c.items[1]);
      expect(c.selectionManageable, isTrue);
      expect(await c.moveSelected('folder_contracts'), isNull);
      expect(
          repo.calls, contains('bulkMove file_a,file_b to folder_contracts'));
      expect(c.notice, 'Moved 2 files to Contracts.');
      expect(c.selected, isEmpty);
      expect(c.items.map((f) => f.name), ['theirs.txt']);

      await c.openFolder('folder_contracts');
      c.selectAll();
      repo.bulkError = const ApiException(403,
          '1 of the 3 selected files were uploaded by someone else. Nothing was changed.');
      expect(await c.deleteSelected(),
          'Nothing was deleted: 1 of the 3 selected files were uploaded by someone else. Nothing was changed.');
      expect(c.selected, hasLength(3));
      repo.bulkError = null;
      expect(await c.deleteSelected(), isNull);
      expect(c.notice, 'Deleted 3 files.');
      expect(c.items, isEmpty);
      c.toggleSelecting();
      expect(c.selecting, isFalse);
    });

    test('tags are added, created and removed on one file', () async {
      final repo = _Repo();
      final c = FilesController(repo);
      await c.load();
      final notes = c.items[1];
      expect(await c.addTag(notes, tagId: 'tag_urgent'), isNull);
      expect(c.items[1].tags.map((t) => t.name), ['Urgent']);
      expect(await c.addTag(notes, newName: 'Legal'), isNull);
      expect(repo.calls,
          containsAll(['createTag Legal', 'tagFile file_b tag_new']));
      expect(await c.addTag(notes, newName: '  '), 'Enter a tag name.');
      expect(await c.removeTag(c.items[1], _urgent), isNull);
      expect(repo.calls.last, 'untagFile file_b tag_urgent');
      expect(await c.createFolder('contracts'),
          'A folder named contracts already exists here.');
      expect(await c.createFolder('Invoices'), isNull);
      expect(c.subfolders.map((f) => f.name), ['Contracts', 'Invoices']);
    });
  });

  group('screen', () {
    testWidgets('the trail, subfolders, tags and the selection bar work',
        (tester) async {
      final repo = _Repo();
      final c = FilesController(repo);
      await _pump(tester, FilesTab(controller: c, currentUserId: 'user_me'));
      expect(find.text('All documents'), findsOneWidget);
      expect(find.text('Contracts · 1 file'), findsOneWidget);
      expect(find.text('Urgent · 1'), findsOneWidget);

      await tester.tap(find.text('Contracts · 1 file'));
      await tester.pumpAndSettle();
      expect(find.text('lease.txt'), findsOneWidget);
      expect(find.text('2026 · 0 files'), findsOneWidget);
      await tester.tap(find.text('2026 · 0 files'));
      await tester.pumpAndSettle();
      expect(find.text('This folder is empty'), findsOneWidget);
      expect(find.text('Upload to 2026'), findsOneWidget);
      await tester.tap(find.widgetWithText(TextButton, 'All documents'));
      await tester.pumpAndSettle();

      await _tapText(tester, 'Urgent · 1');
      expect(find.text('readme.txt'), findsOneWidget);
      expect(find.text('notes.txt'), findsNothing);
      await _tapText(tester, 'Show every tag');

      await _tapText(tester, 'Select');
      await _tapTile(tester, 0);
      await _tapTile(tester, 2);
      expect(find.text('2 selected'), findsOneWidget);
      expect(
          find.text(
              'Some of the selected files are not yours, so only an owner can move or delete the selection.'),
          findsOneWidget);
      await _tapTile(tester, 2);
      await _tapTile(tester, 1);
      await _show(tester, find.widgetWithText(OutlinedButton, 'Move'),
          delta: -120);
      await tester.tap(find.widgetWithText(OutlinedButton, 'Move'));
      await tester.pumpAndSettle();
      expect(find.text('Move to'), findsOneWidget);
      await tester.tap(find.widgetWithText(ListTile, 'Contracts'));
      await tester.pumpAndSettle();
      expect(
          repo.calls, contains('bulkMove file_a,file_b to folder_contracts'));
      expect(find.text('Moved 2 files to Contracts.'), findsOneWidget);

      await _tapText(tester, 'Contracts · 3 files');
      await _tapTile(tester, 0);
      await _tapText(tester, 'Select all');
      await _show(tester, find.widgetWithText(OutlinedButton, 'Delete'),
          delta: -120);
      await tester.tap(find.widgetWithText(OutlinedButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(find.text('Delete 3 files?'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('bulkDelete file_a,file_b,file_d'));
      expect(find.text('Deleted 3 files.'), findsOneWidget);
    });

    testWidgets('New folder refuses a sibling name and creates a folder',
        (tester) async {
      final repo = _Repo();
      final c = FilesController(repo);
      await _pump(tester, FilesTab(controller: c, currentUserId: 'user_me'));
      await tester.tap(find.text('New folder'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'contracts');
      await tester.pumpAndSettle();
      expect(find.text('A folder named contracts already exists here.'),
          findsOneWidget);
      await tester.enterText(find.byType(TextField).last, 'Invoices');
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create folder'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('createFolder Invoices in null'));
      expect(find.text('Invoices · 0 files'), findsOneWidget);
    });

    testWidgets('the detail screen shows the folder and edits tags',
        (tester) async {
      final repo = _Repo();
      final c = FilesController(repo);
      await c.load();
      await _pump(tester, FileDetailScreen(controller: c, file: c.items[1]));
      expect(find.text('Folder'), findsOneWidget);
      expect(find.text('All documents'), findsOneWidget);
      expect(find.text('No tags'), findsOneWidget);
      await tester.tap(find.text('Add tag'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(ListTile, 'Urgent'));
      await tester.pumpAndSettle();
      expect(repo.calls.last, 'tagFile file_b tag_urgent');
      expect(find.widgetWithText(InputChip, 'Urgent'), findsOneWidget);
      await tester.tap(find.byTooltip('Remove tag Urgent'));
      await tester.pumpAndSettle();
      expect(repo.calls.last, 'untagFile file_b tag_urgent');
      expect(find.text('No tags'), findsOneWidget);
    });

    for (final (width, scale) in [
      (320, 1.0),
      (390, 2.0),
      (768, 1.0),
      (1024, 2.0),
      (1440, 1.0)
    ]) {
      testWidgets('fits at $width wide and ${scale}x text', (tester) async {
        final repo = _Repo();
        final c = FilesController(repo);
        await _pump(tester, FilesTab(controller: c, currentUserId: 'user_me'),
            size: Size(width.toDouble(), 900), textScale: scale);
        await _tapText(tester, 'Select');
        await _tapTile(tester, 0);
        await _show(tester, find.text('1 selected'), delta: -120);
        expect(find.text('1 selected'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('Arabic names the trail, tags and selection', (tester) async {
      final repo = _Repo();
      final c = FilesController(repo);
      await _pump(tester, FilesTab(controller: c, currentUserId: 'user_me'),
          arabic: true);
      expect(find.text('كل المستندات'), findsOneWidget);
      expect(find.text('مجلد جديد'), findsOneWidget);
      await _tapText(tester, 'تحديد');
      await _tapTile(tester, 0);
      await _tapTile(tester, 1);
      await _show(tester, find.text('محدد ملفان'), delta: -120);
      expect(find.text('محدد ملفان'), findsOneWidget);
      expect(find.text('نقل'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}

/// A repository whose first list call answers 404 (a deleted folder).
class _FailingOnce implements FilesRepository {
  _FailingOnce(this.inner);
  final _Repo inner;
  bool failed = false;

  @override
  Future<List<WorkspaceFile>> list(
      {String query = '',
      String sort = 'created_at:desc',
      String? cursor,
      int limit = filesPageSize,
      String? folder = everyFolder,
      String? tag}) {
    if (!failed && folder != null && folder != everyFolder) {
      failed = true;
      throw const ApiException(404, 'Folder not found');
    }
    return inner.list(
        query: query,
        sort: sort,
        cursor: cursor,
        limit: limit,
        folder: folder,
        tag: tag);
  }

  @override
  Future<FileUsage> usage() => inner.usage();
  @override
  Future<List<FileFolder>> folders() => inner.folders();
  @override
  Future<List<FileTag>> tags() => inner.tags();
  @override
  Future<FileFolder> createFolder(String name, String? parentId) =>
      inner.createFolder(name, parentId);
  @override
  Future<int> bulkMove(List<String> fileIds, String? folderId) =>
      inner.bulkMove(fileIds, folderId);
  @override
  Future<int> bulkDelete(List<String> fileIds) => inner.bulkDelete(fileIds);
  @override
  Future<WorkspaceFile> tagFile(String id, List<String> tagIds) =>
      inner.tagFile(id, tagIds);
  @override
  Future<WorkspaceFile> untagFile(String id, String tagId) =>
      inner.untagFile(id, tagId);
  @override
  Future<FileTag> createTag(String name) => inner.createTag(name);
  @override
  Future<WorkspaceFile> upload(String path,
          {required String name,
          required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation,
          String? folderId}) =>
      inner.upload(path,
          name: name,
          onProgress: onProgress,
          cancellation: cancellation,
          folderId: folderId);
  @override
  Future<WorkspaceFile> rename(String id, String name) =>
      inner.rename(id, name);
  @override
  Future<WorkspaceFile> reindex(String id) => inner.reindex(id);
  @override
  Future<void> delete(String id) => inner.delete(id);
  @override
  Future<FilePreviewText> previewText(String id) => inner.previewText(id);
  @override
  Future<Uint8List> bytes(String id) => inner.bytes(id);
  @override
  Future<void> download(WorkspaceFile file) => inner.download(file);
}
