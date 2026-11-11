import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'records_kit.dart';
import 'records_widgets.dart';

/// Spreadsheet exports on the phone (docs/mobile.md#exports): the API's CSV
/// file is handed to the system save dialog, where the person picks where it
/// goes. The app writes nothing to its own storage, so no copy is left
/// behind in a readable folder, and nothing is shared without the person's
/// choice.

/// Hands a file to the person. Replaced in tests.
abstract interface class ExportSaver {
  /// True when the file was saved, false when the person cancelled.
  Future<bool> save(String fileName, Uint8List bytes);
}

/// The system save dialog through `file_picker` (Android's document picker,
/// the iOS document picker): the bytes go straight to the chosen place.
class SystemExportSaver implements ExportSaver {
  const SystemExportSaver();
  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    final path = await FilePicker.saveFile(
      dialogTitle: fileName,
      fileName: fileName,
      bytes: bytes,
    );
    return path != null;
  }
}

/// Reads a CSV route's text: the HTTP transport hands a `text/csv` answer
/// over as `{"csv": ...}`.
Future<String> fetchCsv(AnumApiClient api, String path) async {
  final body = await api.request('GET', path);
  final text = JsonReader(body).optString('csv');
  if (text == null) {
    throw const FormatException('The server did not send a CSV file.');
  }
  return text;
}

/// Fetches a CSV with [load] and offers it to [saver] as [fileName], then
/// says what happened in a snack bar: saved, nothing saved (cancelled),
/// refused or failed in the API's words. Returns whether it was saved.
Future<bool> exportCsvFile(
  BuildContext context, {
  required Future<String> Function() load,
  required String fileName,
  ExportSaver saver = const SystemExportSaver(),
}) async {
  final k = KitStrings.of(context);
  final String text;
  try {
    text = await load();
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(
          context,
          k.t('export.failed',
              {'message': RecordsFailure.from(error).message}));
    }
    return false;
  }
  bool saved;
  try {
    // A byte order mark so spreadsheet apps read Arabic text as UTF-8.
    saved = await saver.save(
        fileName, Uint8List.fromList([0xEF, 0xBB, 0xBF, ...utf8.encode(text)]));
  } on Object {
    if (context.mounted) recordsToast(context, k.t('export.saveFailed'));
    return false;
  }
  if (context.mounted) {
    recordsToast(
        context,
        saved
            ? k.t('export.saved', {'name': fileName})
            : k.t('export.cancelled'));
  }
  return saved;
}

/// An "Export CSV" button that disables itself while the export runs.
class ExportCsvButton extends StatefulWidget {
  const ExportCsvButton({
    required this.load,
    required this.fileName,
    this.saver = const SystemExportSaver(),
    super.key,
  });
  final Future<String> Function() load;
  final String fileName;
  final ExportSaver saver;

  @override
  State<ExportCsvButton> createState() => _ExportCsvButtonState();
}

class _ExportCsvButtonState extends State<ExportCsvButton> {
  bool _busy = false;

  Future<void> _run() async {
    setState(() => _busy = true);
    await exportCsvFile(context,
        load: widget.load, fileName: widget.fileName, saver: widget.saver);
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    return OutlinedButton.icon(
      onPressed: _busy ? null : _run,
      icon: const Icon(Icons.download_outlined),
      label: Text(_busy ? k.t('export.working') : k.t('export.csv')),
    );
  }
}
