import 'package:file_picker/file_picker.dart';

/// The size in bytes of a picked file: the length the native picker already
/// reported, else a read from disk, else 0 (file_picker 13 dropped
/// `PlatformFile.size`; `length()` returns null when it cannot be read).
Future<int> pickedFileSize(PlatformFile file) async =>
    file.lengthSync() ?? await file.length() ?? 0;
