import 'dart:typed_data';

import '../../data/api_models.dart';
import '../workspace/files_repository.dart';
import 'mail_directory_models.dart';

/// File bytes for the Email Center (docs/email.md#attachments): picking device
/// files, streaming one to a draft with progress and cancel, downloading a
/// received or attached file through the API, and saving it with the system
/// save dialog. The JSON client cannot carry raw bytes, so this is separate.
abstract interface class MailFileTransfer {
  /// Device files the person picks, limited to [extensions] (with the dot).
  Future<List<PickedMailFile>> pick(List<String> extensions);

  /// `POST /api/v1/mail/drafts/{id}/attachments?name=` with the raw file.
  Future<JsonMap> upload(
    String draftId,
    PickedMailFile file, {
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  });

  /// The bytes of an API download ([path] starts with `/api/v1/mail`).
  Future<Uint8List> download(String path);

  /// Saves [bytes] as [fileName]; false when the person cancelled.
  Future<bool> save(String fileName, Uint8List bytes);
}

/// No file access (tests and builds without a transfer): every call says so.
class NoMailFileTransfer implements MailFileTransfer {
  const NoMailFileTransfer();

  static Never get _unavailable => throw UnsupportedError(
      'Files are not available in this build. Use the web app.');

  @override
  Future<List<PickedMailFile>> pick(List<String> extensions) async =>
      _unavailable;

  @override
  Future<JsonMap> upload(String draftId, PickedMailFile file,
          {required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation}) async =>
      _unavailable;

  @override
  Future<Uint8List> download(String path) async => _unavailable;

  @override
  Future<bool> save(String fileName, Uint8List bytes) async => _unavailable;
}
