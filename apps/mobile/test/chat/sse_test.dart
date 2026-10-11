// The streamed-reply plumbing (docs/conversations.md): the incremental
// server-sent-events parser and the HTTP client the chat and voice use.
import 'dart:async';
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/src/infrastructure/http_event_stream.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

String _frame(String event, Object data) =>
    'event: $event\ndata: ${jsonEncode(data)}\n\n';

/// An http client whose response body the test feeds piece by piece.
class _StreamingClient extends http.BaseClient {
  _StreamingClient({this.status = 200, this.errorBody});
  final int status;
  final String? errorBody;
  final body = StreamController<List<int>>();
  http.BaseRequest? request;
  String? sentBody;
  bool closed = false;
  bool bodyCancelled = false;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    this.request = request;
    sentBody = request is http.Request ? request.body : null;
    body.onCancel = () => bodyCancelled = true;
    if (errorBody != null) {
      return http.StreamedResponse(
          Stream.value(utf8.encode(errorBody!)), status);
    }
    return http.StreamedResponse(body.stream, status);
  }

  void feed(String text) => body.add(utf8.encode(text));

  @override
  void close() => closed = true;
}

Future<MemorySessionStore> _signedIn({bool expired = false}) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
    accessToken: 'example',
    tokenType: 'bearer',
    expiresAt: DateTime.now()
        .toUtc()
        .add(expired ? const Duration(minutes: -1) : const Duration(hours: 1)),
    context: const TenantContext(
        tenantId: 'tenant_test',
        workspaceId: 'workspace_test',
        userId: 'user_test',
        roles: ['member']),
  ));
  return sessions;
}

