import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/session_store.dart';
import '../../src/infrastructure/picked_file_size.dart';
import 'vault_data.dart';

/// Picks one file on the phone: (path, name, size), or null when cancelled.
typedef VaultFilePicker = Future<(String, String, int?)?> Function(
    List<String> extensions);

/// The system file picker, limited to the types the vault accepts.
Future<(String, String, int?)?> pickVaultFile(List<String> extensions) async {
  final allowed = [
    for (final extension in extensions) extension.replaceFirst('.', '')
  ];
  final file = await FilePicker.pickFile(
    type: allowed.isEmpty ? FileType.any : FileType.custom,
    allowedExtensions: allowed.isEmpty ? null : allowed,
  );
  if (file?.path == null) return null;
  return (file!.path!, file.name, await pickedFileSize(file));
}

/// Downloads a document's bytes with the signed-in session and saves them
/// with the system save dialog. The bearer token goes only to the API.
class HttpVaultFileSaver implements VaultFileSaver {
  HttpVaultFileSaver(
      {required this.baseUri, required this.sessions, http.Client? client})
      : _client = client ?? http.Client();
  final Uri baseUri;
  final SessionStore sessions;
  final http.Client _client;

  @override
  Future<void> save(String documentId, String name, {String? versionId}) async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    final uri = baseUri
        .resolve('api/v1/documents/${Uri.encodeComponent(documentId)}/content')
        .replace(
            queryParameters: versionId == null ? null : {'version_id': versionId});
    final response = await _client.get(uri, headers: sessionHeaders(session));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      var message = 'The download failed (${response.statusCode}).';
      try {
        final body = jsonDecode(response.body);
        if (body is Map && body['detail'] is String) message = body['detail'] as String;
      } on FormatException {
        // Not JSON: keep the status sentence.
      }
      throw ApiException(response.statusCode, message);
    }
    await FilePicker.saveFile(
        dialogTitle: name, fileName: name, bytes: response.bodyBytes);
  }
}
