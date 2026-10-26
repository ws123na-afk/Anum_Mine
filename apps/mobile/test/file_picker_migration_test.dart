import 'dart:convert';
import 'dart:typed_data';

import 'package:cross_file/cross_file.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:anum_mobile/features/business/invoice_document.dart';
import 'package:anum_mobile/features/chat/chat_attachments.dart';
import 'package:anum_mobile/features/finance/finance_intel_actions.dart';
import 'package:anum_mobile/features/finance/finance_intel_models.dart';
import 'package:anum_mobile/features/meetings/meetings_screen.dart';
import 'package:anum_mobile/features/voice/call_screen.dart';
import 'package:anum_mobile/features/workspace/files_screen.dart';
import 'package:anum_mobile/src/infrastructure/picked_file_size.dart';

/// An in-memory picked file. [reported] is what the native picker says the
/// length is (null: it did not report one); [onDisk] is what a read finds.
base class MemoryPlatformFile extends PlatformFile {
  MemoryPlatformFile(
    this.name,
    this.bytes, {
    this.reported,
    this.onDisk,
    this.filePath,
    this.readFails = false,
  });

  @override
  final String name;
  final Uint8List bytes;
  final int? reported;
  final int? onDisk;
  final String? filePath;
  final bool readFails;

  @override
  Uri get uri =>
      filePath == null ? Uri.parse('content://x/$name') : Uri.file(filePath!);

  @override
  XFile get xFile => XFile.fromData(bytes, name: name);

  @override
  int? lengthSync() => reported;

  @override
  Future<int?> length() async => onDisk;

  @override
  Future<Uint8List> readAsBytes() async {
    if (readFails) throw const FileSystemExceptionStub();
    return bytes;
  }

  @override
  Stream<Uint8List> readAsByteStream() {
    if (readFails) return Stream.error(const FileSystemExceptionStub());
    return Stream.fromIterable([
      for (var i = 0; i < bytes.length; i += 2)
        bytes.sublist(i, i + 2 > bytes.length ? bytes.length : i + 2)
    ]);
  }
}

class FileSystemExceptionStub implements Exception {
  const FileSystemExceptionStub();
}

class FakePicker extends FilePickerPlatform {
  List<PlatformFile> next = const [];
  FileType? lastType;
  List<String>? lastExtensions;
  final saved = <(String, Uint8List)>[];
  Uri? saveResult = Uri.parse('file:///saved');

  @override
  Future<PlatformFile?> pickFile({
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
  }) async {
    lastType = type;
    lastExtensions = allowedExtensions;
    return next.isEmpty ? null : next.first;
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
  }) async {
    lastType = type;
    lastExtensions = allowedExtensions;
    return next;
  }

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
    return saveResult;
  }
}

Uint8List _text(String value) => Uint8List.fromList(utf8.encode(value));

