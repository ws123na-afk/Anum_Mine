import 'dart:io';
import 'dart:typed_data';

import 'package:cross_file/cross_file.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// A picked file backed by a real file on disk.
base class DiskPlatformFile extends PlatformFile {
  DiskPlatformFile(this._file);
  final File _file;

  @override
  String get name => _file.uri.pathSegments.last;

  @override
  Uri get uri => _file.uri;

  @override
  XFile get xFile => XFile(_file.path);

  @override
  int? lengthSync() => _file.lengthSync();

  @override
  Future<int?> length() async => _file.length();

  @override
  Future<Uint8List> readAsBytes() => _file.readAsBytes();

  @override
  Stream<Uint8List> readAsByteStream() =>
      _file.openRead().map(Uint8List.fromList);
}

/// The system file picker, answering with one real file on disk so the
/// app's streamed upload reads and sends its bytes.
class FakeFilePicker extends FilePickerPlatform {
  FakeFilePicker(this.file);
  final File file;

  /// What the app handed to the system save dialog.
  final saved = <(String, Uint8List)>[];

  @override
  Future<Uri?> saveFile({
    required String fileName,
    required Uint8List bytes,
    required String mimeType,
    String? dialogTitle,
    String? initialDirectory,
    dynamic Function(FilePickerStatus)? onFileSaving,
    WindowsOptions windowsOptions = const WindowsOptions(),
    LinuxOptions linuxOptions = const LinuxOptions(),
    WebOptions webOptions = const WebOptions(),
  }) async {
    saved.add((fileName, bytes));
    return Uri.file('/chosen/$fileName');
  }

  @override
  Future<List<PlatformFile>> pickFiles({
    String? dialogTitle,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    dynamic Function(FilePickerStatus)? onFileLoading,
    int compressionQuality = 0,
    AndroidOptions androidOptions = const AndroidOptions(),
    DarwinOptions darwinOptions = const DarwinOptions(),
    WindowsOptions windowsOptions = const WindowsOptions(),
    LinuxOptions linuxOptions = const LinuxOptions(),
    WebOptions webOptions = const WebOptions(),
  }) async =>
      [DiskPlatformFile(file)];
}

/// Workspace files (docs/files.md), in the API's `FileView` shape: the
/// upload is `POST /api/v1/files?name=...` with the raw bytes.
class FilesState {
  final files = <Map<String, Object?>>[];
  FakeRequest? upload;

  Map<String, Object?> view(String name, int size) => {
        'id': 'file_${files.length + 1}',
        'name': name,
        'content_type': 'text/markdown',
        'kind': 'markdown',
        'previewable': true,
        'can_manage': true,
        'size_bytes': size,
        'sha256': 'cd' * 32,
        'created_by': Fx.userId,
        'created_at': Fx.iso(),
        'updated_by': null,
        'updated_at': null,
        'index': {
          'status': 'indexed',
          'error': null,
          'chunk_count': 1,
          'embedding_model': 'anum-local-hash-v1',
          'indexed_at': Fx.iso(),
        },
      };

  void install(FakeAnumApi api) {
    api.get('/api/v1/files', (_) => FakeResponse.json(files));
    // A workspace with no folders or tags yet (docs/files.md#folders).
    api.get('/api/v1/files/folders',
        (_) => FakeResponse.json({'items': <Object>[]}));
    api.get(
        '/api/v1/files/tags', (_) => FakeResponse.json({'items': <Object>[]}));
    api.get(
        '/api/v1/files/usage',
        (_) => FakeResponse.json({
              'used_bytes': files.fold<int>(
                  0, (sum, f) => sum + (f['size_bytes']! as int)),
              'file_count': files.length,
              'quota_bytes': 2147483648,
              'max_upload_bytes': 26214400,
              'link_ttl_seconds': 300,
              'accepted': 'text, images, PDF, Office files and .zip',
              'accepted_extensions': ['.md', '.pdf', '.png', '.txt'],
              'can_upload': true,
              'can_manage_all': true,
            }));
    api.post('/api/v1/files', (r) {
      upload = r;
      final created = view(r.query['name']!, r.bytes.length);
      files.insert(0, created);
      return FakeResponse.json(created, status: 201);
    });
  }
}

/// A workspace with one folder of the caller's and two files in it, and the
/// folder, tag and zip routes (docs/files.md#folders, #tags, #bulk-actions).
class OrganizedFiles extends FilesState {
  Map<String, Object?> folder = {
    'id': 'folder_contracts',
    'name': 'Contracts',
    'path': 'Contracts',
    'parent_id': null,
    'depth': 1,
    'file_count': 2,
    'can_manage': true,
  };
  final tags = <Map<String, Object?>>[
    {
      'id': 'tag_urgent',
      'name': 'Urgent',
      'file_count': 1,
      'can_manage': true,
    }
  ];
  FakeRequest? renamed, zipped, tagDeleted;

