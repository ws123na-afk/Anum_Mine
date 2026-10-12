import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/push/push_messaging.dart';

/// A phone's push channel under test control.
class FakePushMessaging implements PushMessaging {
  FakePushMessaging(
      {this.available = true,
      this.current = PushPermission.notDetermined,
      this.answer = PushPermission.granted,
      this.currentToken = 'fcm-token-example-phone'});

  bool available;
  PushPermission current;

  /// What the system prompt answers.
  PushPermission answer;
  String? currentToken;
  int prompts = 0;
  int tokenDeletes = 0;
  IncomingPush? launch;
  final refreshes = StreamController<String>.broadcast();
  final foreground = StreamController<IncomingPush>.broadcast();
  final opened = StreamController<IncomingPush>.broadcast();

  @override
  Future<bool> initialize() async => available;
  @override
  String get platform => 'android';
  @override
  String get deviceLabel => 'Android phone';
  @override
  Future<PushPermission> permission() async => current;
  @override
  Future<PushPermission> requestPermission() async {
    prompts++;
    current = answer;
    return answer;
  }

  @override
  Future<String?> token() async => currentToken;
  @override
  Future<void> deleteToken() async => tokenDeletes++;
  @override
  Stream<String> get tokenRefreshes => refreshes.stream;
  @override
  Stream<IncomingPush> get foregroundPushes => foreground.stream;
  @override
  Stream<IncomingPush> get openedPushes => opened.stream;
  @override
  Future<IncomingPush?> launchPush() async => launch;
}

/// Just enough of the API for phone push, keyed like the server: devices per
/// workspace and user.
class FakePushApi implements ApiTransport {
  FakePushApi({this.serverOn = true});
  bool serverOn;
  bool failDevices = false;
  final List<ApiRequest> requests = [];
  final Map<String, List<JsonMap>> devices = {};
  var _next = 0;

  String _scope(ApiRequest request) =>
      request.headers['x-workspace-id'] ?? 'workspace_test';

  List<String> calls() =>
      [for (final r in requests) '${r.method} ${r.uri.path}'];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    final mine = devices.putIfAbsent(_scope(request), () => []);
    if (path == '/api/v1/push/status') {
      return ApiResponse(statusCode: 200, body: {
        'available': serverOn,
        'provider': 'fcm',
        'project_id': serverOn ? 'anum-example' : null,
        'message': serverOn
            ? 'Phone push is on.'
            : 'Phone push is off on this server: the operator has not set ANUM_FCM_SERVICE_ACCOUNT_JSON.',
      });
    }
    if (path == '/api/v1/me/devices' && request.method == 'GET') {
      if (failDevices) {
        return const ApiResponse(
            statusCode: 503, body: {'detail': 'Service unavailable'});
      }
      return ApiResponse(statusCode: 200, body: {'data': mine});
    }
    if (path == '/api/v1/me/devices' && request.method == 'POST') {
      final token = request.body!['token'] as String;
      final hint = token.substring(token.length - 4);
      final existing = mine.where((d) => d['token_hint'] == hint).firstOrNull;
      if (existing != null) {
        return ApiResponse(statusCode: 200, body: existing);
      }
      final device = <String, Object?>{
        'id': 'device_${++_next}',
        'platform': request.body!['platform'],
        'label': request.body!['label'],
        'token_hint': hint,
        'created_at': '2026-10-11T09:00:00Z',
        'updated_at': '2026-10-11T09:00:00Z',
        'last_sent_at': null,
      };
      mine.add(device);
      return ApiResponse(statusCode: 201, body: device);
    }
    if (path.startsWith('/api/v1/me/devices/') && request.method == 'DELETE') {
      final id = Uri.decodeComponent(path.split('/').last);
      final before = mine.length;
      mine.removeWhere((d) => d['id'] == id);
      return before == mine.length
          ? const ApiResponse(
              statusCode: 404, body: {'detail': 'Device not found'})
          : const ApiResponse(statusCode: 204);
    }
    if (path == '/api/v1/auth/local/session' && request.method == 'DELETE') {
      return const ApiResponse(statusCode: 204);
    }
    if (path == '/api/v1/auth/local/workspace/switch') {
      return ApiResponse(
          statusCode: 200,
          body: session(workspaceId: request.body!['workspace_id'] as String));
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

JsonMap session({String workspaceId = 'workspace_test'}) => {
      'access_token': 'anum_local_example',
      'token_type': 'bearer',
      'expires_at': DateTime.now()
          .toUtc()
          .add(const Duration(hours: 1))
          .toIso8601String(),
      'context': {
        'tenant_id': 'tenant_test',
        'workspace_id': workspaceId,
        'user_id': 'user_test',
        'roles': ['owner'],
      },
    };

Future<(AnumApiClient, MemorySessionStore)> signedInApi(
    ApiTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson(session()));
  return (
    AnumApiClient(
        baseUri: Uri.parse('http://127.0.0.1:8000'),
        transport: transport,
        sessions: sessions),
    sessions
  );
}
