// A stand-in for the system save dialog in tests (records_export.dart).
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/features/records_kit/records_export.dart';

class FakeExportSaver implements ExportSaver {
  FakeExportSaver({this.accept = true, this.fail = false});
  bool accept, fail;
  final saved = <String, String>{};

  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    if (fail) throw StateError('disk full');
    if (!accept) return false;
    // Drops the byte order mark the export adds for spreadsheet apps.
    saved[fileName] = utf8.decode(bytes.sublist(3));
    return true;
  }
}
