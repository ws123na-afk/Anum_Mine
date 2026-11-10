// Downloads and exports stop waiting and say why (docs/mobile.md#file-downloads).
import 'dart:async';
import 'dart:io';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/data/transfer_guard.dart';
import 'package:anum_mobile/features/files/file_versions_models.dart';
import 'package:anum_mobile/features/vault/vault_transfer.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const unreachable =
    'The server could not be reached. Check the connection and try again.';
const timedOut =
    'The server did not answer in time. Check the connection and try again.';
const short = Duration(milliseconds: 40);
final base = Uri.parse('https://api.example/');

Future<SessionStore> signedIn() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
    accessToken: 'anum_local_example',
    tokenType: 'bearer',
    expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
    context: const TenantContext(
        tenantId: 'tenant_local',
        workspaceId: 'workspace_foundation',
        userId: 'user_owner',
        roles: ['owner']),
  ));
  return sessions;
}

/// Never answers, like a connection that hangs.
http.Client hanging() => MockClient((_) => Completer<http.Response>().future);

http.Client failing(Object error) => MockClient((_) async => throw error);

final file = WorkspaceFile(
    id: 'file_1',
    name: 'contract.pdf',
    contentType: 'application/pdf',
    sizeBytes: 10,
    createdAt: DateTime.utc(2026, 11, 1));

final version = FileVersion(
    id: 'ver_1',
    fileId: 'file_1',
    version: 1,
    contentType: 'application/pdf',
    sizeBytes: 10,
    createdBy: 'user_owner',
    createdAt: DateTime.utc(2026, 11, 1),
    replacedBy: '',
    replacedAt: DateTime.utc(2026, 11, 2),
    downloadable: true);

Matcher refusedWith(String message) => throwsA(isA<ApiException>()
    .having((e) => e.statusCode, 'status', 0)
    .having((e) => e.message, 'message', message));

void main() {
  /// Every download and export, by name, against [client].
  Future<Map<String, Future<Object?> Function()>> transfers(
      http.Client client) async {
    final sessions = await signedIn();
    final files = HttpWorkspaceFileTransfer(
        baseUri: base,
        sessions: sessions,
        client: client,
        downloadTimeout: short);
    return {
      'file preview (link, then bytes)': () => files.bytes('file_1'),
      'file download': () => files.download(file),
      'older file version': () => files.saveVersion(file, version),
      'mail and billing download': () => HttpMailFileTransfer(
              baseUri: base, sessions: sessions, files: files, client: client)
          .download('/api/v1/billing/invoices/inv_1/pdf'),
      'audit export': () => HttpAuditExporter(
              baseUri: base, sessions: sessions, client: client, timeout: short)
          .export('csv'),
      'vault document': () => HttpVaultFileSaver(
              baseUri: base, sessions: sessions, client: client, timeout: short)
          .save('doc_1', 'passport.pdf'),
    };
  }

  test('a connection that hangs ends with "did not answer in time"', () async {
    for (final entry in (await transfers(hanging())).entries) {
      await expectLater(entry.value(), refusedWith(timedOut),
          reason: entry.key);
    }
  });

  test('a broken connection never shows the raw network error', () async {
    for (final error in <Object>[
      http.ClientException('Connection closed before full header was received'),
      const SocketException('Connection reset by peer'),
      const HandshakeException('CERTIFICATE_VERIFY_FAILED'),
    ]) {
      for (final entry in (await transfers(failing(error))).entries) {
        await expectLater(entry.value(), refusedWith(unreachable),
            reason: '${entry.key}: ${error.runtimeType}');
      }
    }
  });

  test('a failed second step (the bytes behind the link) is guarded too',
      () async {
    final sessions = await signedIn();
    final files = HttpWorkspaceFileTransfer(
        baseUri: base,
        sessions: sessions,
        downloadTimeout: short,
        client: MockClient((request) async => request.method == 'POST'
            ? http.Response('{"url": "/files/signed/abc"}', 200)
            : throw const SocketException('Network is unreachable')));
    await expectLater(files.bytes('file_1'), refusedWith(unreachable));
  });

  test('an API refusal keeps the API\'s sentence', () async {
    final sessions = await signedIn();
    final files = HttpWorkspaceFileTransfer(
        baseUri: base,
        sessions: sessions,
        client: MockClient((_) async => http.Response(
            '{"error": {"code": "forbidden", "message": "Members cannot download this file."}}',
            403)));
    await expectLater(
        files.bytes('file_1'),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'status', 403)
            .having((e) => e.message, 'message',
                'Members cannot download this file.')));
  });

  test('guardTransfer returns the answer when it arrives in time', () async {
    expect(await guardTransfer(() async => 7, timeout: short), 7);
  });
}
