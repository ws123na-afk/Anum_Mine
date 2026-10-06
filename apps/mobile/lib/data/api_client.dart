import 'api_models.dart';
import 'session_store.dart';

class ApiRequest {
  const ApiRequest({
    required this.method,
    required this.uri,
    this.headers = const {},
    this.body,
  });

  final String method;
  final Uri uri;
  final Map<String, String> headers;
  final JsonMap? body;
}

class ApiResponse {
  const ApiResponse({required this.statusCode, this.body});

  final int statusCode;
  final JsonMap? body;
}

abstract interface class ApiTransport {
  Future<ApiResponse> send(ApiRequest request);
}

class ApiException implements Exception {
  const ApiException(this.statusCode, this.message, {this.code});

  /// Reads the API's error envelope (`{"error": {"code", "message"}}`) or
  /// FastAPI's `{"detail": ...}`, keeping the stable code when there is one.
  factory ApiException.fromResponse(ApiResponse response) {
    final body = response.body;
    final envelope = body?['error'];
    final detail = body?['detail'];
    final message = envelope is Map && envelope['message'] is String
        ? envelope['message'] as String
        : detail is String
            ? detail
            : 'Request failed';
    final code = envelope is Map && envelope['code'] is String
        ? envelope['code'] as String
        : null;
    return ApiException(response.statusCode, message, code: code);
  }

  final int statusCode;
  final String message;

  /// The envelope's stable error code, e.g. `model_budget_exceeded`.
  final String? code;

  /// HTTP 402: the tenant's or workspace's monthly model budget is used up.
  bool get isBudgetExceeded =>
      statusCode == 402 || code == 'model_budget_exceeded';

  /// HTTP 403: the caller's role does not grant this.
  bool get isPermissionDenied => statusCode == 403;

  @override
  String toString() => 'ApiException($statusCode, $message)';
}

class AnumApiClient {
  AnumApiClient({
    required Uri baseUri,
    required this.transport,
    required this.sessions,
  }) : baseUri = _normalizeBaseUri(baseUri);

  final Uri baseUri;
  final ApiTransport transport;
  final SessionStore sessions;

  static Uri _normalizeBaseUri(Uri value) {
    if (!value.hasScheme || value.host.isEmpty) {
      throw ArgumentError.value(value, 'baseUri', 'must be absolute');
    }
    return value.replace(
        path: value.path.endsWith('/') ? value.path : '${value.path}/');
  }

  Future<JsonMap> request(
    String method,
    String path, {
    JsonMap? body,
    bool authenticated = true,
    Map<String, String> headers = const {},
  }) async {
    final sent = <String, String>{'content-type': 'application/json'};
    if (authenticated) {
      final session = await sessions.read();
      if (session == null || session.isExpired) {
        if (session?.isExpired ?? false) await sessions.clear();
        throw const ApiException(401, 'Authentication required');
      }
      sent.addAll(sessionHeaders(session));
    }
    // Explicit headers win, e.g. the workspace an invitation is accepted into.
    sent.addAll(headers);
    final response = await transport.send(ApiRequest(
      method: method,
      uri: baseUri.resolve(path.replaceFirst(RegExp(r'^/'), '')),
      headers: sent,
      body: body,
    ));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException.fromResponse(response);
    }
    return response.body ?? const {};
  }
}

/// Identity headers for an authenticated call: the bearer token and, when the
/// session names one, the workspace the API should resolve the membership in.
/// In `oidc` mode the API takes the tenant from the token and the workspace
/// from `x-workspace-id` (docs/identity.md); local sessions carry their own
/// context and the header simply repeats it.
Map<String, String> sessionHeaders(LocalSession session) => {
      'authorization': 'Bearer ${session.accessToken}',
      if (session.context.workspaceId.isNotEmpty)
        'x-workspace-id': session.context.workspaceId,
    };
