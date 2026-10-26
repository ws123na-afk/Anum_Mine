import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import '../../data/sse.dart';
import 'command_models.dart';

/// What the Command screens need beyond JSON calls: the workspace event
/// stream, downloads of report and run-log files, attaching a picked file and
/// saving a file on the device. Behind an interface so tests use a fake.
abstract interface class CommandTransfer {
  /// `GET /api/v1/events/stream` (server-sent events) from [lastEventId].
  Stream<SseEvent> events({String? lastEventId});

  /// The bytes behind an API path such as `/api/v1/tasks/{id}/export?...`.
  Future<Uint8List> download(String apiPath);

  /// Lets the person pick a photo or file; null when they cancel.
  Future<String?> pickFile();

  /// Uploads [path] to workspace files and returns it as an attachment.
  Future<CommandAttachment> attach(String path);

  /// The system save dialog; false when the person cancelled.
  Future<bool> save(String fileName, Uint8List bytes);
}

class HttpCommandTransfer implements CommandTransfer {
  HttpCommandTransfer({
    required Uri baseUri,
    required this.sessions,
    required this.upload,
    http.Client Function()? clientFactory,
  })  : baseUri = baseUri.replace(
            path:
                baseUri.path.endsWith('/') ? baseUri.path : '${baseUri.path}/'),
        _newClient = clientFactory ?? http.Client.new;

  final Uri baseUri;
  final SessionStore sessions;

  /// `POST /api/v1/files` for a local path (the app's file transfer).
  final Future<JsonMap> Function(String path) upload;
  final http.Client Function() _newClient;

  Future<Map<String, String>> _headers() async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    return sessionHeaders(session);
  }

  Uri _resolve(String path) =>
      baseUri.resolve(path.replaceFirst(RegExp(r'^/'), ''));

  @override
  Stream<SseEvent> events({String? lastEventId}) {
    late final StreamController<SseEvent> output;
    http.Client? client;
    StreamSubscription<SseEvent>? subscription;

    Future<void> start() async {
      try {
        final current = client = _newClient();
        final request = http.Request('GET', _resolve('/api/v1/events/stream'))
          ..headers.addAll({
            ...await _headers(),
            'accept': 'text/event-stream',
            if (lastEventId != null) 'last-event-id': lastEventId,
          });
        final response =
            await current.send(request).timeout(const Duration(seconds: 30));
        if (response.statusCode < 200 || response.statusCode >= 300) {
          throw ApiException(response.statusCode, 'The live stream refused');
        }
        subscription = decodeSse(response.stream)
            .timeout(const Duration(seconds: 90))
            .listen(output.add, onError: (Object error, StackTrace stack) {
          if (!output.isClosed) output.addError(error, stack);
          client?.close();
          if (!output.isClosed) output.close();
        }, onDone: () {
          client?.close();
          if (!output.isClosed) output.close();
        }, cancelOnError: true);
      } on Object catch (error, stack) {
        client?.close();
        if (!output.isClosed) {
          output.addError(error, stack);
          await output.close();
        }
      }
    }

    output = StreamController<SseEvent>(
      onListen: () => unawaited(start()),
      onCancel: () async {
        client?.close();
        await subscription?.cancel();
      },
    );
    return output.stream;
  }

  @override
  Future<Uint8List> download(String apiPath) async {
    final client = _newClient();
    try {
      final response =
          await client.get(_resolve(apiPath), headers: await _headers());
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(response.statusCode, 'The download failed');
      }
      return response.bodyBytes;
    } finally {
      client.close();
    }
  }

  @override
  Future<String?> pickFile() async {
    final picked = await FilePicker.pickFile();
    return picked?.path;
  }

  @override
  Future<CommandAttachment> attach(String path) async {
    final body = JsonReader.of(await upload(path));
    return CommandAttachment(
        fileId: body.string('id'), name: body.string('name'));
  }

  @override
  Future<bool> save(String fileName, Uint8List bytes) async =>
      await FilePicker.saveFile(
          dialogTitle: fileName, fileName: fileName, bytes: bytes) !=
      null;
}
