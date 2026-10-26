import 'package:file_picker/file_picker.dart';

/// The size in bytes of a picked file, or null when it cannot be determined.
///
/// file_picker 13 dropped `PlatformFile.size`, and a provider may expose no
/// size metadata (`lengthSync()` and `length()` are then null). An unknown
/// size is not an empty file: it is counted by streaming the content, and
/// only a file that cannot be read at all answers null. Callers treat null as
/// unknown (the server still enforces its limits), never as 0.
Future<int?> pickedFileSize(PlatformFile file) async {
  final known = file.lengthSync() ?? await file.length();
  if (known != null) return known;
  try {
    var total = 0;
    await for (final chunk in file.readAsByteStream()) {
      total += chunk.length;
    }
    return total;
  } on Object {
    return null;
  }
}