void main() {
  group('SseParser', () {
    test('delivers events in order across any chunking', () {
      final text = _frame('turn', {
            'user_turn': {'id': 'turn_1'}
          }) +
          _frame('delta', {'text': 'Hel'}) +
          _frame('delta', {'text': 'lo\nthere'}) +
          _frame('done', {'assistant_turn': null});
      for (final size in [1, 2, 3, 7, 16, text.length]) {
        final parser = SseParser();
        final events = <SseEvent>[];
        for (var i = 0; i < text.length; i += size) {
          events.addAll(parser.push(text.substring(
              i, i + size > text.length ? text.length : i + size)));
        }
        events.addAll(parser.end());
        expect(events.map((e) => e.event), ['turn', 'delta', 'delta', 'done'],
            reason: 'chunk size $size');
        expect(events[2].fields['text'], 'lo\nthere');
        expect(events[3].fields, {'assistant_turn': null});
      }
    });

    test('accepts CRLF and CR line endings, also split between chunks', () {
      final parser = SseParser();
      final events = [
        ...parser.push('event: delta\r'),
        ...parser.push('\ndata: {"text":"a"}\r\n\r'),
        ...parser.push('\nevent: delta\rdata: {"text":"b"}\r\r'),
        ...parser.end(),
      ];
      expect(events.map((e) => e.fields['text']), ['a', 'b']);
    });

    test('ignores comments and frames without data; joins data lines', () {
      final parser = SseParser();
      final events = parser.push(': keep-alive\n\n'
          'event: ping\n\n'
          'event: note\ndata: first\ndata: second\n\n'
          'data:no-space\n\n');
      expect(events, hasLength(2));
      expect(events[0].event, 'note');
      expect(events[0].data, 'first\nsecond');
      expect(events[0].fields, isEmpty);
      expect(events[1].event, 'message');
      expect(events[1].data, 'no-space');
    });

    test('end() flushes a last frame without its blank line', () {
      final parser = SseParser();
      expect(parser.push('event: done\ndata: {"ok":true}'), isEmpty);
      final events = parser.end();
      expect(events.single.event, 'done');
      expect(events.single.fields['ok'], isTrue);
      expect(parser.end(), isEmpty);
    });

    test('decodeSse keeps multi-byte characters split across chunks', () async {
      final bytes = utf8.encode(_frame('delta', {'text': 'مرحبا ✓'}));
      final chunks = [
        for (var i = 0; i < bytes.length; i += 3)
          bytes.sublist(i, i + 3 > bytes.length ? bytes.length : i + 3)
      ];
      final events = await decodeSse(Stream.fromIterable(chunks)).toList();
      expect(events.single.fields['text'], 'مرحبا ✓');
    });
  });

  group('HttpEventStreamClient', () {
    test(
        'posts JSON with the session headers and streams events as they arrive',
        () async {
      final client = _StreamingClient();
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test/'),
        sessions: await _signedIn(),
        clientFactory: () => client,
      );
      final received = <SseEvent>[];
      final done = Completer<void>();
      events.post('/api/v1/conversations/conv_1/ask', {'content': 'Hi'}).listen(
          received.add,
          onDone: done.complete);
      await pumpEventQueue();

      final request = client.request!;
      expect(request.method, 'POST');
      expect(request.url.toString(),
          'https://anum.test/api/v1/conversations/conv_1/ask');
      expect(request.headers['authorization'], 'Bearer example');
      expect(request.headers['x-workspace-id'], 'workspace_test');
      expect(request.headers['accept'], 'text/event-stream');
      expect(jsonDecode(client.sentBody!), {'content': 'Hi'});

      client.feed(_frame('delta', {'text': 'Hel'}));
      await pumpEventQueue();
      expect(received.map((e) => e.fields['text']), ['Hel']);
      client.feed(_frame('delta', {'text': 'lo'}).substring(0, 10));
      await pumpEventQueue();
      expect(received, hasLength(1));
      client.feed(_frame('delta', {'text': 'lo'}).substring(10));
      await client.body.close();
      await done.future;
      expect(received.map((e) => e.fields['text']), ['Hel', 'lo']);
      expect(client.closed, isTrue);
    });

    test('cancelling the subscription closes the connection', () async {
      final client = _StreamingClient();
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test'),
        sessions: await _signedIn(),
        clientFactory: () => client,
      );
      final subscription = events.post('/ask', const {}).listen((_) {});
      await pumpEventQueue();
      client.feed(_frame('delta', {'text': 'partial'}));
      await pumpEventQueue();
      await subscription.cancel();
      expect(client.bodyCancelled, isTrue);
      expect(client.closed, isTrue);
    });

    test('a refusal is the API error, with its code', () async {
      final client = _StreamingClient(
          status: 402,
          errorBody: jsonEncode({
            'error': {
              'code': 'model_budget_exceeded',
              'message': 'The monthly model budget is used up until 1 Nov.'
            }
          }));
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test'),
        sessions: await _signedIn(),
        clientFactory: () => client,
      );
      await expectLater(
          events.post('/ask', const {}).toList(),
          throwsA(isA<ApiException>()
              .having((e) => e.statusCode, 'statusCode', 402)
              .having((e) => e.isBudgetExceeded, 'isBudgetExceeded', isTrue)
              .having((e) => e.message, 'message', contains('1 Nov'))));
      expect(client.closed, isTrue);
    });

    test('a silent stream times out instead of hanging', () async {
      final client = _StreamingClient();
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test'),
        sessions: await _signedIn(),
        clientFactory: () => client,
        idleTimeout: const Duration(milliseconds: 50),
      );
      await expectLater(events.post('/ask', const {}).toList(),
          throwsA(isA<EventStreamTimeoutException>()));
      expect(client.closed, isTrue);
    });

    test('an unreachable server reads as offline (status 0)', () async {
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test'),
        sessions: await _signedIn(),
        clientFactory: () => _FailingClient(),
      );
      await expectLater(
          events.post('/ask', const {}).toList(),
          throwsA(isA<ApiException>()
              .having((e) => e.statusCode, 'statusCode', 0)));
    });

    test('an expired session never reaches the network', () async {
      final sessions = await _signedIn(expired: true);
      final events = HttpEventStreamClient(
        baseUri: Uri.parse('https://anum.test'),
        sessions: sessions,
        clientFactory: () => throw StateError('no request expected'),
      );
      await expectLater(
          events.post('/ask', const {}).toList(),
          throwsA(isA<ApiException>()
              .having((e) => e.statusCode, 'statusCode', 401)));
      expect(await sessions.read(), isNull);
    });
  });
}

class _FailingClient extends http.BaseClient {
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      Future.error(http.ClientException('Connection refused', request.url));
}
