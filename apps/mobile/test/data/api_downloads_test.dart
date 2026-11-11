// CSV exports and the files zip on the phone (docs/mobile.md#file-downloads):
// the session headers, the API's refusals, the size cap, the file name from
// the answer made safe, and the bytes handed only to the save dialog.
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_downloads.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Future<MemorySessionStore> _sessions() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 't', workspaceId: 'w', userId: 'u', roles: ['owner'])));
  return sessions;
}

void main() {
  test('file names from the answer are made safe', () {
    expect(
        attachmentName('attachment; filename="contacts.csv"'), 'contacts.csv');
    expect(attachmentName('attachment; filename=files-2026-11-11.zip'),
        'files-2026-11-11.zip');
    expect(attachmentName('attachment; filename="../../etc/passwd"'), 'passwd');
    expect(attachmentName('attachment; filename="..\\\\secret.csv"'),
        'secret.csv');
    expect(attachmentName(null), isNull);
    expect(safeFileName('.hidden'), 'hidden');
    expect(safeFileName('a\u0000b<c>.csv'), 'abc.csv');
    expect(safeFileName(''), 'download');
    expect(safeFileName('x' * 300).length, 120);
  });

  test('a download sends the session, saves the bytes and the API name',
      () async {
    final sent = <http.BaseRequest>[];
    final saved = <(String, Uint8List, String)>[];
    final downloads = HttpApiDownloads(
      baseUri: Uri.parse('http://localhost:8000'),
      sessions: await _sessions(),
      client: MockClient((request) async {
        sent.add(request);
        return http.Response('a,b\n1,2\n', 200, headers: {
          'content-type': 'text/csv',
          'content-disposition': 'attachment; filename="contacts.csv"'
        });
      }),
      saveBytes: (name, bytes, title) async {
        saved.add((name, bytes, title));
        return true;
      },
    );
    final ok = await downloads.save('POST', '/api/v1/files/bulk/download',
        body: {
          'file_ids': ['f1']
        },
        fileName: 'fallback.zip',
        dialogTitle: 'Save');
    expect(ok, isTrue);
    expect(sent.single.headers['authorization'], 'Bearer token');
    expect(sent.single.followRedirects, isFalse);
    expect(sent.single.headers['x-workspace-id'], 'w');
    expect(sent.single.url.path, '/api/v1/files/bulk/download');
    expect(jsonDecode((sent.single as http.Request).body), {
      'file_ids': ['f1']
    });
    expect(saved.single.$1, 'contacts.csv');
    expect(utf8.decode(saved.single.$2), 'a,b\n1,2\n');
  });

  test('a refusal keeps the API sentence and saves nothing', () async {
    var saves = 0;
    final downloads = HttpApiDownloads(
      baseUri: Uri.parse('http://localhost:8000'),
      sessions: await _sessions(),
      client: MockClient((_) async => http.Response(
          jsonEncode({
            'error': {
              'code': 'quarantined',
              'message': 'report.pdf is quarantined.'
            }
          }),
          409,
          headers: {'content-type': 'application/json'})),
      saveBytes: (_, __, ___) async {
        saves++;
        return true;
      },
    );
    await expectLater(
        downloads.save('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'status', 409)
            .having(
                (e) => e.message, 'message', 'report.pdf is quarantined.')));
    expect(saves, 0);
  });

  test('a redirect is an answer, never followed with the token', () async {
    var saves = 0;
    final downloads = HttpApiDownloads(
      baseUri: Uri.parse('http://localhost:8000'),
      sessions: await _sessions(),
      client: MockClient((_) async => http.Response('', 302,
          headers: {'location': 'https://elsewhere.example/x.csv'})),
      saveBytes: (_, __, ___) async {
        saves++;
        return true;
      },
    );
    await expectLater(
        downloads.save('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 's', 302)));
    expect(saves, 0);
  });

  test('an answer larger than the phone holds is stopped', () async {
    final downloads = HttpApiDownloads(
      baseUri: Uri.parse('http://localhost:8000'),
      sessions: await _sessions(),
      maxBytes: 10,
      client: MockClient.streaming((request, _) async => http.StreamedResponse(
          Stream.fromIterable([
            List.filled(8, 1),
            List.filled(8, 1),
          ]),
          200)),
      saveBytes: (_, __, ___) async => true,
    );
    await expectLater(
        downloads.save('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'status', 413)
            .having((e) => e.message, 'message', contains('fewer files'))));
  });

  test('a network failure reads as a sentence; no session refuses', () async {
    final downloads = HttpApiDownloads(
      baseUri: Uri.parse('http://localhost:8000'),
      sessions: await _sessions(),
      client: MockClient((_) async => throw http.ClientException('reset')),
      saveBytes: (_, __, ___) async => true,
    );
    await expectLater(
        downloads.save('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>().having(
            (e) => e.message, 'message', contains('could not be reached'))));
    final none = HttpApiDownloads(
        baseUri: Uri.parse('http://localhost:8000'),
        sessions: MemorySessionStore(),
        saveBytes: (_, __, ___) async => true);
    await expectLater(none.save('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 's', 401)));
  });

  test('the API client refuses plainly without a downloader', () async {
    final api = AnumApiClient(
        baseUri: Uri.parse('http://localhost:8000'),
        transport: _NoTransport(),
        sessions: MemorySessionStore());
    expect(() => api.download('GET', '/x', fileName: 'x', dialogTitle: 't'),
        throwsA(isA<ApiException>()));
  });
}

class _NoTransport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) => throw UnimplementedError();
}
