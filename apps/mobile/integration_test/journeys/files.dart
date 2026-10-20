import 'dart:io';
import 'dart:typed_data';

import 'package:cross_file/cross_file.dart';
import 'package:file_picker/file_picker.dart';
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

void filesJourneys() => group('Files', () {
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
