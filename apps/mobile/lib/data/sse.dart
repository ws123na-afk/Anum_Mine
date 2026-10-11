/// Server-sent events for the streamed assistant replies
/// (docs/conversations.md, docs/voice.md): an incremental parser, a byte
/// stream decoder and the client interface the chat and voice use.
library;

import 'dart:async';
import 'dart:convert';

import 'api_models.dart';

/// One server-sent event. [data] is the decoded JSON when the data is JSON,
/// else the raw text.
class SseEvent {
  const SseEvent(this.event, this.data);

  final String event;
  final Object? data;

  /// The data as a JSON object, or an empty map when it is not one.
  JsonMap get fields {
    final value = data;
    if (value is Map) {
      try {
        return jsonObject(value);
      } on FormatException {
        return const {};
      }
    }
    return const {};
  }

  @override
  String toString() => 'SseEvent($event, $data)';
}

/// Incremental parser: feed decoded text in any chunking and complete events
/// come back in order. Frames end with a blank line; `data:` lines are
/// joined with newlines; comments (`:`) and frames without data are ignored;
/// `\r\n` and `\r` line endings are accepted, also when split across chunks.
class SseParser {
  String _buffer = '';
  bool _carriageReturn = false;

  /// Adds [text] and returns the events it completed.
  List<SseEvent> push(String text) {
    var chunk = text;
    if (_carriageReturn) {
      chunk = '\r$chunk';
      _carriageReturn = false;
    }
    // A trailing CR may be the first half of CRLF: wait for the next chunk.
    if (chunk.endsWith('\r')) {
      _carriageReturn = true;
      chunk = chunk.substring(0, chunk.length - 1);
    }
    _buffer += chunk.replaceAll(RegExp(r'\r\n?'), '\n');
    final events = <SseEvent>[];
    var index = _buffer.indexOf('\n\n');
    while (index >= 0) {
      final event = _parseFrame(_buffer.substring(0, index));
      if (event != null) events.add(event);
      _buffer = _buffer.substring(index + 2);
      index = _buffer.indexOf('\n\n');
    }
    return events;
  }

  /// The stream ended: returns the last frame when it had no blank line.
  List<SseEvent> end() {
    if (_carriageReturn) {
      _carriageReturn = false;
      _buffer += '\n';
    }
    final rest = _buffer;
    _buffer = '';
    if (rest.trim().isEmpty) return const [];
    final event = _parseFrame(rest);
    return event == null ? const [] : [event];
  }

  static SseEvent? _parseFrame(String frame) {
    var event = 'message';
    final data = <String>[];
    for (final line in frame.split('\n')) {
      if (line.isEmpty || line.startsWith(':')) continue;
      final colon = line.indexOf(':');
      final field = colon < 0 ? line : line.substring(0, colon);
      var value = colon < 0 ? '' : line.substring(colon + 1);
      if (value.startsWith(' ')) value = value.substring(1);
      if (field == 'event') {
        event = value;
      } else if (field == 'data') {
        data.add(value);
      }
    }
    if (data.isEmpty) return null;
    final text = data.join('\n');
    Object? parsed = text;
    try {
      parsed = jsonDecode(text);
    } on FormatException {
      // Plain text data.
    }
    return SseEvent(event, parsed);
  }
}

/// Decodes a UTF-8 byte stream of server-sent events. Cancelling the
/// subscription cancels [bytes] at once (which closes the HTTP response).
/// Not an `async*` generator on purpose: one parked at `await for` only sees
/// a cancel when the next chunk arrives, so Stop would hang.
Stream<SseEvent> decodeSse(Stream<List<int>> bytes) {
  final parser = SseParser();
  return utf8.decoder.bind(bytes).transform(
        StreamTransformer<String, SseEvent>.fromHandlers(
          handleData: (text, sink) {
            for (final event in parser.push(text)) {
              sink.add(event);
            }
          },
          handleDone: (sink) {
            for (final event in parser.end()) {
              sink.add(event);
            }
            sink.close();
          },
        ),
      );
}

/// No answer from the server in time: it did not start answering, or went
/// silent in the middle of a reply.
class EventStreamTimeoutException implements Exception {
  const EventStreamTimeoutException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// POSTs JSON and delivers the server-sent events of the answer as they
/// arrive, with the session's bearer token and workspace header.
///
/// - A non-2xx answer is an [ApiException]-style error on the stream (for
///   example `402 model_budget_exceeded` or `403`).
/// - Cancelling the subscription closes the connection, which the API turns
///   into stopping the model (the **Stop** button).
/// - No response headers within the connect timeout, or no data within the
///   idle timeout, fails with [EventStreamTimeoutException].
abstract interface class EventStreamClient {
  Stream<SseEvent> post(String path, JsonMap body);
}
