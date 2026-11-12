import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'api_client.dart';
import 'api_models.dart';
import 'session_store.dart';
import 'transfer_guard.dart';

/// The most a download the phone builds in memory may hold (a zip of files,
/// a CSV): 256 MiB. The API allows a 1 GiB zip; a phone holds the bytes until
/// the system save dialog has written them, so a larger selection is refused
/// before anything is sent, and an answer that grows past it is stopped
/// (docs/mobile.md#file-downloads).
const maxPhoneDownloadBytes = 256 * 1024 * 1024;

/// Hands finished bytes to the system save dialog under [fileName]; answers
/// whether the person saved them (false when they closed the dialog). The
/// dialog writes where the person chooses: the app never writes a copy of
/// its own, so no readable file is left behind in shared storage or a cache.
typedef SaveBytes = Future<bool> Function(
    String fileName, Uint8List bytes, String dialogTitle);

/// A file the API sends as bytes (CSV exports, the files zip), saved through
/// the system save dialog. Every call has the transfer time limit and the
/// readable network errors of [guardTransfer]; an API refusal keeps the
/// API's sentence.
abstract interface class ApiDownloads {
  /// Fetches [path] with the session's headers (with [body] as JSON when
  /// given) and offers the bytes to the save dialog. [fileName] is used when
  /// the answer names no file. True when saved, false when the dialog was
  /// closed.
  Future<bool> save(String method, String path,
      {JsonMap? body, required String fileName, required String dialogTitle});

  /// Fetches [path] with the session's headers and answers its bytes, held
  /// in memory only (the action browser's live picture); nothing is saved.
  Future<Uint8List> fetch(String path, {int maxBytes = 8 * 1024 * 1024});
}

class HttpApiDownloads implements ApiDownloads {
  HttpApiDownloads({
    required this.baseUri,
    required this.sessions,
    required this.saveBytes,
    http.Client? client,
    this.timeout = transferTimeout,
    this.maxBytes = maxPhoneDownloadBytes,
  }) : _client = client ?? http.Client();

  final Uri baseUri;
  final SessionStore sessions;
  final SaveBytes saveBytes;
  final http.Client _client;
  final Duration timeout;
  final int maxBytes;

  @override
  Future<bool> save(String method, String path,
      {JsonMap? body,
      required String fileName,
      required String dialogTitle}) async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    final (bytes, named) = await guardTransfer(() async {
      final request = http.Request(
          method, baseUri.resolve(path.replaceFirst(RegExp(r'^/'), '')))
        ..headers.addAll(sessionHeaders(session))
        // The bearer token goes to the configured API only: a redirect is
        // refused as an answer, never followed with the header.
        ..followRedirects = false;
      if (body != null) {
        request.headers['content-type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      final response = await _client.send(request);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        final text = await response.stream.bytesToString();
        throw ApiException.fromResponse(
            ApiResponse(statusCode: response.statusCode, body: _json(text)));
      }
      final length = response.contentLength;
      if (length != null && length > maxBytes) {
        throw ApiException(413, tooLargeMessage(maxBytes));
      }
      final builder = BytesBuilder(copy: false);
      await for (final chunk in response.stream) {
        builder.add(chunk);
        if (builder.length > maxBytes) {
          throw ApiException(413, tooLargeMessage(maxBytes));
        }
      }
      return (
        builder.takeBytes(),
        attachmentName(response.headers['content-disposition'])
      );
    }, timeout: timeout);
    return saveBytes(named ?? safeFileName(fileName), bytes, dialogTitle);
  }

  @override
  Future<Uint8List> fetch(String path, {int maxBytes = 8 * 1024 * 1024}) async {
    final session = await sessions.read();
    if (session == null || session.isExpired) {
      throw const ApiException(401, 'Authentication required');
    }
    return guardTransfer(() async {
      final request = http.Request(
          'GET', baseUri.resolve(path.replaceFirst(RegExp(r'^/'), '')))
        ..headers.addAll(sessionHeaders(session))
        ..followRedirects = false;
      final response = await _client.send(request);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        final text = await response.stream.bytesToString();
        throw ApiException.fromResponse(
            ApiResponse(statusCode: response.statusCode, body: _json(text)));
      }
      final builder = BytesBuilder(copy: false);
      await for (final chunk in response.stream) {
        builder.add(chunk);
        if (builder.length > maxBytes) {
          throw ApiException(413, tooLargeMessage(maxBytes));
        }
      }
      return builder.takeBytes();
    }, timeout: timeout);
  }

  static JsonMap? _json(String text) {
    try {
      final decoded = jsonDecode(text);
      return decoded is Map<String, dynamic> ? decoded : null;
    } on FormatException {
      return null;
    }
  }
}

/// The refusal when a download would be larger than the phone holds.
String tooLargeMessage(int maxBytes) =>
    'This download is larger than the ${maxBytes ~/ (1024 * 1024)} MB a phone can save at once. '
    'Choose fewer files, or download them from the web app.';

/// The file name in a `Content-Disposition: attachment; filename="..."`
/// header, made safe to offer to the save dialog; null when there is none.
String? attachmentName(String? header) {
  if (header == null) return null;
  final match =
      RegExp(r'filename="?([^";]+)"?', caseSensitive: false).firstMatch(header);
  final name = match?.group(1);
  if (name == null) return null;
  final safe = safeFileName(name);
  return safe.isEmpty ? null : safe;
}

/// A file name with no folder part, no control characters and nothing that
/// could step out of the folder the person picks (`..`, `/`, `\`), at most
/// 120 characters. Falls back to `download` when nothing is left.
String safeFileName(String name) {
  var value = name.split(RegExp(r'[/\\]')).last;
  value = value.replaceAll(RegExp(r'[\x00-\x1f\x7f<>:"|?*]'), '').trim();
  while (value.startsWith('.')) {
    value = value.substring(1);
  }
  if (value.length > 120) value = value.substring(value.length - 120);
  return value.isEmpty ? 'download' : value;
}
