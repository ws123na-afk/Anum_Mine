import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import '../../data/sse.dart';

/// [EventStreamClient] over `package:http`.
///
/// Each request gets its own [http.Client], closed when the stream ends or is
/// cancelled: closing it aborts the connection on every platform and every
/// `http` 1.x version, which is what makes **Stop** stop the model.
class HttpEventStreamClient implements EventStreamClient {
  HttpEventStreamClient({
    required Uri baseUri,
    required this.sessions,
    http.Client Function()? clientFactory,
    this.connectTimeout = const Duration(seconds: 30),
    this.idleTimeout = const Duration(seconds: 90),
  })  : baseUri = baseUri.replace(
            path:
                baseUri.path.endsWith('/') ? baseUri.path : '${baseUri.path}/'),
        _newClient = clientFactory ?? http.Client.new;

  final Uri baseUri;
  final SessionStore sessions;
  final http.Client Function() _newClient;

  /// Until the response headers arrive (the API checks the budget and stores
  /// the question first).
  final Duration connectTimeout;

  /// Longest silence while streaming. The API gives up on a model that sends
  /// no first token within 20 s and ends every stream after 120 s.
  final Duration idleTimeout;

  @override
  Stream<SseEvent> post(String path, JsonMap body) {
    late final StreamController<SseEvent> output;
    http.Client? client;
    StreamSubscription<SseEvent>? subscription;
    var cancelled = false;

    void closeClient() {
      client?.close();
      client = null;
    }

    Future<void> fail(Object error, StackTrace stack) async {
      final active = subscription;
      subscription = null;
      closeClient();
      await active?.cancel();
      if (cancelled || output.isClosed) return;
      output.addError(_translate(error), stack);
      await output.close();
    }

    Future<void> start() async {
      try {
        final session = await sessions.read();
        if (session == null || session.isExpired) {
          if (session?.isExpired ?? false) await sessions.clear();
          throw const ApiException(401, 'Authentication required');
        }
        if (cancelled) return;
        final current = client = _newClient();
        final request = http.Request(
            'POST', baseUri.resolve(path.replaceFirst(RegExp(r'^/'), '')))
          ..headers.addAll({
            ...sessionHeaders(session),
            'content-type': 'application/json',
            'accept': 'text/event-stream',
          })
          ..body = jsonEncode(body);
        final response = await current.send(request).timeout(connectTimeout,
            onTimeout: () => throw const EventStreamTimeoutException(
                'The server did not answer in time. Check the connection and try again.'));
        if (cancelled) return;
        if (response.statusCode < 200 || response.statusCode >= 300) {
          final text =
              await response.stream.bytesToString().timeout(connectTimeout);
          throw ApiException.fromResponse(ApiResponse(
              statusCode: response.statusCode, body: _decodeBody(text)));
        }
        subscription = decodeSse(response.stream).timeout(idleTimeout).listen(
              (event) {
                if (!cancelled) output.add(event);
              },
              onError: (Object error, StackTrace stack) =>
                  unawaited(fail(error, stack)),
              onDone: () {
                subscription = null;
                closeClient();
                if (!cancelled && !output.isClosed) output.close();
              },
              cancelOnError: false,
            );
      } on Object catch (error, stack) {
        await fail(error, stack);
      }
    }

    output = StreamController<SseEvent>(
      onListen: () => unawaited(start()),
      onPause: () => subscription?.pause(),
      onResume: () => subscription?.resume(),
      onCancel: () async {
        cancelled = true;
        final active = subscription;
        subscription = null;
        // Closing the client aborts the connection even mid-chunk.
        closeClient();
        await active?.cancel();
      },
    );
    return output.stream;
  }

  static Object _translate(Object error) => switch (error) {
        TimeoutException() => const EventStreamTimeoutException(
            'The answer stopped arriving. Check the connection and try again.'),
        http.ClientException() => const ApiException(0,
            'The server could not be reached. Check the connection and try again.'),
        _ => error,
      };

  static JsonMap? _decodeBody(String text) {
    if (text.isEmpty) return null;
    try {
      final decoded = jsonDecode(text);
      return decoded is Map<String, Object?> ? decoded : {'data': decoded};
    } on FormatException {
      return {'detail': text};
    }
  }
}
