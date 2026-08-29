import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import '../../features/workspace/api_workspace_repository.dart';
import '../../features/workspace/workspace_models.dart';

class HttpApiTransport implements ApiTransport {
  HttpApiTransport({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final response = await _client.send(http.Request(request.method, request.uri)
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
      return LocalSession.fromJson(jsonDecode(value) as JsonMap);
    } on Object {
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(LocalSession session) =>
      _storage.write(key: _key, value: jsonEncode(session.toJson()));
}

class HttpWorkspaceFileTransfer implements WorkspaceFileTransfer {
  HttpWorkspaceFileTransfer({
    required this.baseUri,
    required this.sessions,
    http.Client? client,
  }) : _client = client ?? http.Client();

  final Uri baseUri;
  final SessionStore sessions;
  final http.Client _client;

  Future<Map<String, String>> _headers() async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    return {'authorization': 'Bearer ${session.accessToken}'};
  }

  @override
  Future<JsonMap> upload(String path) async {
    final file = File(path);
    final bytes = await file.readAsBytes();
    final response = await _client.post(
      baseUri.resolve('api/v1/files'),
      headers: {
        ...await _headers(),
        'content-type': 'application/octet-stream',
        'x-file-name': file.uri.pathSegments.last,
      },
      body: bytes,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, _detail(response.body));
    }
    return jsonDecode(response.body) as JsonMap;
  }

  @override
  Future<void> download(WorkspaceFile file) async {
    final response = await _client.get(
      baseUri.resolve('api/v1/files/${file.id}/content'),
      headers: await _headers(),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, _detail(response.body));
    }
    await FilePicker.platform.saveFile(
      dialogTitle: 'Save ${file.name}',
      fileName: file.name,
      bytes: response.bodyBytes,
    );
  }

  String _detail(String body) {
    try {
      final value = jsonDecode(body) as JsonMap;
      return value['detail'] as String? ?? 'File transfer failed';
    } on Object {
      return 'File transfer failed';
    }
  }
}