void main() {
  late FakePicker picker;
  late FilePickerPlatform previous;

  setUp(() {
    previous = FilePickerPlatform.instance;
    picker = FakePicker();
    FilePickerPlatform.instance = picker;
  });
  tearDown(() => FilePickerPlatform.instance = previous);

  group('pickedFileSize', () {
    test('uses the length the picker reported', () async {
      final file =
          MemoryPlatformFile('a.txt', _text('abc'), reported: 3, onDisk: 99);
      expect(await pickedFileSize(file), 3);
    });

    test('falls back to a read from disk', () async {
      expect(
          await pickedFileSize(
              MemoryPlatformFile('a.txt', _text('abc'), onDisk: 3)),
          3);
    });

    test('counts the content when the provider reports no size', () async {
      expect(
          await pickedFileSize(MemoryPlatformFile('a.txt', _text('abcde'))), 5);
      // A genuinely empty file is 0; an unreadable one is unknown, not 0.
      expect(await pickedFileSize(MemoryPlatformFile('a.txt', _text(''))), 0);
      expect(
          await pickedFileSize(
              MemoryPlatformFile('a.txt', _text('abc'), readFails: true)),
          isNull);
    });
  });

  group('Files and mail pickers', () {
    test('pickDeviceFiles returns path, name and size, skipping pathless',
        () async {
      picker.next = [
        MemoryPlatformFile('a.md', _text('hello'),
            reported: 5, filePath: '/tmp/a.md'),
        MemoryPlatformFile('b.md', _text('x'), reported: 1),
      ];
      expect(await pickDeviceFiles(), [('/tmp/a.md', 'a.md', 5)]);
    });

    test('pickDeviceFiles is empty when cancelled', () async {
      expect(await pickDeviceFiles(), isEmpty);
    });

    test('pickMeetingFile restricts extensions and returns the size', () async {
      picker.next = [
        MemoryPlatformFile('m.vtt', _text('WEBVTT'),
            onDisk: 6, filePath: '/tmp/m.vtt'),
      ];
      expect(
          await pickMeetingFile(['.vtt', '.txt']), ('/tmp/m.vtt', 'm.vtt', 6));
      expect(picker.lastType, FileType.custom);
      expect(picker.lastExtensions, ['vtt', 'txt']);
    });

    test('pickMeetingFile is null when cancelled or without a path', () async {
      expect(await pickMeetingFile(['.vtt']), isNull);
      picker.next = [MemoryPlatformFile('m.vtt', _text('x'), reported: 1)];
      expect(await pickMeetingFile(['.vtt']), isNull);
    });
  });

  group('Chat attachment picker', () {
    const chat = DeviceChatAttachmentPicker();

    test('reads a small text file', () async {
      picker.next = [MemoryPlatformFile('n.txt', _text('hi'), reported: 2)];
      final attachment = await chat.pick();
      expect(attachment?.name, 'n.txt');
      expect(attachment?.text, 'hi');
      expect(picker.lastExtensions, chatAttachmentExtensions);
    });

    test('reads a file with no reported size', () async {
      picker.next = [MemoryPlatformFile('n.txt', _text('hi'))];
      expect((await chat.pick())?.text, 'hi');
    });

    test('is null when cancelled', () async {
      expect(await chat.pick(), isNull);
    });

    test('rejects a file over the size limit before reading it', () async {
      picker.next = [
        MemoryPlatformFile('big.txt', _text('x'),
            reported: chatAttachmentMaxBytes + 1, readFails: true),
      ];
      expect(
          chat.pick(),
          throwsA(isA<ChatAttachmentException>()
              .having((e) => e.reason, 'reason', 'size')));
    });

    test('reports an unreadable file as an encoding problem', () async {
      picker.next = [
        MemoryPlatformFile('n.txt', _text('hi'), reported: 2, readFails: true),
      ];
      expect(
          chat.pick(),
          throwsA(isA<ChatAttachmentException>()
              .having((e) => e.reason, 'reason', 'encoding')));
    });
  });

  group('Bars file picker', () {
    test('reads a CSV as text', () async {
      picker.next = [
        MemoryPlatformFile('bars.csv', _text('a,b\n1,2\n'), reported: 8)
      ];
      final file = await pickBarsFile();
      expect(file?.name, 'bars.csv');
      expect(file?.text, 'a,b\n1,2\n');
      expect(file?.problem, isNull);
    });

    test('flags an empty file, a large file and non-text bytes', () async {
      picker.next = [MemoryPlatformFile('e.csv', _text(''), reported: 0)];
      expect((await pickBarsFile())?.problem, 'fileEmpty');
      picker.next = [
        MemoryPlatformFile('big.csv', _text('x'),
            reported: maxBarsFileBytes + 1),
      ];
      expect((await pickBarsFile())?.problem, 'fileTooBig');
      picker.next = [
        MemoryPlatformFile('bin.csv', Uint8List.fromList([0xff, 0xfe, 0xfd]),
            reported: 3),
      ];
      expect((await pickBarsFile())?.problem, 'fileNotText');
    });

    test('a file with no reported size is read, not treated as empty',
        () async {
      picker.next = [MemoryPlatformFile('bars.csv', _text('a,b\n1,2\n'))];
      final file = await pickBarsFile();
      expect(file?.problem, isNull);
      expect(file?.text, 'a,b\n1,2\n');
    });

    test('is null when cancelled', () async {
      expect(await pickBarsFile(), isNull);
    });
  });

  group('Photo picker', () {
    test('returns bytes and a media type from the extension', () async {
      picker.next = [MemoryPlatformFile('p.PNG', _text('png'))];
      final photo = await pickDevicePhoto();
      expect(photo?.$2, 'image/png');
      expect(photo?.$1, _text('png'));
      expect(picker.lastType, FileType.image);
    });

    test('is null when cancelled or unreadable', () async {
      expect(await pickDevicePhoto(), isNull);
      picker.next = [MemoryPlatformFile('p.jpg', _text('x'), readFails: true)];
      expect(await pickDevicePhoto(), isNull);
    });
  });

  group('Saving', () {
    test('the invoice saver reports saved or cancelled', () async {
      const saver = FilePickerInvoiceSaver();
      expect(await saver.save('inv.html', _text('<p>')), isTrue);
      expect(picker.saved.single.$1, 'inv.html');
      picker.saveResult = null;
      expect(await saver.save('inv.html', _text('<p>')), isFalse);
    });
  });
}