  @override
  void install(FakeAnumApi api) {
    super.install(api);
    for (final name in ['lease.md', 'terms.md']) {
      files.add({
        ...view(name, 12),
        'folder_id': 'folder_contracts',
        'folder_path': 'Contracts',
      });
    }
    api.get(
        '/api/v1/files/folders',
        (_) => FakeResponse.json({
              'items': [folder]
            }));
    api.get('/api/v1/files/tags', (_) => FakeResponse.json({'items': tags}));
    api.patch('/api/v1/files/folders/{id}', (r) {
      renamed = r;
      folder = {...folder, 'name': r.body['name'], 'path': r.body['name']};
      return FakeResponse.json(folder);
    });
    api.delete('/api/v1/files/tags/{id}', (r) {
      tagDeleted = r;
      tags.removeWhere((t) => t['id'] == r.params['id']);
      return const FakeResponse.empty();
    });
    api.post('/api/v1/files/bulk/download', (r) {
      zipped = r;
      return FakeResponse.bytes([
        0x50,
        0x4b,
        0x05,
        0x06,
        ...List.filled(18, 0)
      ], headers: {
        'content-type': 'application/zip',
        'content-disposition': 'attachment; filename="files-2026-11-11.zip"',
      });
    });
  }
}

void filesJourneys() => group('Files', () {
      testWidgets(
          'files: rename a folder, delete a tag, download a selection as a zip',
          (tester) async {
        final previous = FilePickerPlatform.instance;
        final picker = FakeFilePicker(File('/dev/null'));
        FilePickerPlatform.instance = picker;
        addTearDown(() => FilePickerPlatform.instance = previous);

        final api = FakeAnumApi();
        seedBaseline(api);
        final files = OrganizedFiles()..install(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await waitFor(tester, find.text('Documents'), api: api);
        await tapOn(tester, find.text('Documents'));
        await waitFor(tester, find.textContaining('Contracts ·'), api: api);
        await tapOn(tester, find.textContaining('Contracts ·'));
        await waitFor(tester, find.byKey(const Key('folder-menu')), api: api);

        // Rename the open folder.
        await tapOn(tester, find.byKey(const Key('folder-menu')));
        await tapOn(tester, find.byKey(const Key('folder-rename')));
        await tester.enterText(
            find.byKey(const Key('name-dialog-field')), 'Agreements');
        await pumpFor(tester);
        await tapOn(tester, find.byKey(const Key('name-dialog-save')));
        await waitUntil(
            tester, 'the folder to be renamed', () => files.renamed != null,
            api: api);
        expect(files.renamed!.body, {'name': 'Agreements'});

        // Delete a tag from Manage tags.
        await tapOn(tester, find.byKey(const Key('tags-manage')));
        await tapOn(tester, find.byKey(const Key('tag-delete-tag_urgent')));
        await tapOn(tester, find.byKey(const Key('tag-delete-confirm')));
        await waitUntil(
            tester, 'the tag to be deleted', () => files.tagDeleted != null,
            api: api);
        await tester.tapAt(const Offset(20, 40));
        await pumpFor(tester);

        // Select both files and download them as one zip.
        await tapOn(tester, find.text('Select'));
        await tapOn(tester, find.text('lease.md'));
        await tapOn(tester, find.text('Select all'));
        await tapOn(tester, find.byKey(const Key('files-download-zip')));
        await waitUntil(tester, 'the zip to reach the save dialog',
            () => picker.saved.isNotEmpty,
            api: api);
        expect(files.zipped!.body['file_ids'], hasLength(2));
        expect(files.zipped!.headers['authorization'], startsWith('Bearer '));
        expect(picker.saved.single.$1, 'files-2026-11-11.zip');
        expect(picker.saved.single.$2.take(2), [0x50, 0x4b]);
        expect(api.unmatched.where((r) => r.contains('/files')), isEmpty);
      });

      testWidgets('files: upload a file, then find it in the list',
          (tester) async {
        final dir = await Directory.systemTemp.createTemp('anum_files');
        addTearDown(() => dir.delete(recursive: true));
        final picked = File('${dir.path}/release-notes.md')
          ..writeAsStringSync('# Release notes\n\nFaster exports.\n');
        final previous = FilePickerPlatform.instance;
        FilePickerPlatform.instance = FakeFilePicker(picked);
        addTearDown(() => FilePickerPlatform.instance = previous);

        final api = FakeAnumApi();
        seedBaseline(api);
        final files = FilesState()..install(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await waitFor(tester, find.text('Documents'), api: api);
        await tapOn(tester, find.text('Documents'));
        await waitFor(tester, find.text('Upload'), api: api);
        expect(find.text('release-notes.md'), findsNothing);
        await tapOn(tester, find.text('Upload'));

        // The upload finishes (no progress row left) and the list shows the file.
        await waitUntil(
            tester,
            'the upload to finish and the list to show it',
            () =>
                files.upload != null &&
                find.text('Cancel').evaluate().isEmpty &&
                find.text('No files yet').evaluate().isEmpty &&
                find.text('release-notes.md').evaluate().isNotEmpty,
            api: api);
        final sent = files.upload!;
        expect(sent.query['name'], 'release-notes.md');
        expect(sent.headers['content-type'], 'application/octet-stream');
        expect(String.fromCharCodes(sent.bytes), contains('Faster exports.'));
        // The list was read again from the API after the upload.
        expect(
            api.log
                .where((r) => r.method == 'GET' && r.path == '/api/v1/files'),
            hasLength(greaterThan(1)));
        expect(api.unmatched.where((r) => r.contains('/files')), isEmpty);
      });
    });
