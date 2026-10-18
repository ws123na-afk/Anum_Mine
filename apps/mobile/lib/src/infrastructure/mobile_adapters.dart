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
import '../../features/mail_center/mail_center_transfer.dart';
import '../../features/mail_center/mail_directory_models.dart';
import '../../features/meetings/meetings_repository.dart';
import '../../features/auth/workspace_selection.dart';
import '../../features/workspace/api_workspace_repository.dart';
import '../../features/workspace/files_repository.dart';
import '../../features/workspace/workspace_models.dart';
import '../../features/governance/api_governance_repository.dart';
import '../../features/privacy/privacy_repository.dart';

class HttpApiTransport implements ApiTransport {
  HttpApiTransport({http.Client? client, this.timeout = defaultTimeout})
      : _client = client ?? http.Client();

  /// The longest one JSON call may take, headers and body together. Long
  /// enough for a model call with its server-side retries; it only stops a
  /// connection that hangs from spinning forever (streams have their own
  /// timeouts in `HttpEventStream`).
  static const defaultTimeout = Duration(minutes: 5);

  final http.Client _client;
  final Duration timeout;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    try {
      return await _send(request).timeout(timeout);
    } on TimeoutException {
      throw const ApiException(0,
          'The server did not answer in time. Check the connection and try again.');
    } on HandshakeException {
      throw ApiException(0,
          'TLS handshake failed with ${request.uri.host}. Please try again.');
    } on http.ClientException {
      throw const ApiException(0,
          'The server could not be reached. Check the connection and try again.');
    } on SocketException {
      throw const ApiException(0,
          'The server could not be reached. Check the connection and try again.');
    }
  }

  Future<ApiResponse> _send(ApiRequest request) async {
    final response =
        await _client.send(http.Request(request.method, request.uri)
          ..headers.addAll(request.headers)
          ..body = request.body == null ? '' : jsonEncode(request.body));
    final bodyText = await response.stream.bytesToString();
    JsonMap? body;
    if (bodyText.isNotEmpty) {
      final Object? decoded;
      try {
        decoded = jsonDecode(bodyText);
      } on FormatException {
        // A proxy's HTML error page (502/503 from Caddy or the host) is not
        // JSON; the status code still says what failed.
        if (response.statusCode >= 400) {
          return ApiResponse(statusCode: response.statusCode, body: null);
        }
        rethrow;
      }
      body = decoded is Map<String, dynamic> ? decoded : {'data': decoded};
    }
    return ApiResponse(statusCode: response.statusCode, body: body);
  }
}

/// iOS keychain class for everything the app keeps in secure storage: readable
/// after the first unlock (background refresh and push still work) and never
/// restored to another device from a backup (`...ThisDeviceOnly`). Android
/// ignores it; its items are already bound to the device's keystore and app
/// backups are off.
const deviceOnlyKeychain =
    IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device);

/// Items written before [deviceOnlyKeychain] used the plugin default
/// (`WhenUnlocked`, which an encrypted backup carries to a new device).
const _legacyKeychain = IOSOptions.defaultOptions;

/// Reads [key] as a device-only item, moving an item stored with the old
/// keychain class over once. The keychain query includes the class, so the
/// old item is deleted before the new one is added (the class is not part of
/// an item's identity, so adding first would collide).
Future<String?> readDeviceOnly(FlutterSecureStorage storage, String key) async {
  final value = await storage.read(key: key, iOptions: deviceOnlyKeychain);
  if (value != null) return value;
  final legacy = await storage.read(key: key, iOptions: _legacyKeychain);
  if (legacy == null) return null;
  await storage.delete(key: key, iOptions: _legacyKeychain);
  await storage.write(key: key, value: legacy, iOptions: deviceOnlyKeychain);
  return legacy;
}

class SecureSessionStore implements SessionStore {
  SecureSessionStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'anum.local-session';
  final FlutterSecureStorage _storage;

  @override
  Future<void> clear() async {
    await _storage.delete(key: _key, iOptions: deviceOnlyKeychain);
    // A session written before the device-only class must not outlive sign-out.
    await _storage.delete(key: _key, iOptions: _legacyKeychain);
  }

  @override
  Future<LocalSession?> read() async {
    final value = await readDeviceOnly(_storage, _key);
    if (value == null) return null;
    try {
      return LocalSession.fromJson(jsonObject(jsonDecode(value), 'session'));
    } on Object {
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(LocalSession session) => _storage.write(
      key: _key,
      value: jsonEncode(session.toJson()),
      iOptions: deviceOnlyKeychain);
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
      readDeviceOnly(_storage, _key(tenantId, userId));

  @override
  Future<void> write(
          {required String tenantId,
          required String userId,
          required String workspaceId}) =>
      _storage.write(
          key: _key(tenantId, userId),
          value: workspaceId,
          iOptions: deviceOnlyKeychain);
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

/// Email Center file bytes (docs/email.md#attachments): device files picked
/// with the system picker and streamed to a draft with progress and cancel,
/// and downloads through the API saved with the system save dialog. The
/// bearer token is sent only to the configured API.
class HttpMailFileTransfer implements MailFileTransfer {
  HttpMailFileTransfer({
    required this.baseUri,
    required this.sessions,
    required this.files,
    http.Client? client,
  }) : _client = client ?? http.Client();

  final Uri baseUri;
  final SessionStore sessions;
  final HttpWorkspaceFileTransfer files;
  final http.Client _client;

  @override
  Future<List<PickedMailFile>> pick(List<String> extensions) async {
    final allowed = [
      for (final e in extensions)
        if (e.startsWith('.') && e.length > 1) e.substring(1)
    ];
    final picked = await FilePicker.platform.pickFiles(
      allowMultiple: true,
      withData: false,
      type: allowed.isEmpty ? FileType.any : FileType.custom,
      allowedExtensions: allowed.isEmpty ? null : allowed,
    );
    return [
      for (final f in picked?.files ?? const <PlatformFile>[])
        if (f.path != null) PickedMailFile(f.path!, f.name, f.size),
    ];
  }

  @override
  Future<JsonMap> upload(
    String draftId,
    PickedMailFile file, {
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  }) =>
      files.uploadTo(
          'api/v1/mail/drafts/${Uri.encodeComponent(draftId)}/attachments',
          {'name': file.name},
          file.path,
          onProgress: onProgress,
          cancellation: cancellation);

  @override
  Future<Uint8List> download(String path) async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    final http.Response response;
    try {
      response = await _client
          .get(baseUri.resolve(path.replaceFirst(RegExp(r'^/'), '')),
              headers: sessionHeaders(session))
          .timeout(HttpApiTransport.defaultTimeout);
    } on TimeoutException {
      throw const ApiException(0,
          'The server did not answer in time. Check the connection and try again.');
    } on http.ClientException {
      throw const ApiException(0,
          'The server could not be reached. Check the connection and try again.');
    } on SocketException {
      throw const ApiException(0,
          'The server could not be reached. Check the connection and try again.');
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      String message;
      try {
        final reader = JsonReader.of(jsonDecode(response.body));
        message = reader.optObject('error')?.optString('message') ??
            reader.optString('detail') ??
            'The download failed.';
      } on Object {
        message = 'The download failed.';
      }
      throw ApiException(response.statusCode, message);
    }
    return response.bodyBytes;
  }

  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    final path = await FilePicker.platform.saveFile(
      dialogTitle: 'Save $fileName',
      fileName: fileName,
      bytes: bytes,
    );
    return path != null;
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
