import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// One request the app sent to the fake API.
class FakeRequest {
  FakeRequest(this.method, this.uri, this.headers, this.bytes, this.params);

  final String method;
  final Uri uri;
  final Map<String, String> headers;
  final List<int> bytes;

  /// `{name}` segments of the matched route pattern.
  final Map<String, String> params;

  String get path => uri.path;
  Map<String, String> get query => uri.queryParameters;
  Map<String, List<String>> get queryAll => uri.queryParametersAll;

  /// The JSON body, or null for an empty or non-JSON body.
  Object? get json {
    if (bytes.isEmpty) return null;
    try {
      return jsonDecode(utf8.decode(bytes));
    } on FormatException {
      return null;
    }
  }

  Map<String, Object?> get body =>
      (json as Map<String, Object?>?) ?? const <String, Object?>{};

  @override
  String toString() =>
      '$method ${uri.path}${uri.hasQuery ? '?${uri.query}' : ''}';
}

/// What a route answers: JSON, an empty body, or a server-sent event stream.
class FakeResponse {
  const FakeResponse.json(this.body, {this.status = 200})
      : events = null,
        raw = null;
  const FakeResponse.empty({this.status = 204})
      : body = null,
        events = null,
        raw = null;

  /// `text/event-stream` frames, written as the API's SSE encoder writes them.
  const FakeResponse.events(this.events, {this.status = 200})
      : body = null,
        raw = null;

  /// A response body that stays open until the client goes away, as a quiet
  /// live stream does (team call signalling, task events).
  FakeResponse.openStream({this.status = 200})
      : body = null,
        events = null,
        raw = StreamController<List<int>>();

  /// The API's error envelope (`{"error": {"code", "message"}}`).
  FakeResponse.error(int status, String code, String message)
      : this.json({
          'error': {'code': code, 'message': message}
        }, status: status);

  final int status;
  final Object? body;
  final List<(String, Object)>? events;
  final StreamController<List<int>>? raw;
}

typedef FakeHandler = FutureOr<FakeResponse> Function(FakeRequest request);

class _Route {
  _Route(this.method, this.pattern, this.handler)
      : _names = [
          for (final m in RegExp(r'\{(\w+)\}').allMatches(pattern)) m.group(1)!
        ],
        _regex = RegExp('^${pattern.replaceAllMapped(
          RegExp(r'\{(\w+)\}|([.?+*()\[\]^$|\\])'),
          (m) => m.group(1) != null ? '([^/]+)' : '\\${m.group(2)}',
        )}\$');

  final String method;
  final String pattern;
  final FakeHandler handler;
  final List<String> _names;
  final RegExp _regex;

  Map<String, String>? match(String method, String path) {
    if (method != this.method) return null;
    final m = _regex.firstMatch(path);
    if (m == null) return null;
    return {
      for (var i = 0; i < _names.length; i++)
        _names[i]: Uri.decodeComponent(m.group(i + 1)!)
    };
  }
}

/// A fake ANUM API behind a `package:http` client. The app's real transport,
/// stream reader and upload code run against it; each route answers with
/// the API's own response shape (services/api, mirrored from the web e2e
/// fixtures in apps/web/e2e/support).
///
/// Routes registered later win, so a journey can override a default. A
/// request no route answers is recorded in [unmatched] and gets the API's
/// 404 envelope, which the screens must handle like any refusal.
class FakeAnumApi {
  final _routes = <_Route>[];

  /// Every request, in order.
  final log = <FakeRequest>[];

  /// Requests no route answered, as `METHOD /path`.
  final unmatched = <String>[];

  final _open = <StreamController<List<int>>>[];

  void on(String method, String pattern, FakeHandler handler) =>
      _routes.add(_Route(method, pattern, handler));

  void get(String pattern, FakeHandler handler) => on('GET', pattern, handler);
  void post(String pattern, FakeHandler handler) =>
      on('POST', pattern, handler);
  void put(String pattern, FakeHandler handler) => on('PUT', pattern, handler);
  void patch(String pattern, FakeHandler handler) =>
      on('PATCH', pattern, handler);
  void delete(String pattern, FakeHandler handler) =>
      on('DELETE', pattern, handler);

  /// Requests to [path] (exact) with [method].
  List<FakeRequest> sent(String method, String path) => [
        for (final r in log)
          if (r.method == method && r.path == path) r
      ];

  late final http.Client client = MockClient.streaming(_handle);

  /// Closes the live streams still open, so nothing outlives the test.
  Future<void> close() async {
    for (final c in _open) {
      if (!c.isClosed) await c.close();
    }
  }

  Future<http.StreamedResponse> _handle(
      http.BaseRequest request, http.ByteStream bodyStream) async {
    final bytes = await bodyStream.toBytes();
    final path = request.url.path;
    for (final route in _routes.reversed) {
      final params = route.match(request.method, path);
      if (params == null) continue;
      final fake = FakeRequest(
          request.method, request.url, request.headers, bytes, params);
      log.add(fake);
      return _encode(await route.handler(fake));
    }
    final fake = FakeRequest(
        request.method, request.url, request.headers, bytes, const {});
    log.add(fake);
    unmatched.add('${request.method} $path');
    return _encode(FakeResponse.error(
        404, 'not_found', 'No fixture for ${request.method} $path'));
  }

  http.StreamedResponse _encode(FakeResponse response) {
    final events = response.events;
    final raw = response.raw;
    if (raw != null) {
      _open.add(raw);
      return http.StreamedResponse(raw.stream, response.status,
          headers: {'content-type': 'text/event-stream'});
    }
    if (events != null) {
      final text = StringBuffer();
      for (final (name, data) in events) {
        text
          ..write('event: $name\n')
          ..write('data: ${data is String ? data : jsonEncode(data)}\n\n');
      }
      return http.StreamedResponse(
          Stream.value(utf8.encode(text.toString())), response.status,
          headers: {'content-type': 'text/event-stream'});
    }
    final body = response.body;
    if (body == null) {
      return http.StreamedResponse(const Stream.empty(), response.status);
    }
    return http.StreamedResponse(
        Stream.value(utf8.encode(jsonEncode(body))), response.status,
        headers: {'content-type': 'application/json'});
  }
}
