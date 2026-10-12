import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import '../../features/meetings/meetings_repository.dart';
import '../../features/auth/workspace_selection.dart';
import '../../features/workspace/api_workspace_repository.dart';
import '../../features/workspace/files_repository.dart';
import '../../features/workspace/workspace_models.dart';
import '../../features/governance/api_governance_repository.dart';
import '../../features/privacy/privacy_repository.dart';

class HttpApiTransport implements ApiTransport {
  HttpApiTransport({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final response =
        await _client.send(http.Request(request.method, request.uri)
          ..headers.addAll(request.headers)
          ..body = request.body == null ? '' : jsonEncode(request.body));
    final bodyText = await response.stream.bytesToString();
    JsonMap? body;
    if (bodyText.isNotEmpty) {
      final decoded = jsonDecode(bodyText);
      body = decoded is Map<String, dynamic> ? decoded : {'data': decoded};
    }
    return ApiResponse(statusCode: response.statusCode, body: body);
  }
}

class SecureSessionStore implements SessionStore {
  SecureSessionStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'anum.local-session';
  final FlutterSecureStorage _storage;

  @override
  Future<void> clear() => _storage.delete(key: _key);

  @override
  Future<LocalSession?> read() async {
    final value = await _storage.read(key: _key);
    if (value == null) return null;
    try {
      return LocalSession.fromJson(jsonObject(jsonDecode(value), 'session'));
    } on Object {
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(LocalSession session) =>
      _storage.write(key: _key, value: jsonEncode(session.toJson()));
}

/// The workspace picked on this device, per tenant and user, in platform
/// secure storage. Signing out keeps it (it is an id, not a credential), so
/// the next sign-in of the same person opens the same workspace.
class SecureWorkspacePreferenceStore implements WorkspacePreferenceStore {
  SecureWorkspacePreferenceStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  static String _key(String tenantId, String userId) =>
      'anum.workspace-choice.${Uri.encodeComponent(tenantId)}.'
      '${Uri.encodeComponent(userId)}';

  @override
  Future<String?> read(
          {required String tenantId, required String userId}) async =>
      _storage.read(key: _key(tenantId, userId));

  @override
  Future<void> write(
          {required String tenantId,
          required String userId,
          required String workspaceId}) =>
      _storage.write(key: _key(tenantId, userId), value: workspaceId);
}

/// Workspace file bytes (docs/files.md#mobile): streamed uploads with
/// progress and cancel, previews and downloads through short-lived links.
class HttpWorkspaceFileTransfer
    implements
        WorkspaceFileTransfer,
        FileStreamTransfer,
        MeetingUploadTransfer {
  HttpWorkspaceFileTransfer({
    required this.baseUri,
    required this.sessions,
    http.Client? client,
    http.Client Function()? uploadClient,
  })  : _client = client ?? http.Client(),
        _uploadClient = uploadClient ?? http.Client.new;

  final Uri baseUri;
  final SessionStore sessions;
  final http.Client _client;

  /// One client per upload, so cancelling closes only that connection.
  final http.Client Function() _uploadClient;

  Future<Map<String, String>> _headers() async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    return sessionHeaders(session);
  }

  Uri _uploadUri(String endpoint, Map<String, String> query) =>
      baseUri.resolve(endpoint).replace(queryParameters: query);

  @override
  Future<JsonMap> upload(String path) async {
    final file = File(path);
    return uploadStream(path,
        name: file.uri.pathSegments.last,
        onProgress: (_, __) {},
        cancellation: UploadCancellation());
  }

  @override
  Future<JsonMap> uploadStream(
    String path, {
    required String name,
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  }) =>
      uploadTo('api/v1/files', {'name': name}, path,
          onProgress: onProgress, cancellation: cancellation);

  /// Streams the file at [path] to [endpoint] (Files, or a meeting
  /// recording or transcript import, docs/meetings.md) with progress.
  @override
  Future<JsonMap> uploadTo(
    String endpoint,
    Map<String, String> query,
    String path, {
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  }) async {
    final file = File(path);
    final total = await file.length();
    final client = _uploadClient();
    var sent = 0;
    final request = http.StreamedRequest('POST', _uploadUri(endpoint, query))
      ..headers.addAll({
        ...await _headers(),
        'content-type': 'application/octet-stream',
      })
      ..contentLength = total;
    unawaited(cancellation.whenCancelled.then((_) => client.close()));
    unawaited(file
        .openRead()
        .listen((chunk) {
          if (cancellation.isCancelled) return;
          sent += chunk.length;
          request.sink.add(chunk);
          onProgress(sent, total);
        }, onDone: request.sink.close, onError: request.sink.addError)
        .asFuture<void>()
        .catchError((Object _) {}));
    try {
      final response =
          await http.Response.fromStream(await client.send(request));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(response.statusCode, _detail(response.body));
      }
      return jsonObject(jsonDecode(response.body));
    } on ApiException {
      rethrow;
    } catch (_) {
      if (cancellation.isCancelled) throw const UploadCancelledException();
      rethrow;
    } finally {
      client.close();
    }
  }

  /// A short-lived link (no bearer token needed to read it).
  Future<Uri> _link(String fileId, String disposition) async {
    final response = await _client.post(
      baseUri.resolve('api/v1/files/${Uri.encodeComponent(fileId)}/links'),
      headers: {...await _headers(), 'content-type': 'application/json'},
      body: jsonEncode({'disposition': disposition}),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, _detail(response.body));
    }
    final url = JsonReader.of(jsonDecode(response.body)).string('url');
    return baseUri.resolve(url.replaceFirst(RegExp(r'^/'), ''));
  }

  Future<Uint8List> _fetch(Uri link) async {
    final response = await _client.get(link);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, _detail(response.body));
    }
    return response.bodyBytes;
  }

  @override
  Future<Uint8List> bytes(String fileId) async =>
      _fetch(await _link(fileId, 'inline'));

  @override
  Future<void> saveCopy(WorkspaceFile file) => download(file);

  @override
  Future<void> download(WorkspaceFile file) async {
    final bytes = await _fetch(await _link(file.id, 'attachment'));
    await FilePicker.platform.saveFile(
      dialogTitle: 'Save ${file.name}',
      fileName: file.name,
      bytes: bytes,
    );
  }

  String _detail(String body) {
    try {
      final reader = JsonReader.of(jsonDecode(body));
      return reader.optObject('error')?.optString('message') ??
          reader.optString('detail') ??
          'File transfer failed';
    } on Object {
      return 'File transfer failed';
    }
  }
}

class HttpAuditExporter implements AuditExporter {
  HttpAuditExporter(
      {required this.baseUri, required this.sessions, http.Client? client})
      : _client = client ?? http.Client();
  final Uri baseUri;
  final SessionStore sessions;
  final http.Client _client;

  @override
  Future<void> export(String format) async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    final response = await _client.get(
      baseUri.resolve('api/v1/audit/export?format=$format'),
      headers: sessionHeaders(session),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, 'Audit export failed');
    }
    await FilePicker.platform.saveFile(
      dialogTitle: 'Save audit export',
      fileName: 'audit-export.$format',
      bytes: response.bodyBytes,
    );
  }
}

/// Saves "Download my data" through the system save dialog (docs/privacy.md).
class FilePickerExportSaver implements PrivacyExportSaver {
  const FilePickerExportSaver();

  @override
  Future<bool> save(String fileName, List<int> bytes) async {
    final path = await FilePicker.platform.saveFile(
      dialogTitle: 'Save your ANUM data',
      fileName: fileName,
      bytes: Uint8List.fromList(bytes),
    );
    return path != null;
  }
}
